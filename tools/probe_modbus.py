# -*- coding: utf-8 -*-
"""Modbus TCP 点位探测器 —— 动环设备(温湿度/烟感/水浸/UPS)接入第一步

**零依赖**: 只用 Python 标准库, 拷到任意机器直接跑。

------------------------------------------------------------------
为什么需要它
------------------------------------------------------------------
原动环平台里只写了 "IP + 端口 + Modbus地址", 但**没写寄存器映射** ——
也就是不知道"第几个寄存器是温度"。这个脚本连上去把寄存器全读一遍,
有值的列出来, 一眼就能认出:

    0x0000 = 634  ->  634/10 = 63.4 %   多半是湿度  ← 现场实测: 0=湿度
    0x0001 = 256  ->  256/10 = 25.6 ℃   多半是温度  ← 现场实测: 1=温度
    0x0000 = 0    ->  开关量 0/1       烟感/水浸的"正常/报警"

    注意! 顺序是**反直觉**的: 0=湿度、1=温度。早年本工具写反过(0=温度),
    导致把 60% 的湿度配成 60℃ 显示。判断窍门: 温度几小时几乎不动,
    湿度会明显波动 —— 隔一段时间读两次, 会涨的那一列才是湿度。

------------------------------------------------------------------
用法
------------------------------------------------------------------
  # 扫一台: IP:端口:从站地址
  python3 probe_modbus.py --dev 172.16.0.238:5305:1

  # 一次扫多台(你截图里那 4 台 + 常见组合)
  python3 probe_modbus.py --dev 192.168.204.71:5001:1 \
                             192.168.204.71:5002:9 \
                             172.16.0.238:5302:1 \
                             172.16.0.238:5305:1

  # 不知道从站地址? 自动扫 1~16
  python3 probe_modbus.py --dev 172.16.0.238:5305 --scan-slave

  # 寄存器不止 32 个时, 扩大范围
  python3 probe_modbus.py --dev 172.16.0.238:5305:1 --count 64

  # 只看某一类寄存器(掉电保护/干扰排查时有用)
  python3 probe_modbus.py --dev 172.16.0.238:5305:1 --only holding

------------------------------------------------------------------
安全说明
------------------------------------------------------------------
**只用读功能码 (01/02/03/04), 绝不发写指令 (05/06/15/16)。**
所以无论怎么跑都不会改设备配置、不会误操作 UPS 开关机。
"""
import argparse
import socket
import struct
import sys
import time

# ---------------------------------------------------------------------------
# Modbus TCP 帧: MBAP(7字节) + PDU
#   MBAP = 事务号(2) 协议号(2,固定0) 长度(2) 单元号(1)
#   PDU  = 功能码(1) 起始地址(2) 数量(2)
# ---------------------------------------------------------------------------
FC_READ_COILS = 0x01
FC_READ_DISCRETE = 0x02
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04

FC_NAMES = {
    FC_READ_COILS: "线圈      (Coil, 可读写开关量) 0xxxx",
    FC_READ_DISCRETE: "离散输入  (Discrete Input, 只读开关量) 1xxxx",
    FC_READ_HOLDING: "保持寄存器(Holding Register) 4xxxx",
    FC_READ_INPUT: "输入寄存器(Input Register)  3xxxx",
}

# 常见异常码
EXC = {
    1: "功能码不支持(设备不认这个寄存器区)",
    2: "寄存器地址不存在",
    3: "数量超限",
    4: "设备故障",
    6: "设备忙",
}


class ModbusError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"异常码 {code}: {EXC.get(code, '未知')}")


def _read_exact(sock, n, timeout):
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("连接被对端关闭")
        buf += chunk
    return buf


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusTCP:
    """一个 TCP 连接对应一个 Modbus 网关端口(一段 RS-485 总线)。

    rtu=False: 标准 Modbus TCP 帧(带 MBAP 头) —— 网关是"Modbus TCP 模式"时用。
    rtu=True : 裸 RTU 帧(带 CRC16, 无 MBAP) —— 网关是"透传/TCP Server 模式"时用。
               判别方法: TCP 连得上但 Modbus TCP 帧全超时, 就是透传模式。
    """

    def __init__(self, host, port, slave=1, timeout=3.0, rtu=False):
        self.host, self.port = host, port
        self.slave = slave
        self.timeout = timeout
        self.rtu = rtu
        self._tid = 0
        self.sock = None

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *a):
        self.close()

    def _request(self, fc, addr, count, slave=None):
        if self.sock is None:
            self.connect()
        unit = self.slave if slave is None else slave
        if self.rtu:
            pdu = bytes([unit & 0xFF, fc]) + struct.pack(">HH", addr, count)
            self.sock.sendall(pdu + struct.pack("<H", _crc16(pdu)))
            head = _read_exact(self.sock, 3, self.timeout)   # 从站, 功能码, 字节数/异常码
            _unit, rfc, third = head
            if rfc & 0x80:
                _read_exact(self.sock, 2, self.timeout)      # 异常帧 CRC
                raise ModbusError(third)
            body = _read_exact(self.sock, third, self.timeout)
            _read_exact(self.sock, 2, self.timeout)          # 正常帧 CRC
            # 与 TCP 模式返回格式对齐: [功能码, 字节数] + 数据
            return bytes([rfc, third]) + body
        self._tid = (self._tid + 1) & 0xFFFF
        pdu = struct.pack(">BHH", fc, addr, count)
        frame = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, unit) + pdu
        self.sock.sendall(frame)

        head = _read_exact(self.sock, 7, self.timeout)
        _tid, _pid, length, _unit = struct.unpack(">HHHB", head)
        body = _read_exact(self.sock, max(length - 1, 0), self.timeout)
        if not body:
            raise ConnectionError("响应为空")
        if body[0] & 0x80:
            raise ModbusError(body[1] if len(body) > 1 else 0)
        return body

    # ------------------------------------------------------------------
    # 原始帧诊断 —— 排查"幽灵从站/数值可疑"的杀手锏
    # ------------------------------------------------------------------
    def raw_exchange(self, fc, addr, count, slave=None, settle=1.0):
        """发一帧, 把线上回来的**所有字节**原样捞回来(不再按协议裁剪)。

        为什么需要: 只看解析后的数值会被解析逻辑"圆过去"。现场遇到过
        多个从站返回同一个可疑值(如 259=0x0103, 正好是请求帧 01 03 的字节),
        必须看原始字节才能判断应答究竟来自设备, 还是来自网关回显/残留缓冲。
        """
        if self.sock is None:
            self.connect()
        unit = self.slave if slave is None else slave
        if self.rtu:
            pdu = bytes([unit & 0xFF, fc]) + struct.pack(">HH", addr, count)
            req = pdu + struct.pack("<H", _crc16(pdu))
        else:
            self._tid = (self._tid + 1) & 0xFFFF
            pdu = struct.pack(">BHH", fc, addr, count)
            req = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, unit) + pdu
        self.sock.sendall(req)
        self.sock.settimeout(settle)
        buf = b""
        try:
            while len(buf) < 512:
                chunk = self.sock.recv(256)
                if not chunk:
                    break
                buf += chunk
        except (socket.timeout, TimeoutError):
            pass
        return req, buf

    def read(self, fc, addr, count, slave=None):
        """返回寄存器值列表。"""
        body = self._request(fc, addr, count, slave)
        if fc in (FC_READ_COILS, FC_READ_DISCRETE):
            nbytes = body[1]
            data = body[2:2 + nbytes]
            bits = []
            for byte in data:
                for i in range(8):
                    bits.append((byte >> i) & 1)
            return bits[:count]
        nbytes = body[1]
        data = body[2:2 + nbytes]
        return list(struct.unpack(f">{nbytes // 2}H", data[:nbytes // 2 * 2]))


def u16_to_s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def explain_frame(req, resp, rtu, fc, count):
    """把一帧原始字节翻译成人话, 并把"不自洽"的地方点出来。返回行列表。

    重点看三件事:
      ① 应答里的从站字节 == 请求里的从站字节吗? 不等 → 回话的不是你问的那台。
      ② 字节数/帧长/CRC 自洽吗? 不 CRC → 线路干扰或波特率不对。
      ③ 有没有尾随字节? 有 → 上一帧残留, 会让后续解析整体错位。
    """
    L = []
    L.append(f"    请求({len(req):>3}B): {req.hex(' ').upper()}")
    if not resp:
        L.append("    应答: <空> —— 设备一个字节都没回")
        return L
    L.append(f"    应答({len(resp):>3}B): {resp.hex(' ').upper()}")

    if not rtu:  # ---- 标准 Modbus TCP ----
        if len(resp) < 8:
            L.append(f"    ✗ 应答不足 8 字节({len(resp)}), 不是合法 MBAP 帧")
            return L
        _tid, _pid, length, unit = struct.unpack(">HHHB", resp[:7])
        L.append(f"    MBAP: 事务号={_tid} 长度={length} 单元号={unit} (请求单元号={req[6]})")
        L.append(f"    {'✓ 单元号一致' if unit == req[6] else '✗ 单元号不一致 ← 回话的不是你问的那台!'}")
        body = resp[7:]
        if not body:
            L.append("    ✗ 无 PDU")
            return L
        rfc = body[0]
        if rfc & 0x80:
            L.append(f"    功能码=0x{rfc:02X} → 异常帧, 异常码={body[1] if len(body) > 1 else '?'}")
            return L
        L.append(f"    功能码=0x{rfc:02X} {'✓' if rfc == fc else '✗ 与请求的 0x%02X 不符' % fc}")
        return L

    # ---- RTU 透传 ----
    if len(resp) < 3:
        L.append(f"    ✗ 应答不足 3 字节({len(resp)}), 不是合法 RTU 帧")
        return L
    rs, rfc, third = resp[0], resp[1], resp[2]
    same = "✓ 一致" if rs == req[0] else "✗ 不一致 ← 关键!"
    L.append(f"    从站: 应答={rs}  请求={req[0]}   {same}")
    if rs != req[0]:
        L.append("      → 回话的不是你问的那台。常见原因: 总线上有设备应答所有地址, "
                 "或网关把请求透传给了另一台。")
    if rfc & 0x80:
        L.append(f"    功能码=0x{rfc:02X} → 异常帧, 异常码={third}: {EXC.get(third, '未知')}")
        return L
    ok = "✓" if rfc == fc else "✗"
    L.append(f"    功能码: 0x{rfc:02X} (请求 0x{fc:02X}) {ok}"
             + ("" if rfc == fc else "  ← 设备连功能码都不区分, 高度疑似罐装应答/回显"))
    expect = (count + 7) // 8 if fc in (1, 2) else count * 2
    L.append(f"    字节数: {third} (期望 {expect}) {'✓' if third == expect else '✗ 不符'}")
    need = 3 + third + 2
    L.append(f"    帧长: 应为 {need}B, 实收 {len(resp)}B {'✓' if len(resp) == need else '✗ 不符'}")
    if len(resp) > need:
        L.append(f"    ⚠ 多出 {len(resp) - need}B 尾随字节: "
                 f"{resp[need:].hex(' ').upper()} —— 上一帧残留/回显, 会让后续解析整体错位!")
    if len(resp) >= need:
        crc_got = struct.unpack("<H", resp[need - 2:need])[0]
        crc_calc = _crc16(resp[:need - 2])
        L.append(f"    CRC: 收到=0x{crc_got:04X} 计算=0x{crc_calc:04X} "
                 f"{'✓' if crc_got == crc_calc else '✗ 校验失败 —— 线路干扰或波特率/校验位不对'}")
        data = resp[3:3 + third]
        if fc in (3, 4) and third >= 2:
            vals = struct.unpack(f">{third // 2}H", data[:third // 2 * 2])
            nz = [(i, v) for i, v in enumerate(vals) if v]
            L.append(f"    寄存器: {list(vals)}")
            L.append(f"    非零  : {nz if nz else '无'}")
    return L


def guess_meaning(addr, fc, raw):
    """根据常见动环设备习惯, 猜这个点位可能是什么。只是提示, 不保证。

    温湿度传感器 —— 现场实测(南院区多台)顺序是**反**的, 务必注意:
      保持寄存器 0x0000 = 湿度 ×10, 0x0001 = 温度 ×10
    不少资料写"0=温度", 按那个配会把 60% 的湿度显示成 60℃。
    UPS(科士达等)则常把电压/电流/容量放在 0x0008 之后。
    """
    if fc in (FC_READ_COILS, FC_READ_DISCRETE):
        return "开关量: 0=正常/断开  1=报警/闭合" + ("  ← 此刻为报警" if raw else "")
    if fc == FC_READ_INPUT:
        return ""

    tips = []
    dec = raw / 10.0
    # 温湿度: 现场实测顺序为 0=湿度、1=温度(不是常见的 0=温度!)
    if addr == 0 and 0 < dec <= 100:
        tips.append(f"×0.1 → {dec:.1f} %（温湿度探头现场实测: 0x0000=湿度）")
    elif addr == 1 and 0 < dec <= 120:
        tips.append(f"×0.1 → {dec:.1f} ℃（温湿度探头现场实测: 0x0001=温度）")
    elif 0 < dec <= 120 and addr in (2, 3):
        tips.append(f"×0.1 → {dec:.1f}（第 {addr + 1} 路模拟量）")

    # 状态字: 小整数且出现在靠后的地址
    if raw in (0, 1, 2) and addr >= 4:
        tips.append("可能是状态字 0=正常 1=报警/异常 2=故障")

    # 电压/电流/容量: 通常排在 0x0008 之后
    if addr >= 8:
        if 180 <= raw <= 260:
            tips.append(f"可能是电压 {raw} V")
        elif 0 < raw <= 100 and addr in (8, 10):
            tips.append(f"可能是频率/电流 {raw}（×0.1 → {dec:.1f}）")

    if raw >= 0x8000:
        tips.append(f"负值(有符号 {u16_to_s16(raw)})，可能是功率/温差")
    return "；".join(tips)


def probe_device(host, port, slave, count, only, timeout, scan_slave=False, rtu=False,
                 start=0):
    print(f"\n{'=' * 74}")
    mode = "RTU over TCP(透传)" if rtu else "Modbus TCP"
    print(f"设备 {host}:{port}   从站地址 {slave}   传输 {mode}   起始地址 {start}")
    print("=" * 74)

    # 从站地址未知时, 先扫一遍 1~16 (用起始地址探测, 科士达UPS这类寄存器在 30000+ 的
    # 设备, 0 地址没数据, 扫从站也必须从 start 探)
    if scan_slave:
        ok_slaves = []
        for sid in range(1, 17):
            try:
                with ModbusTCP(host, port, sid, timeout, rtu=rtu) as m:
                    m.read(FC_READ_HOLDING, start, 1)
                    ok_slaves.append(sid)
            except Exception:
                continue
        if ok_slaves:
            print(f"  自动扫描到可用从站地址: {ok_slaves}")
            slave = ok_slaves[0]
        else:
            print("  1~16 都没响应。检查: ①网关的从站地址是不是 17 以上 "
                  "②该端口对应哪段 485 总线 ③串口参数(波特率/校验位)对不对")
            return []

    plans = [
        (FC_READ_HOLDING, "保持寄存器", start),
        (FC_READ_INPUT, "输入寄存器", start),
        (FC_READ_COILS, "线圈", start),
        (FC_READ_DISCRETE, "离散输入", start),
    ]
    want = {"holding": FC_READ_HOLDING, "input": FC_READ_INPUT,
            "coil": FC_READ_COILS, "discrete": FC_READ_DISCRETE}
    if only:
        plans = [p for p in plans if p[0] == want[only]]

    found = []
    try:
        with ModbusTCP(host, port, slave, timeout, rtu=rtu) as m:
            for fc, label, start in plans:
                print(f"\n  [{label}] 功能码 0x{fc:02X}")
                try:
                    # 一次读太多有些网关会拒, 分批 16 个
                    vals, step = [], 16
                    for off in range(0, count, step):
                        n = min(step, count - off)
                        vals += m.read(fc, start + off, n)
                except ModbusError as e:
                    print(f"    - 跳过: {e}")
                    continue
                except Exception as e:
                    print(f"    - 读取失败: {type(e).__name__}: {e}")
                    continue

                hit = [(start + i, v) for i, v in enumerate(vals) if v != 0]
                if not hit:
                    print(f"    - 地址 {start}~{start + count - 1} 全为 0"
                          "（没接设备, 或点位在别的地址段）")
                    continue
                print(f"    {'地址':<8}{'原始值':<10}{'有符号':<10}{'×0.1':<10}说明")
                for addr, raw in hit:
                    s16 = u16_to_s16(raw) if fc in (FC_READ_HOLDING, FC_READ_INPUT) else raw
                    dec = f"{raw / 10:.1f}"
                    tip = guess_meaning(addr, fc, raw)
                    print(f"    {addr:<8}{raw:<10}{s16:<10}{dec:<10}{tip}")
                    found.append((label, addr, raw))
    except Exception as e:
        print(f"  ✗ 连不上 {host}:{port} —— {type(e).__name__}: {e}")
        print("    排查: ping 通不通 / 防火墙放通 / 端口是不是被原平台占用了")
        return []

    if not found:
        print("\n  ⚠ 所有寄存器读出来都是 0。常见原因:")
        print("    1) 该端口对应的 485 总线上没接这台设备(端口和设备的对应关系反了);")
        print("    2) 从站地址不对 -> 加 --scan-slave 自动试;")
        print("    3) 网关的串口参数(波特率/数据位/校验)与设备不匹配;")
        print("    4) 点位在更后面的地址 -> 加 --count 128;")
        print("    5) 协议地址在 30000+ (科士达UPS等) -> 加 --start 30000 --count 64")
    return found


def main():
    ap = argparse.ArgumentParser(description="Modbus TCP 点位探测器")
    ap.add_argument("--dev", nargs="+", required=True,
                    help="设备, 格式 IP:端口[:从站地址], 可给多个")
    ap.add_argument("--count", type=int, default=32, help="扫描多少个寄存器, 默认 32")
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0,
                    help="起始寄存器地址, 默认 0。科士达UPS等协议地址在 30000+ 的"
                         "设备用 --start 30000 --count 64 扫")
    ap.add_argument("--only", choices=["holding", "input", "coil", "discrete"],
                    help="只扫某一类寄存器")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--rtu", action="store_true",
                    help="用裸 RTU 帧(带CRC16)探测。TCP 连得上但 Modbus TCP 帧"
                         "全超时 = 网关在透传模式, 加这个再试")
    ap.add_argument("--scan-slave", action="store_true",
                    help="自动扫 1~16 找从站地址")
    args = ap.parse_args()

    print("Modbus TCP 点位探测（只读, 不发任何写指令, 不会改动设备）")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    summary = []
    for spec in args.dev:
        parts = spec.split(":")
        if len(parts) == 2:
            host, port = parts[0], int(parts[1])
            slave = 1
        elif len(parts) >= 3:
            host, port, slave = parts[0], int(parts[1]), int(parts[2])
        else:
            print(f"  ✗ 格式不对: {spec}  (要写 IP:端口:从站地址)")
            continue
        got = probe_device(host, port, slave, args.count, args.only,
                           args.timeout, args.scan_slave, args.rtu, args.start)
        summary.append((spec, len(got)))

    print(f"\n{'=' * 74}")
    print("汇总")
    print("=" * 74)
    for spec, n in summary:
        flag = "✓" if n else "✗"
        print(f"  {flag} {spec:<32} 读到 {n} 个非零点位")

    if any(n for _, n in summary):
        print("\n下一步: 把上面『有值』的地址和数值发给我, 我帮你配上点位映射:")
        print("  · 湿度 63.4 %  →  地址 0x0000, 系数 0.1")
        print("  · 温度 25.6 ℃  →  地址 0x0001, 系数 0.1 (数据类型选 s16, 零下才准)")
        print("  · 烟感/水浸    →  地址 0x0000, 0=正常 1=报警")
        print("  ⚠ 温湿度顺序是 0=湿度/1=温度(现场实测), 别按习惯写成 0=温度!")
        print("\n配好后平台会按间隔自动轮询, 超阈值直接走你已经配好的")
        print("钉钉/短信/电话告警通道。")


if __name__ == "__main__":
    main()
