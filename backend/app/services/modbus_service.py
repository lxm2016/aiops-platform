"""Modbus TCP 采集 + 动环点位轮询

现场情况（172.16.0.238 / 192.168.204.71 这类）:
    一台串口服务器(Modbus 网关) 上挂多段 RS-485, 每段映射一个 TCP 端口,
    每段总线上再挂若干个 Modbus 从站(从站地址区分)。
    所以 "IP + 端口 + 从站地址" 三者才能定位到一台设备。

本模块只做两件事:
    1. read_point()   —— 从一个点位读出数值(含系数换算)
    2. poll_device()  —— 轮询一台设备的所有点位, 更新缓存值并写时序

**只发读功能码(01/02/03/04), 绝不发写指令**, 不会误动 UPS 开关。
"""
import asyncio
import logging
import socket
import struct
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EnvDevice, EnvPoint, EnvPointReading

logger = logging.getLogger(__name__)

FC_READ_COILS = 1
FC_READ_DISCRETE = 2
FC_READ_HOLDING = 3
FC_READ_INPUT = 4

EXC_TEXT = {
    1: "功能码不支持",
    2: "寄存器地址不存在",
    3: "读取数量超限",
    4: "设备故障",
    6: "设备忙",
}


class ModbusError(Exception):
    pass


class ModbusTCPClient:
    """同步的 Modbus TCP 客户端。调用方用 asyncio.to_thread 包一层即可。"""

    def __init__(self, host: str, port: int, slave: int = 1, timeout: float = 3.0):
        self.host, self.port, self.slave = host, port, slave
        self.timeout = timeout
        self._tid = 0
        self.sock: Optional[socket.socket] = None

    def __enter__(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except socket.timeout:
            # 连接都建不起来 —— 十有八九是网络层问题(路由不通/防火墙拦/端口写错)
            raise ModbusError(
                f"连接超时: 无法建立到 {self.host}:{self.port} 的 TCP 连接"
                f"(请在后端服务器上核实网络可达性/防火墙/端口号)")
        except OSError as e:
            raise ModbusError(
                f"连接失败: {self.host}:{self.port} ({type(e).__name__}: {e})")
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self

    def __exit__(self, *exc):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ModbusError("连接被对端关闭")
            buf += chunk
        return buf

    def read(self, fc: int, address: int, count: int = 1):
        """读寄存器, 返回原始整数列表; count=1 时返回单个整数。"""
        if self.sock is None:
            self.__enter__()
        self._tid = (self._tid + 1) & 0xFFFF
        pdu = struct.pack(">BHH", fc, address, count)
        req = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.slave) + pdu
        try:
            self.sock.sendall(req)
            head = self._recv_exact(7)
        except socket.timeout:
            # TCP 能连上但对请求不应答 —— 多半是从站地址/功能码/地址不对
            raise ModbusError(
                "响应超时: 设备已接受连接但对请求无应答"
                "(常见原因: 从站地址不对/功能码不对/设备忙)")
        _tid, _pid, length, _unit = struct.unpack(">HHHB", head)
        body = self._recv_exact(max(length - 1, 0))
        if body and body[0] & 0x80:
            code = body[1] if len(body) > 1 else 0
            raise ModbusError(f"异常码 {code}: {EXC_TEXT.get(code, '未知')}")

        nbytes = body[1]
        data = body[2:2 + nbytes]
        if fc in (FC_READ_COILS, FC_READ_DISCRETE):
            bits = []
            for byte in data:
                for i in range(8):
                    bits.append((byte >> i) & 1)
            return bits[:count] if count > 1 else bits[0]
        vals = list(struct.unpack(f">{len(data) // 2}H", data[:len(data) // 2 * 2]))
        return vals if count > 1 else vals[0]


def _crc16_modbus(data: bytes) -> int:
    """Modbus RTU 的 CRC-16 (多项式 0xA001, 初值 0xFFFF, 低字节在前)。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusRTUOverTCP:
    """Modbus RTU over TCP —— 给"透传模式"的串口服务器用。

    现场不少串口服务器配的是"透明传输/TCP Server"模式: 它把收到的字节
    **原样**转发到 485 总线。标准 Modbus TCP 帧带 MBAP 头、没有 CRC,
    RTU 设备根本不认识, 表现就是"TCP 连得上但请求全超时"。
    这时必须发裸 RTU 帧(从站号+功能码+数据+CRC16), 网关透传后设备才有应答。

    界面/建设备时 protocol 选 "modbus_rtu" 即走这条链路。
    """

    def __init__(self, host: str, port: int, slave: int = 1, timeout: float = 3.0):
        self.host, self.port, self.slave = host, port, slave
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

    def __enter__(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except socket.timeout:
            raise ModbusError(
                f"连接超时: 无法建立到 {self.host}:{self.port} 的 TCP 连接"
                f"(请在后端服务器上核实网络可达性/防火墙/端口号)")
        except OSError as e:
            raise ModbusError(
                f"连接失败: {self.host}:{self.port} ({type(e).__name__}: {e})")
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self

    def __exit__(self, *exc):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ModbusError("连接被对端关闭")
            buf += chunk
        return buf

    def read(self, fc: int, address: int, count: int = 1):
        """发裸 RTU 帧读寄存器, 返回值与 ModbusTCPClient.read 对齐。"""
        if self.sock is None:
            self.__enter__()
        pdu = bytes([self.slave & 0xFF, fc]) + struct.pack(">HH", address, count)
        req = pdu + struct.pack("<H", _crc16_modbus(pdu))
        try:
            self.sock.sendall(req)
            head = self._recv_exact(3)      # 从站号, 功能码, 字节数/异常码
        except socket.timeout:
            raise ModbusError(
                "响应超时: 网关已接受连接但对 RTU 请求无应答"
                "(常见原因: 网关串口参数与设备不符/总线上无此从站/485 接线问题)")
        slave, rfc, third = head
        if rfc & 0x80:                       # 异常帧: +1 字节异常码(已在 third) +2 CRC
            self._recv_exact(2)
            raise ModbusError(f"异常码 {third}: {EXC_TEXT.get(third, '未知')}")
        data = self._recv_exact(third) if third > 0 else b""
        self._recv_exact(2)                  # CRC
        if slave != (self.slave & 0xFF):
            raise ModbusError(f"响应从站号不匹配(请求 {self.slave}, 应答 {slave})")

        if fc in (FC_READ_COILS, FC_READ_DISCRETE):
            bits = []
            for byte in data:
                for i in range(8):
                    bits.append((byte >> i) & 1)
            return bits[:count] if count > 1 else bits[0]
        vals = list(struct.unpack(f">{len(data) // 2}H", data[:len(data) // 2 * 2]))
        return vals if count > 1 else vals[0]


def make_client(device: EnvDevice):
    """按设备的 protocol 选传输层。

    modbus_rtu  -> 裸 RTU 帧(透传模式的串口服务器)
    其余(默认) -> 标准 Modbus TCP
    """
    cls = ModbusRTUOverTCP if (getattr(device, "protocol", "") or "") == "modbus_rtu" \
        else ModbusTCPClient
    return cls(device.ip, device.port, device.slave_id or 1)


# ---------------------------------------------------------------------------
# 点位取值
# ---------------------------------------------------------------------------
def _apply_type(raw_vals, data_type: str, bit_index: int) -> float:
    """把寄存器原始值按数据类型换算成整数/浮点。"""
    dt = (data_type or "u16").lower()
    if dt == "bit":
        return 1.0 if (int(raw_vals) >> (bit_index or 0)) & 1 else 0.0
    if dt == "u16":
        return float(raw_vals & 0xFFFF)
    if dt == "s16":
        v = raw_vals & 0xFFFF
        return float(v - 0x10000 if v >= 0x8000 else v)
    if dt in ("u32", "s32"):
        # 32 位按"高字在前"处理(大端), 这是绝大多数设备的默认
        hi, lo = (raw_vals >> 16) & 0xFFFF, raw_vals & 0xFFFF
        v = (hi << 16) | lo
        if dt == "s32" and v >= 0x80000000:
            v -= 0x100000000
        return float(v)
    return float(raw_vals)


def read_point(client: ModbusTCPClient, point: EnvPoint) -> tuple:
    """读一个点位, 返回 (数值, 原始值)。"""
    fc = point.fc or FC_READ_HOLDING
    count = 2 if (point.data_type or "").lower() in ("u32", "s32") else 1
    raw = client.read(fc, point.address or 0, count)
    if count > 1 and isinstance(raw, list):
        raw = (raw[0] << 16) | raw[1]
    value = _apply_type(raw, point.data_type, point.bit_index)
    value = value * (point.scale if point.scale is not None else 1.0) \
        + (point.offset or 0.0)
    return round(value, 3), int(raw)


def _point_alarm(point: EnvPoint, value: Optional[float]) -> bool:
    """点位当前是否处于异常态(烟感/水浸这类 0=正常 1=报警)。"""
    if value is None or point.alarm_value is None:
        return False
    return abs(float(value) - float(point.alarm_value)) < 1e-6


def _to_metric(value: float, point: EnvPoint) -> float:
    """转成告警引擎认识的值。

    开关量点位(烟感/水浸)在引擎里按"0=正常, 1=异常"处理, 这样
    规则可以统一写成 "≥1 告警", 不用为每个设备单独配。
    """
    if point.alarm_value is not None:
        return 1.0 if _point_alarm(point, value) else 0.0
    return float(value)


# ---------------------------------------------------------------------------
# 设备轮询
# ---------------------------------------------------------------------------
async def poll_device(db: AsyncSession, device: EnvDevice,
                      write_history: bool = True) -> dict:
    """轮询一台设备的所有点位。返回 {"ok": n, "fail": n, "error": str}。"""
    points = (await db.execute(
        select(EnvPoint).where(EnvPoint.device_id == device.id)
        .order_by(EnvPoint.sort, EnvPoint.id)
    )).scalars().all()
    active = [p for p in points if p.enabled]

    res = {"device": device.name, "ok": 0, "fail": 0, "error": "", "values": []}
    if not active:
        res["error"] = "没有启用的点位"
        return res

    def _do() -> list:
        """在子线程里跑: 一次连接把所有点位读完, 省掉反复握手。"""
        out = []
        with make_client(device) as client:
            for p in active:
                try:
                    value, raw = read_point(client, p)
                    out.append((p.id, value, raw, True, ""))
                except Exception as e:
                    out.append((p.id, None, None, False, f"{type(e).__name__}: {e}"))
        return out

    try:
        rows = await asyncio.wait_for(asyncio.to_thread(_do), timeout=25)
    except Exception as e:
        device.status = "offline"
        device.last_error = f"{type(e).__name__}: {e}"[:250]
        res["fail"] = len(active)
        res["error"] = device.last_error
        logger.warning(f"[动环] {device.name}({device.ip}:{device.port}) 轮询失败: {e}")
        # 设备整体连不上 -> 点位全部标记失败, 前端能看出来
        for p in active:
            p.ok = False
        await db.flush()
        return res

    now = datetime.utcnow()
    for pid, value, raw, ok, err in rows:
        p = next((x for x in active if x.id == pid), None)
        if p is None:
            continue
        p.ok = ok
        p.updated_at = now
        if ok:
            p.value = value
            p.raw = raw
            res["ok"] += 1
            res["values"].append({"point": p.name, "value": value, "unit": p.unit})
            if write_history:
                db.add(EnvPointReading(point_id=p.id, value=value, collected_at=now))
        else:
            res["fail"] += 1
            if not res["error"]:
                res["error"] = err

    any_ok = res["ok"] > 0
    device.status = "online" if any_ok else "offline"
    if any_ok:
        device.last_seen = now
        device.last_error = ""
    else:
        device.last_error = (res["error"] or "全部点位读取失败")[:250]
    await db.flush()
    return res


async def poll_all_devices(db: AsyncSession) -> list:
    """轮询所有启用的动环设备。"""
    devices = (await db.execute(
        select(EnvDevice).where(EnvDevice.enabled == True)  # noqa: E712
    )).scalars().all()
    out = []
    for d in devices:
        if not d.poll_interval:
            continue
        out.append(await poll_device(db, d))
    return out


# ---------------------------------------------------------------------------
# 把点位值喂给告警引擎
# ---------------------------------------------------------------------------
async def evaluate_device_points(db: AsyncSession, device: EnvDevice,
                                 values: list) -> dict:
    """把本次读到的点位值送进告警引擎(阈值/通知复用现有告警模块)。

    点位的 key 为空时跳过 —— 没起名字的点位无法配规则。
    返回 {"evaluated": 送评估的点位数, "alerted": 真正命中阈值产生告警的点位数}。
    """
    from app.services import alert_engine

    # 规则和阈值都挂在 category="env" 上(动环共用一套), 但通知里要区分
    # 消防/漏水/供电 —— 靠 cat_label 覆盖显示名, 不改规则匹配的类别。
    cat_label = alert_engine.ENV_CATEGORY_LABELS.get(device.category, "动环")

    evaluated = alerted = 0
    for item in values:
        p = item.get("point_obj")
        if p is None or not p.key:
            continue
        value = item.get("value")
        if value is None:
            continue
        evaluated += 1
        alert = await alert_engine.evaluate_metric(
            db, "env", device.name, p.key, _to_metric(float(value), p),
            extra={"ip": device.ip, "cat_label": cat_label},
        )
        if alert is not None:
            alerted += 1
    return {"evaluated": evaluated, "alerted": alerted}
