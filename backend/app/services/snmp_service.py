"""SNMP-based collection for network devices (H3C / Huawei / Dell / Cisco switches).

=======================================================================
 稳定性关键说明 (2026-09 修复"长时间运行后全站超时"根因)
=======================================================================
历史问题: 每次调用 snmp_get/snmp_walk 都新建一个 SnmpEngine(), 并且
walk_cmd 的异步生成器在提前 return/break 时没有被关闭。

实测证据 (tools/repro_leak.py, pysnmp 7.1.16):
    - 旧写法: 30 次调用 -> 句柄 +37, asyncio 任务 +30 (永不回收)
    - 新写法: 30 次调用 -> 句柄  +1, asyncio 任务  +1
单台交换机一轮轮询约 8~10 次 SNMP 操作; 调度器每 2 分钟轮询一次全部设备。
按 10 台交换机算, 旧写法每小时泄漏约 2700 个 fd 和 2700 个悬空 asyncio 任务,
数小时内即耗尽 Linux 默认 1024 的文件描述符上限, 导致进程仍存活但所有
HTTP 请求超时 (页面能打开、接口全挂), 必须重启服务才能恢复。

本模块现在强制遵守两条铁律:
    1. 全局复用唯一一个 SnmpEngine (不再每次 new);
    2. 所有 walk_cmd 异步生成器一律用 contextlib.aclosing 包裹, 保证关闭;
另外为每次 SNMP 操作增加了硬超时 (asyncio.wait_for) 与全局限流, 避免个别
不可达设备把一轮轮询拖到几十分钟、造成定时任务堆积。

注意: 所有 get_cmd/next_cmd/walk_cmd 必须传 lookupMib=False, 否则 pysnmp7
会尝试加载本地 MIB 库解析 OID, 离线环境无 MIB 文件将导致 walk 全部失败。
"""
import asyncio
import logging
from contextlib import aclosing
from typing import Optional

from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity, get_cmd, next_cmd, walk_cmd,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 超时与限流参数
# --------------------------------------------------------------------------
# 单次标量操作 (GET / GETNEXT) 的硬超时
SINGLE_OP_TIMEOUT = 6.0
# 单次 walk 表遍历的硬超时 (端口表较大的交换机需要更久)
WALK_TIMEOUT = 25.0
# 底层 UDP 层超时/重试 (pysnmp 自身)
UDP_TIMEOUT = 3
UDP_RETRIES = 1
# 同时进行的 SNMP 操作上限, 防止瞬间打开过多句柄
_MAX_CONCURRENT_OPS = 6
# 单台设备一次采集的总超时 (含 sysName/sysDescr/CPU/内存/端口表)
DEVICE_TIMEOUT = 75.0

_op_semaphore: Optional[asyncio.Semaphore] = None


def _sem() -> asyncio.Semaphore:
    """并发信号量 (延迟创建并绑定当前事件循环)。"""
    global _op_semaphore
    loop = asyncio.get_running_loop()
    if _op_semaphore is None or getattr(_op_semaphore, "_loop", loop) is not loop:
        _op_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_OPS)
    return _op_semaphore


# --------------------------------------------------------------------------
# 全局唯一的 SnmpEngine
# --------------------------------------------------------------------------
_ENGINE: Optional[SnmpEngine] = None
_ENGINE_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ENGINE_LOCK: Optional[asyncio.Lock] = None
_TARGETS: dict = {}


def _lock() -> asyncio.Lock:
    global _ENGINE_LOCK
    loop = asyncio.get_running_loop()
    if _ENGINE_LOCK is None or getattr(_ENGINE_LOCK, "_loop", loop) is not loop:
        _ENGINE_LOCK = asyncio.Lock()
    return _ENGINE_LOCK


async def get_engine() -> SnmpEngine:
    """获取全局共享的 SnmpEngine (首次调用时创建)。

    复用单个 engine 是 pysnmp 官方推荐做法: SnmpEngine 是重量级对象,
    内部持有 transportDispatcher 与事件循环任务, 每次新建都会泄漏。
    """
    global _ENGINE, _ENGINE_LOOP
    loop = asyncio.get_running_loop()
    if _ENGINE is None or _ENGINE_LOOP is not loop:
        async with _lock():
            if _ENGINE is None or _ENGINE_LOOP is not loop:
                await close_engine()
                _ENGINE = SnmpEngine()
                _ENGINE_LOOP = loop
                logger.info("[SNMP] 已初始化共享 SnmpEngine (复用模式)")
    return _ENGINE


async def close_engine() -> None:
    """关闭并释放 engine 持有的 socket / dispatcher (兼容不同 pysnmp 版本)。"""
    global _ENGINE, _ENGINE_LOOP
    engine, _ENGINE, _ENGINE_LOOP = _ENGINE, None, None
    _TARGETS.clear()
    if engine is None:
        return
    for closer in ("close_dispatcher", "aclose"):
        fn = getattr(engine, closer, None)
        if fn is None:
            continue
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                await res
            logger.info("[SNMP] 共享 SnmpEngine 已关闭")
            return
        except Exception as e:  # pragma: no cover - 尽力而为
            logger.debug(f"[SNMP] 关闭 engine 时 {closer} 失败: {e}")


async def _target(ip: str) -> UdpTransportTarget:
    """按 IP 缓存 transport target, 避免重复建对象。"""
    key = (ip, UDP_TIMEOUT, UDP_RETRIES)
    cached = _TARGETS.get(key)
    if cached is not None:
        return cached
    target = await UdpTransportTarget.create(
        (ip, 161), timeout=UDP_TIMEOUT, retries=UDP_RETRIES
    )
    if len(_TARGETS) > 500:      # 简单上限, 防止异常输入把缓存撑爆
        _TARGETS.clear()
    _TARGETS[key] = target
    return target


def stats() -> dict:
    """供健康检查使用的诊断信息。"""
    return {
        "engine_ready": _ENGINE is not None,
        "cached_targets": len(_TARGETS),
        "max_concurrent_ops": _MAX_CONCURRENT_OPS,
    }


# Common OIDs
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"

# ifTable / ifXTable
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"       # 端口名称
OID_IF_OPER = "1.3.6.1.2.1.2.2.1.8"        # 端口运行状态
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"       # 端口速率(bps)
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"   # 端口备注(ifAlias, 交换机上常写对端用途)

# ifOperStatus 数值 -> 中文
PORT_STATUS_MAP = {
    1: "up", 2: "down", 3: "testing",
    4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown",
}

# Vendor-specific CPU/memory OIDs — 列表按优先级尝试, 兼容不同型号/固件
VENDOR_OIDS = {
    "huawei": {
        # hwEntityCpuUsage / hwEntityMemUsage (entity-based, 新固件)
        "cpu_list": [
            "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5",   # hwEntityCpuUsage (推荐)
            "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.6",   # hwEntityCpuUsage5Sec
            "1.3.6.1.4.1.2011.10.2.1.1.10",          # 旧版S系列交换机
        ],
        "mem_list": [
            "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7",   # hwEntityMemUsage (推荐)
            "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.8",   # hwEntityMemUsage备用
        ],
    },
    "h3c": {
        # hh3cEntityExt* 表按实体索引。**关键**: 不能只用 GETNEXT 取第一个实例 ——
        # 华三实体表里第一个实例常常是恒为 1 的实体, 面板就会永远显示 1%。
        # 采集侧已改为 walk 整表 + 只接受合理的百分比(见 _pick_percent)。
        "cpu_list": [
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.6",   # hh3cEntityExtCpuUsage (Comware7 推荐)
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.4",   # 部分版本 CPU
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.3",   # 旧版 Comware5 CPU
        ],
        "mem_list": [
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.8",   # hh3cEntityExtMemUsage (Comware7 官方, 应为使用率)
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.2",   # 旧版 Comware5 内存
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.7",   # 备用(部分型号此列含义不同)
        ],
    },
    "cisco": {
        "cpu_list": [
            "1.3.6.1.4.1.9.9.109.1.1.1.1.3",   # cpmCPUTotal5secRev
            "1.3.6.1.4.1.9.9.109.1.1.1.1.8",   # cpmCPUTotal1minRev
        ],
        "mem_list": [
            "1.3.6.1.4.1.9.9.48.1.1.1.5",      # ciscoMemoryPoolUsed (需配合总内存算百分比)
        ],
    },
}

# 向后兼容: 旧代码引用 VENDOR_OIDS[vendor]["cpu"] 的地方仍可工作
for _v in VENDOR_OIDS:
    VENDOR_OIDS[_v]["cpu"] = VENDOR_OIDS[_v]["cpu_list"][0]
    VENDOR_OIDS[_v]["mem"] = VENDOR_OIDS[_v]["mem_list"][0]


def _community(community: str, version: str) -> CommunityData:
    return CommunityData(community, mpModel=1 if version == "2c" else 0)


# --------------------------------------------------------------------------
# 基础操作 (全部带硬超时 + 复用 engine + 关闭生成器)
# --------------------------------------------------------------------------
async def _do_get(ip: str, community: str, oid: str, version: str):
    return await get_cmd(
        await get_engine(),
        _community(community, version),
        await _target(ip),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lookupMib=False,
    )


async def snmp_get(ip: str, community: str, oid: str, version: str = "2c") -> Optional[str]:
    """Perform a single SNMP GET (标量OID)。"""
    try:
        async with _sem():
            error_indication, error_status, error_index, var_binds = await asyncio.wait_for(
                _do_get(ip, community, oid, version), timeout=SINGLE_OP_TIMEOUT
            )
        if error_indication or error_status:
            logger.debug(f"[SNMP] GET {ip} {oid}: error={error_indication or error_status}")
            return None
        for var_bind in var_binds:
            return str(var_bind[1])
    except asyncio.TimeoutError:
        logger.debug(f"[SNMP] GET {ip} {oid}: 超时 {SINGLE_OP_TIMEOUT}s")
    except Exception as e:
        logger.debug(f"[SNMP] GET {ip} {oid}: exception={e}")
    return None


async def _do_next(ip: str, community: str, oid: str, version: str):
    return await next_cmd(
        await get_engine(),
        _community(community, version),
        await _target(ip),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lookupMib=False,
    )


async def _walk_first(ip: str, community: str, oid: str, version: str) -> Optional[str]:
    """walk 取首条 (在 aclosing 内消费, 保证生成器被关闭)。"""
    gen = walk_cmd(
        await get_engine(),
        _community(community, version),
        await _target(ip),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
        lookupMib=False,
    )
    async with aclosing(gen):
        async for (error_indication, error_status, error_index, var_binds) in gen:
            if error_indication or error_status:
                break
            for var_bind in var_binds:
                if str(var_bind[0]).startswith(oid):
                    return str(var_bind[1])
            break
    return None


async def snmp_get_next(ip: str, community: str, oid: str, version: str = "2c") -> Optional[str]:
    """GETNEXT并返回目标OID子树下的首个值 (表类型OID用, 如CPU/内存使用率)。

    注意: pysnmp 7.x 的 next_cmd 是普通协程(返回单条结果), 不是异步生成器,
    必须用 await 直接调用, 用 async for 会抛 TypeError。
    如果 next_cmd 失败, 回退到 walk_cmd 取首条(更可靠)。
    """
    # 方式1: next_cmd (pysnmp 7.x 普通协程)
    try:
        async with _sem():
            error_indication, error_status, error_index, var_binds = await asyncio.wait_for(
                _do_next(ip, community, oid, version), timeout=SINGLE_OP_TIMEOUT
            )
        if not error_indication and not error_status:
            for var_bind in var_binds:
                if str(var_bind[0]).startswith(oid):
                    val = str(var_bind[1])
                    logger.debug(f"[SNMP] GETNEXT {ip} {oid} -> {val}")
                    return val
        else:
            logger.debug(f"[SNMP] GETNEXT {ip} {oid}: error={error_indication or error_status}")
    except asyncio.TimeoutError:
        logger.debug(f"[SNMP] GETNEXT {ip} {oid}: 超时 {SINGLE_OP_TIMEOUT}s")
    except Exception as e:
        logger.debug(f"[SNMP] GETNEXT {ip} {oid}: exception={e}")

    # 方式2: 回退到 walk_cmd 取首条 (某些设备next_cmd行为不一致)
    try:
        async with _sem():
            val = await asyncio.wait_for(
                _walk_first(ip, community, oid, version), timeout=SINGLE_OP_TIMEOUT
            )
        if val is not None:
            logger.debug(f"[SNMP] WALK-fallback {ip} {oid} -> {val}")
            return val
    except asyncio.TimeoutError:
        logger.debug(f"[SNMP] WALK-fallback {ip} {oid}: 超时")
    except Exception as e:
        logger.debug(f"[SNMP] WALK-fallback {ip} {oid}: exception={e}")

    return None


async def _get_first_value(ip: str, community: str, oid_list: list, version: str = "2c") -> Optional[str]:
    """按OID列表优先级依次尝试GETNEXT, 返回首个成功值。"""
    for oid in oid_list:
        val = await snmp_get_next(ip, community, oid, version)
        if val is not None:
            try:
                fval = float(val)
                if fval > 0:
                    return val
            except ValueError:
                return val
    return None


async def _do_walk(ip: str, community: str, oid: str, version: str) -> dict:
    """在 aclosing 保护下完整消费一次 walk。"""
    out = {}
    gen = walk_cmd(
        await get_engine(),
        _community(community, version),
        await _target(ip),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
        lookupMib=False,
    )
    async with aclosing(gen):
        async for (error_indication, error_status, error_index, var_binds) in gen:
            if error_indication or error_status:
                break
            for var_bind in var_binds:
                full_oid = str(var_bind[0])
                if full_oid.startswith(oid + "."):
                    suffix = full_oid[len(oid) + 1:]
                    out[suffix] = str(var_bind[1])
    return out


async def snmp_walk(ip: str, community: str, oid: str, version: str = "2c") -> dict:
    """Walk指定OID子树, 返回 {实例后缀: 值字符串}。

    使用官方 walk_cmd 异步生成器(内部循环GETNEXT), lexicographicMode=False
    限定在子树内; 外层超时保护, 超时会取消遍历并关闭生成器 (不泄漏)。
    """
    try:
        async with _sem():
            return await asyncio.wait_for(
                _do_walk(ip, community, oid, version), timeout=WALK_TIMEOUT
            )
    except asyncio.TimeoutError:
        logger.warning(f"[SNMP] WALK {ip} {oid}: 超时 {WALK_TIMEOUT}s, 已取消")
    except Exception as e:
        logger.debug(f"[SNMP] WALK {ip} {oid}: exception={e}")
    return {}


# --------------------------------------------------------------------------
# CPU/内存百分比: walk 整表 + 只接受合理百分比 (修复"永远显示 1%")
# --------------------------------------------------------------------------
HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"      # hrProcessorLoad (0~100)
HR_STORAGE_TYPE = "1.3.6.1.2.1.25.2.3.1.2"        # hrStorageType
HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"        # hrStorageSize
HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"        # hrStorageUsed
HR_STORAGE_RAM = "1.3.6.1.2.1.25.2.1.2"           # hrStorageRam 枚举值


async def _pick_percent(ip: str, community: str, oid_list: list,
                        version: str, label: str) -> Optional[float]:
    """遍历候选 OID 的整张表, 返回首个存在合理百分比 (0,100] 的表内最大值。

    根因: 华三 hh3cEntityExt* 表按实体索引, 旧的实现用 GETNEXT 只取第一个
    实例, 而实体表首个实例往往是某个恒为 1 的实体 -> 面板永远 1%。
    改成 walk 全表 + 过滤合理百分比, 才拿得到主控真实占用。
    """
    for oid in oid_list:
        table = await snmp_walk(ip, community, oid, version)
        vals = []
        for v in table.values():
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if 0 < f <= 100:
                vals.append(f)
        if vals:
            val = max(vals)
            logger.info(f"[SNMP] {ip} {label} <- {oid} (实例{len(table)}个, 取max={val})")
            return val
        if table:
            logger.debug(f"[SNMP] {ip} {label} {oid} 有{len(table)}个实例但无合理百分比, 试下个")
    return None


async def _hr_processor_load(ip: str, community: str, version: str = "2c") -> Optional[float]:
    """标准 HOST-RESOURCES CPU 兜底: hrProcessorLoad 取最大值(最忙的核)。"""
    table = await snmp_walk(ip, community, HR_PROCESSOR_LOAD, version)
    vals = []
    for v in table.values():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0 <= f <= 100:
            vals.append(f)
    return max(vals) if vals else None


async def _hr_storage_ram_percent(ip: str, community: str, version: str = "2c") -> Optional[float]:
    """标准 HOST-RESOURCES 内存兜底: hrStorageRam 分区的 已用/总量 百分比。"""
    types = await snmp_walk(ip, community, HR_STORAGE_TYPE, version)
    sizes = await snmp_walk(ip, community, HR_STORAGE_SIZE, version)
    useds = await snmp_walk(ip, community, HR_STORAGE_USED, version)
    for idx, t in types.items():
        if str(t).strip() == HR_STORAGE_RAM:
            try:
                size = float(sizes.get(idx, 0))
                used = float(useds.get(idx, 0))
            except (TypeError, ValueError):
                continue
            if size > 0:
                return round(used / size * 100, 1)
    return None


async def walk_ports(ip: str, community: str, version: str = "2c") -> list:
    """采集全部端口明细: 名称/状态/速率/备注(ifAlias)/是否物理口。"""
    descrs = await snmp_walk(ip, community, OID_IF_DESCR, version)
    if not descrs:
        return []
    opers = await snmp_walk(ip, community, OID_IF_OPER, version)
    speeds = await snmp_walk(ip, community, OID_IF_SPEED, version)
    aliases = await snmp_walk(ip, community, OID_IF_ALIAS, version)

    ports = []
    for suffix, name in descrs.items():
        try:
            index = int(suffix)
        except ValueError:
            continue
        oper = _safe_int(opers.get(suffix))
        ports.append({
            "port_index": index,
            "name": name,
            "status": PORT_STATUS_MAP.get(oper, "unknown"),
            "speed_mbps": _to_speed_mbps(speeds.get(suffix)),
            "alias": aliases.get(suffix, "") or "",
            "physical": 1 if is_physical_port(name) else 0,
        })
    ports.sort(key=lambda p: p["port_index"])
    return ports


async def get_port_status(ip: str, community: str, version: str, port_index: int) -> Optional[str]:
    """单端口状态测试: GET ifOperStatus.<index>。"""
    val = await snmp_get(ip, community, f"{OID_IF_OPER}.{port_index}", version)
    if val is None:
        return None
    try:
        return PORT_STATUS_MAP.get(int(val), "unknown")
    except ValueError:
        return "unknown"


def _safe_int(s: Optional[str]) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _to_speed_mbps(s: Optional[str]) -> int:
    """ifSpeed值可能是整数bps或浮点字符串, 统一转Mbps。"""
    try:
        return max(0, int(float(str(s).strip()) / 1_000_000))
    except (ValueError, TypeError):
        return 0


# 虚拟接口名前缀: 这些口不计入"端口(UP/总)"统计, 但仍在明细中展示
VIRTUAL_PORT_PREFIXES = (
    "Vlan", "Vlanif", "Vlan-interface", "LoopBack", "Loopback", "NULL", "Null",
    "InLoopBack", "Register", "Tunnel", "Aux", "Route", "Stack", "Virtual", "MEth",
)


def is_physical_port(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    return not n.startswith(VIRTUAL_PORT_PREFIXES)


def _empty_result(ip: str) -> dict:
    return {
        "ip": ip, "reachable": False, "sys_name": "", "sys_descr": "",
        "cpu_percent": 0.0, "mem_percent": 0.0, "port_total": 0,
        "port_up": 0, "ports": [],
    }


async def _collect_network_device_inner(
    ip: str, community: str, vendor: str, version: str
) -> dict:
    result = _empty_result(ip)

    # Basic reachability via sysName
    sys_name = await snmp_get(ip, community, OID_SYS_NAME, version)
    if sys_name is None:
        return result
    result["reachable"] = True
    result["sys_name"] = sys_name

    descr = await snmp_get(ip, community, OID_SYS_DESCR, version)
    result["sys_descr"] = descr or ""

    # Vendor-specific CPU/mem — 按OID列表优先级尝试多个OID
    vendor_key = vendor.lower() if vendor else ""
    if not vendor_key:
        descr_lower = (descr or "").lower()
        if "huawei" in descr_lower:
            vendor_key = "huawei"
        elif "h3c" in descr_lower or "hangzhou" in descr_lower:
            vendor_key = "h3c"
        elif "cisco" in descr_lower:
            vendor_key = "cisco"

    cpu_oid_list, mem_oid_list = [], []
    if vendor_key in VENDOR_OIDS:
        cpu_oid_list = VENDOR_OIDS[vendor_key].get("cpu_list", [VENDOR_OIDS[vendor_key]["cpu"]])
        mem_oid_list = VENDOR_OIDS[vendor_key].get("mem_list", [VENDOR_OIDS[vendor_key]["mem"]])
    else:
        logger.info(f"[SNMP] {ip} 未识别厂商(vendor={vendor}, descr={(descr or '')[:60]}), 仅用标准OID兜底")

    # CPU: 厂商私有OID(整表walk+只取合理百分比) -> 标准 hrProcessorLoad 兜底
    cpu_val = await _pick_percent(ip, community, cpu_oid_list, version, "CPU") if cpu_oid_list else None
    if cpu_val is None:
        cpu_val = await _hr_processor_load(ip, community, version)
        if cpu_val is not None:
            logger.info(f"[SNMP] {ip} CPU 使用标准 hrProcessorLoad 兜底 = {cpu_val}")
    if cpu_val is not None:
        result["cpu_percent"] = float(cpu_val)
    else:
        logger.info(f"[SNMP] {ip} ({vendor_key or '未知'}) CPU 采集失败")

    # 内存: 厂商私有OID -> 标准 hrStorageRam 兜底
    mem_val = await _pick_percent(ip, community, mem_oid_list, version, "内存") if mem_oid_list else None
    if mem_val is None:
        mem_val = await _hr_storage_ram_percent(ip, community, version)
        if mem_val is not None:
            logger.info(f"[SNMP] {ip} 内存 使用标准 hrStorageRam 兜底 = {mem_val}")
    if mem_val is not None:
        result["mem_percent"] = float(mem_val)
    else:
        logger.info(f"[SNMP] {ip} ({vendor_key or '未知'}) 内存 采集失败")

    # 端口明细 (名称/状态/速率/备注); UP/总 只统计物理口, 排除VLAN等虚拟口
    ports = await walk_ports(ip, community, version)
    result["ports"] = ports
    physical = [p for p in ports if p["physical"]]
    result["port_total"] = len(physical)
    result["port_up"] = sum(1 for p in physical if p["status"] == "up")

    return result


async def collect_network_device(
    ip: str, community: str = "public", vendor: str = "", version: str = "2c"
) -> dict:
    """Collect basic info + CPU/memory + port status from a network device.

    带整体超时: 单台设备采集最长 DEVICE_TIMEOUT 秒, 超时返回不可达结果,
    保证一轮轮询能被调度器控制在合理时长内 (避免任务堆积)。
    """
    try:
        return await asyncio.wait_for(
            _collect_network_device_inner(ip, community, vendor, version),
            timeout=DEVICE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[SNMP] 设备 {ip} 采集整体超时({DEVICE_TIMEOUT}s), 标记离线")
        return _empty_result(ip)
    except Exception as e:
        logger.error(f"[SNMP] 设备 {ip} 采集异常: {e}", exc_info=True)
        return _empty_result(ip)
