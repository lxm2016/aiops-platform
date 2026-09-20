"""SNMP-based collection for network devices (H3C / Huawei / Dell / Cisco switches).

注意: 所有 get_cmd/next_cmd/walk_cmd 必须传 lookupMib=False, 否则 pysnmp7 会尝试
加载本地 MIB 库解析 OID, 离线环境无 MIB 文件将导致 walk 全部失败。
"""
import logging
from typing import Optional

from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity, get_cmd, next_cmd, walk_cmd,
)

logger = logging.getLogger(__name__)

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
        # hh3cEntityExtCpuUsage / hh3cEntityExtMemUsage (entity-based)
        "cpu_list": [
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.6",   # hh3cEntityExtCpuUsage (推荐)
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.3",   # 旧版Comware5 CPU
        ],
        "mem_list": [
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.7",   # hh3cEntityExtMemUsage (正确OID, 原来误用.8)
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.2",   # 旧版Comware5 内存
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


async def _target(ip: str) -> UdpTransportTarget:
    return await UdpTransportTarget.create((ip, 161), timeout=5, retries=1)


async def snmp_get(ip: str, community: str, oid: str, version: str = "2c") -> Optional[str]:
    """Perform a single SNMP GET (标量OID)。"""
    try:
        error_indication, error_status, error_index, var_binds = await get_cmd(
            SnmpEngine(),
            _community(community, version),
            await _target(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lookupMib=False,
        )
        if error_indication or error_status:
            logger.debug(f"[SNMP] GET {ip} {oid}: error={error_indication or error_status}")
            return None
        for var_bind in var_binds:
            return str(var_bind[1])
    except Exception as e:
        logger.debug(f"[SNMP] GET {ip} {oid}: exception={e}")
        return None
    return None


async def snmp_get_next(ip: str, community: str, oid: str, version: str = "2c") -> Optional[str]:
    """GETNEXT并返回目标OID子树下的首个值 (表类型OID用, 如CPU/内存使用率)。

    注意: pysnmp 7.x 的 next_cmd 是普通协程(返回单条结果), 不是异步生成器,
    必须用 await 直接调用, 用 async for 会抛 TypeError。
    如果 next_cmd 失败, 回退到 walk_cmd 取首条(更可靠)。
    """
    # 方式1: next_cmd (pysnmp 7.x 普通协程)
    try:
        error_indication, error_status, error_index, var_binds = await next_cmd(
            SnmpEngine(),
            _community(community, version),
            await _target(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lookupMib=False,
        )
        if not error_indication and not error_status:
            for var_bind in var_binds:
                if str(var_bind[0]).startswith(oid):
                    val = str(var_bind[1])
                    logger.debug(f"[SNMP] GETNEXT {ip} {oid} -> {val}")
                    return val
        else:
            logger.debug(f"[SNMP] GETNEXT {ip} {oid}: error={error_indication or error_status}")
    except Exception as e:
        logger.debug(f"[SNMP] GETNEXT {ip} {oid}: exception={e}")

    # 方式2: 回退到 walk_cmd 取首条 (某些设备next_cmd行为不一致)
    try:
        async for (error_indication, error_status, error_index, var_binds) in walk_cmd(
            SnmpEngine(),
            _community(community, version),
            await _target(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
            lookupMib=False,
        ):
            if error_indication or error_status:
                break
            for var_bind in var_binds:
                if str(var_bind[0]).startswith(oid):
                    val = str(var_bind[1])
                    logger.debug(f"[SNMP] WALK-fallback {ip} {oid} -> {val}")
                    return val
            break  # 只取首条
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


async def snmp_walk(ip: str, community: str, oid: str, version: str = "2c") -> dict:
    """Walk指定OID子树, 返回 {实例后缀: 值字符串}。

    使用官方 walk_cmd 异步生成器(内部循环GETNEXT), lexicographicMode=False
    限定在子树内; 不能对 next_cmd 用 async for (7.x 中它不是生成器)。
    """
    out = {}
    try:
        async for (error_indication, error_status, error_index, var_binds) in walk_cmd(
            SnmpEngine(),
            _community(community, version),
            await _target(ip),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
            lookupMib=False,
        ):
            if error_indication or error_status:
                break
            for var_bind in var_binds:
                full_oid = str(var_bind[0])
                if full_oid.startswith(oid + "."):
                    suffix = full_oid[len(oid) + 1:]
                    out[suffix] = str(var_bind[1])
    except Exception as e:
        logger.debug(f"[SNMP] WALK {ip} {oid}: exception={e}")
    return out


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


async def collect_network_device(
    ip: str, community: str = "public", vendor: str = "", version: str = "2c"
) -> dict:
    """Collect basic info + CPU/memory + port status from a network device."""
    result = {
        "ip": ip,
        "reachable": False,
        "sys_name": "",
        "sys_descr": "",
        "cpu_percent": 0.0,
        "mem_percent": 0.0,
        "port_total": 0,
        "port_up": 0,
        "ports": [],
    }

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

    if vendor_key in VENDOR_OIDS:
        cpu_oid_list = VENDOR_OIDS[vendor_key].get("cpu_list", [VENDOR_OIDS[vendor_key]["cpu"]])
        mem_oid_list = VENDOR_OIDS[vendor_key].get("mem_list", [VENDOR_OIDS[vendor_key]["mem"]])

        cpu = await _get_first_value(ip, community, cpu_oid_list, version)
        mem = await _get_first_value(ip, community, mem_oid_list, version)

        if cpu:
            try:
                result["cpu_percent"] = float(cpu)
            except ValueError:
                logger.warning(f"[SNMP] {ip} CPU值无法转为float: {cpu}")
        else:
            logger.info(f"[SNMP] {ip} ({vendor_key}) CPU采集失败, 尝试了{len(cpu_oid_list)}个OID")

        if mem:
            try:
                result["mem_percent"] = float(mem)
            except ValueError:
                logger.warning(f"[SNMP] {ip} 内存值无法转为float: {mem}")
        else:
            logger.info(f"[SNMP] {ip} ({vendor_key}) 内存采集失败, 尝试了{len(mem_oid_list)}个OID")
    else:
        logger.info(f"[SNMP] {ip} 未识别厂商(vendor={vendor}, descr={descr[:60] if descr else ''}), 跳过CPU/内存采集")

    # 端口明细 (名称/状态/速率/备注); UP/总 只统计物理口, 排除VLAN等虚拟口
    ports = await walk_ports(ip, community, version)
    result["ports"] = ports
    physical = [p for p in ports if p["physical"]]
    result["port_total"] = len(physical)
    result["port_up"] = sum(1 for p in physical if p["status"] == "up")

    return result
