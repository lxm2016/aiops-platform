"""Storage array collection: SNMP (HOST-RESOURCES) and SMI-S (WBEM/CIM).

不同厂商存储管理方式不同:
- SNMP: 多数存储(华为/H3C/宏杉/EMC等)支持标准HOST-RESOURCES-MIB, 可取容量/磁盘/控制器状态
- SMI-S: 通过pywbem走CIM/WBEM协议(华为OceanStor/EMC等), 可取存储池/卷/磁盘/控制器明细
- 无: 仅手工登记容量
"""
import asyncio
from typing import Optional

from app.services.snmp_service import snmp_walk, snmp_get, _safe_int

# HOST-RESOURCES-MIB
OID_HR_STORAGE_TYPES = "1.3.6.1.2.1.25.2.3.1.2"   # 存储类型OID
OID_HR_STORAGE_DESCR = "1.3.6.1.2.1.25.2.3.1.3"   # 描述
OID_HR_STORAGE_AU = "1.3.6.1.2.1.25.2.3.1.4"      # 分配单元大小(字节)
OID_HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"    # 总大小(AU)
OID_HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"    # 已用(AU)

# 固定存储类型OID (hrStorageType)
STORAGE_TYPE_FIXED = "1.3.6.1.2.1.25.2.1.4"

# CIM状态 -> 中文
CIM_STATUS_MAP = {
    0: "未知", 1: "其他", 2: "正常", 3: "降级", 4: "压力",
    5: "预测性故障", 6: "错误", 7: "不可恢复错误",
}

# ---------------------------------------------------------------------------
# 华为 OceanStor 私有 MIB (企业号 34774)
# ---------------------------------------------------------------------------
# 为什么必须单列: 华为 OceanStor(V3/V5/V6/Dorado 都算)**不暴露标准 HOST-RESOURCES
# -MIB 的 hrStorage 表**, 只答自己的私有 MIB。原来只 walk hrStorage, 所以华为
# 存储永远采不到容量。OID 取自 Zabbix 官方模板
# templates/san/huawei_5300v5_snmp 与 huawei_dorado_snmp, 两者同一棵 34774.4.1。
HW_BASE = "1.3.6.1.4.1.34774.4.1"
HW_SYS_STATUS = f"{HW_BASE}.1.3.0"     # 系统运行状态
HW_CAP_USED = f"{HW_BASE}.1.4.0"       # 已用容量 (MB)
HW_CAP_TOTAL = f"{HW_BASE}.1.5.0"      # 总容量   (MB)
HW_VERSION = f"{HW_BASE}.1.6.0"        # 设备版本
# .8 实测是**已分配(thin 供给)容量**, 不是已用 —— 见 _pool_capacity() 的注释
HW_POOL = f"{HW_BASE}.23.4.2.1"        # 存储池: .2名称 .5健康 .6运行 .7总 .8已分配 .9可用
HW_CTRL = f"{HW_BASE}.23.5.2.1"        # 控制器: .1ID .2健康 .3运行 .6角色 .8CPU% .9内存%
HW_DISK = f"{HW_BASE}.23.5.1.1"        # 硬盘:   .1ID .2健康 .3运行 .4位置 .11温度 .12型号 .25健康分
HW_FAN = f"{HW_BASE}.23.5.4.1"         # 风扇:   .1ID .2位置 .3健康 .4运行
HW_BBU = f"{HW_BASE}.23.5.5.1"         # 电源/BBU: .1ID .2位置 .3健康 .4运行
HW_LUN = f"{HW_BASE}.19.9.4.1"         # LUN:    .2名称 .5容量(KB) .11状态
HW_ENC = f"{HW_BASE}.23.5.6.1"         # 机框:   .2名称 .4健康 .5运行 .8温度
# 标准 MIB sysDescr: Dorado V6 等型号不答私有 .1.6.0(设备版本), 但它一定答,
# 用它兜底把型号显示出来(如 "OceanStor Dorado 5600 V6")
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"

# 华为容量字段单位是 MB(模板里统一 ×1048576 得字节); LUN 容量单位是 KB
_MB = 1024 * 1024
_KB = 1024

# 华为自己的状态枚举(health/running) —— 与 CIM 那套不是一回事
HW_HEALTH = {1: "正常", 2: "故障", 3: "降级", 4: "未知", 0: "未知"}
HW_RUNNING = {1: "正常", 2: "故障", 3: "未启动", 4: "正在启动",
              5: "正在停止", 6: "已停止", 7: "降级", 0: "未知",
              # 27: 实测 OceanStor 上"存储池运行/控制器运行/硬盘运行"健康时一律是 27,
              #     而同一对象的 health 字段是 1(正常)。枚举表里没写 27, 不补就会显示
              #     "状态27" 并被判成异常。这是现场实测补的, 不是猜的。
              27: "正常"}

# 硬盘健康分: 0~100 有效, 255 = 该盘不支持/未上报(实测 HSSD 盘全为 255)
_HEALTH_SCORE_INVALID = {255}


def _hw_status(mapping: dict, raw) -> str:
    v = _safe_int(raw)
    if v is None:
        return str(raw or "未知")
    return mapping.get(v, f"状态{v}")


def _hw_health_score(raw):
    """健康分归一化: 越界或 255(不支持)一律返回 None, 避免前端拿 255 当百分比。"""
    v = _safe_int(raw)
    if v is None or v < 0 or v > 100 or v in _HEALTH_SCORE_INVALID:
        return None
    return v


def _pick_unit(raw: int, target_bytes: float) -> int:
    """给一个**不知道单位**的容量字段挑单位。

    挑与 target_bytes 最接近的那个倍数。用在存储池 .8(已分配容量)上:
    该字段单位随固件/型号在 KB/MB 间漂移, 但与"ΣLUN容量"必然同量级,
    所以拿 LUN 总量当基准反推单位比硬编码可靠。
    """
    if not raw or target_bytes <= 0:
        return _MB
    best, best_err = _MB, None
    for mult in (1, _KB, _MB):
        err = abs(raw * mult - target_bytes) / target_bytes
        if best_err is None or err < best_err:
            best, best_err = mult, err
    return best


def _pool_capacity(total_mb: int, alloc_raw: int, free_mb: int, alloc_unit: int):
    """由存储池三件套算出 (总字节, 已用字节, 已分配字节, 备注)。

    关键坑(实测) —— .8 是「已分配/thin 供给容量」, 精简配置下会**远大于**总容量:
        某台 OceanStor: .7总=28519332MB(27.2TiB)  .8=138566656  .9可用=12058318MB
          · 若把 .8 当"已用MB": 使用率 = 486%  → 假告警
          · .8 按 KB 折算 = 132.15TiB ≈ 该阵列 16 个 LUN 容量之和(超配 4.86 倍)
          · 真实已用 = 总 - 可用 = 16.46TiB, 使用率 57.7%
    所以已用一律用 (总-可用) 同单位相减得, .8 只作为"已分配"单独上报。
    """
    total_b = max(total_mb, 0) * _MB
    free_b = max(free_mb, 0) * _MB
    if total_mb <= 0:
        # 没有总量时只能信已用+可用
        return (free_b + max(alloc_raw, 0) * _MB), (max(alloc_raw, 0) * _MB), \
            (max(alloc_raw, 0) * alloc_unit), "缺总容量, 已用按(已用+可用)估算"
    if free_mb <= 0 or free_b > total_b:
        # 可用缺失或口径异常: 退一步, 若 .8 不超过总量就当已用用
        u = max(alloc_raw, 0) * _MB
        u = min(u, total_b) if u else 0
        return total_b, u, (max(alloc_raw, 0) * alloc_unit), "可用容量缺失/异常, 已用改用已分配并截断"
    used_b = total_b - free_b
    alloc_b = max(alloc_raw, 0) * alloc_unit
    note = ""
    if alloc_b > total_b:
        note = (f"已分配 {alloc_b / (1024 ** 4):.2f}TiB 超过总容量 "
                f"{total_b / (1024 ** 4):.2f}TiB (超配 {alloc_b / total_b:.2f} 倍, "
                f"属精简配置, 非故障)")
    return total_b, used_b, alloc_b, note

# WBEM端口: http 5988 / https 5989
WBEM_PORTS = (5989, 5988)


async def collect_huawei_snmp(ip: str, community, version: str = "2c") -> dict:
    """华为 OceanStor 私有 MIB 采集 (V3/V5/V6/Dorado 通用)。

    community 传团体名字符串(v1/v2c), 或 v3 的 USM 参数字典 —— 华为 DeviceManager
    里「SNMPv1&SNMPv2c协议开关」默认是**关闭**的, 只留 USM 用户, 所以 v3 是常态。
    """
    result = {
        "reachable": False, "source": "huawei-oceanstor",
        "capacity_tb": 0.0, "used_tb": 0.0, "used_percent": 0.0,
        "details": {"disks": [], "controllers": [], "pools": [], "volumes": [],
                    "fans": [], "power": [], "enclosures": []},
    }

    status = await snmp_get(ip, community, HW_SYS_STATUS, version)
    ver = await snmp_get(ip, community, HW_VERSION, version)

    # 判据不能只看那两个标量: 实测有 OceanStor 的 .1.3.0(系统运行状态) 和
    # .1.6.0(设备版本) **全部不答**, 但池/控制器/硬盘/LUN 表数据齐全。
    # 只认标量会把这种机器判成"不是华为" → 退回 hrStorage → 一台机器都采不到。
    # 所以: 34774.4.1 下任一私有表答得上来即认定华为, 顺带把索引表缓存复用。
    probe = {}
    for _k, _o in (("pools", f"{HW_POOL}.2"), ("controllers", f"{HW_CTRL}.1"),
                   ("disks", f"{HW_DISK}.1"), ("luns", f"{HW_LUN}.2")):
        probe[_k] = await snmp_walk(ip, community, _o, version)
    if status is None and ver is None and not any(probe.values()):
        return result                      # 不是华为存储, 让调用方退回标准 MIB
    result["reachable"] = True
    if status is not None:
        result["details"]["system_status"] = _hw_status(HW_RUNNING, status)
    if ver:
        result["details"]["version"] = str(ver)
    else:
        # 私有 .1.6.0 不答时退回标准 sysDescr —— 至少让页面上能看到型号
        desc = await snmp_get(ip, community, OID_SYS_DESCR, version)
        if desc:
            result["details"]["version"] = str(desc).strip().splitlines()[0][:120]

    # LUN 容量只取一次: 既用于明细, 也用来反推存储池 .8(已分配) 的单位
    lun_cap_raw = (await snmp_walk(ip, community, f"{HW_LUN}.5", version)
                   if probe["luns"] else {})

    # ---- 存储池 (容量主口径) ----
    pool_names = probe["pools"] or {}
    if pool_names:
        p_health = await snmp_walk(ip, community, f"{HW_POOL}.5", version)
        p_run = await snmp_walk(ip, community, f"{HW_POOL}.6", version)
        p_total = await snmp_walk(ip, community, f"{HW_POOL}.7", version)
        p_alloc = await snmp_walk(ip, community, f"{HW_POOL}.8", version)
        p_free = await snmp_walk(ip, community, f"{HW_POOL}.9", version)

        # .8 单位随型号在 KB/MB 间漂移, 用 ΣLUN容量 当基准反推最稳
        lun_bytes = sum((_safe_int(v) or 0) for v in lun_cap_raw.values()) * _KB
        alloc_unit = _MB
        for sfx in pool_names:
            raw = _safe_int(p_alloc.get(sfx)) or 0
            if raw:
                alloc_unit = _pick_unit(raw, lun_bytes)
                break

        total_b = used_b = alloc_b = 0
        notes = []
        for sfx, nm in pool_names.items():
            t, u, a, note = _pool_capacity(
                _safe_int(p_total.get(sfx)) or 0,
                _safe_int(p_alloc.get(sfx)) or 0,
                _safe_int(p_free.get(sfx)) or 0,
                alloc_unit,
            )
            total_b += t
            used_b += u
            alloc_b += a
            if note:
                notes.append(note)
            result["details"]["pools"].append({
                "name": str(nm or f"池{sfx}"),
                "health": _hw_status(HW_HEALTH, p_health.get(sfx)),
                "running": _hw_status(HW_RUNNING, p_run.get(sfx)),
                "size_tb": round(t / (1024 ** 4), 2),
                "used_tb": round(u / (1024 ** 4), 2),
                "free_tb": round((_safe_int(p_free.get(sfx)) or 0) * _MB / (1024 ** 4), 2),
                "alloc_tb": round(a / (1024 ** 4), 2) if a else None,
                "used_percent": round(u / t * 100, 1) if t else 0.0,
            })
        if total_b:
            result["capacity_tb"] = round(total_b / (1024 ** 4), 2)
            result["used_tb"] = round(used_b / (1024 ** 4), 2)
            result["used_percent"] = round(min(used_b, total_b) / total_b * 100, 1)
            if alloc_b:
                result["details"]["alloc_tb"] = round(alloc_b / (1024 ** 4), 2)
                result["details"]["over_subscription"] = round(alloc_b / total_b, 2)
            if notes:
                result["details"]["capacity_note"] = "; ".join(notes[:3])

    # 池表读不到(权限/型号差异)时, 退回整机标量口径
    if not result["capacity_tb"]:
        t = _safe_int(await snmp_get(ip, community, HW_CAP_TOTAL, version)) or 0
        u = _safe_int(await snmp_get(ip, community, HW_CAP_USED, version)) or 0
        if t:
            result["capacity_tb"] = round(t * _MB / (1024 ** 4), 2)
            result["used_tb"] = round(min(u, t) * _MB / (1024 ** 4), 2)
            result["used_percent"] = round(min(u, t) / t * 100, 1)

    # ---- 控制器 (CPU/内存使用率) ----
    ctrl_ids = probe["controllers"] or {}
    if ctrl_ids:
        c_health = await snmp_walk(ip, community, f"{HW_CTRL}.2", version)
        c_run = await snmp_walk(ip, community, f"{HW_CTRL}.3", version)
        c_role = await snmp_walk(ip, community, f"{HW_CTRL}.6", version)
        c_cpu = await snmp_walk(ip, community, f"{HW_CTRL}.8", version)
        c_mem = await snmp_walk(ip, community, f"{HW_CTRL}.9", version)
        for sfx, cid in ctrl_ids.items():
            role = _safe_int(c_role.get(sfx))
            result["details"]["controllers"].append({
                "name": f"控制器{cid or sfx}",
                "role": {1: "主", 2: "从", 0: "未知"}.get(role, role),
                "health": _hw_status(HW_HEALTH, c_health.get(sfx)),
                "running": _hw_status(HW_RUNNING, c_run.get(sfx)),
                "cpu": _safe_int(c_cpu.get(sfx)),
                "memory": _safe_int(c_mem.get(sfx)),
            })

    # ---- 硬盘 ----
    disk_ids = probe["disks"] or {}
    if disk_ids:
        d_model = await snmp_walk(ip, community, f"{HW_DISK}.12", version)
        d_loc = await snmp_walk(ip, community, f"{HW_DISK}.4", version)
        d_health = await snmp_walk(ip, community, f"{HW_DISK}.2", version)
        d_run = await snmp_walk(ip, community, f"{HW_DISK}.3", version)
        d_temp = await snmp_walk(ip, community, f"{HW_DISK}.11", version)
        d_score = await snmp_walk(ip, community, f"{HW_DISK}.25", version)
        for sfx, did in disk_ids.items():
            result["details"]["disks"].append({
                "name": f"{d_loc.get(sfx) or did} {d_model.get(sfx) or ''}".strip(),
                "health": _hw_status(HW_HEALTH, d_health.get(sfx)),
                "running": _hw_status(HW_RUNNING, d_run.get(sfx)),
                "temperature": _safe_int(d_temp.get(sfx)),
                # 255 = 该盘未上报/不支持健康分, 不能当百分比展示
                "health_score": _hw_health_score(d_score.get(sfx)),
            })

    # ---- LUN ----
    lun_names = probe["luns"] or {}
    if lun_names:
        l_cap = lun_cap_raw
        l_st = await snmp_walk(ip, community, f"{HW_LUN}.11", version)
        for sfx, nm in lun_names.items():
            cap_b = (_safe_int(l_cap.get(sfx)) or 0) * _KB
            result["details"]["volumes"].append({
                "name": str(nm or f"LUN{sfx}"),
                "size_tb": round(cap_b / (1024 ** 4), 3),
                "status": _hw_status(HW_RUNNING, l_st.get(sfx)),
            })

    # ---- 风扇 / 电源 / 机框 (只取数量与异常项, 避免明细过大) ----
    for key, oid, label in (("fans", HW_FAN, "风扇"), ("power", HW_BBU, "电源")):
        ids = await snmp_walk(ip, community, f"{oid}.1", version)
        if not ids:
            continue
        health = await snmp_walk(ip, community, f"{oid}.3", version)
        for sfx, i in ids.items():
            result["details"][key].append({
                "name": f"{label}{i or sfx}",
                "health": _hw_status(HW_HEALTH, health.get(sfx)),
            })

    return result


async def collect_storage_snmp(ip: str, community, version: str = "2c") -> dict:
    """SNMP 采集存储。

    顺序: 先试华为 OceanStor 私有 MIB(华为存储不暴露 hrStorage, 只能走这条),
    不是华为设备再退回标准 HOST-RESOURCES-MIB。
    """
    hw = await collect_huawei_snmp(ip, community, version)
    if hw.get("reachable"):
        return hw

    result = {
        "reachable": False, "source": "host-resources",
        "capacity_tb": 0.0,
        "used_tb": 0.0,
        "used_percent": 0.0,
        "details": {"disks": [], "controllers": [], "pools": [], "volumes": []},
    }

    types = await snmp_walk(ip, community, OID_HR_STORAGE_TYPES, version)
    if not types:
        return result
    result["reachable"] = True

    descrs = await snmp_walk(ip, community, OID_HR_STORAGE_DESCR, version)
    aus = await snmp_walk(ip, community, OID_HR_STORAGE_AU, version)
    sizes = await snmp_walk(ip, community, OID_HR_STORAGE_SIZE, version)
    useds = await snmp_walk(ip, community, OID_HR_STORAGE_USED, version)

    total_bytes = 0
    used_bytes = 0
    for suffix, type_oid in types.items():
        if type_oid != STORAGE_TYPE_FIXED:
            continue
        au = _safe_int(aus.get(suffix)) or 0
        size = _safe_int(sizes.get(suffix)) or 0
        used = _safe_int(useds.get(suffix)) or 0
        if size <= 0:
            continue
        total_bytes += size * au
        used_bytes += min(used, size) * au
        result["details"]["disks"].append({
            "name": descrs.get(suffix, f"存储单元{suffix}"),
            "size_tb": round(size * au / (1024 ** 4), 2),
            "used_tb": round(used * au / (1024 ** 4), 2),
            "used_percent": round(used / size * 100, 1) if size else 0,
        })

    if total_bytes:
        result["capacity_tb"] = round(total_bytes / (1024 ** 4), 2)
        result["used_tb"] = round(used_bytes / (1024 ** 4), 2)
        result["used_percent"] = round(used_bytes / total_bytes * 100, 1)

    # 控制器状态: 尝试常见厂商OID (华为/宏杉等实体状态表)
    for oid in ("1.3.6.1.4.1.2011.2.235.1.1.12.1.6", "1.3.6.1.4.1.25506.2.6.1.1.1.1.12"):
        states = await snmp_walk(ip, community, oid, version)
        if states:
            for suffix, val in states.items():
                result["details"]["controllers"].append({
                    "name": f"控制器{suffix}",
                    "status": CIM_STATUS_MAP.get(_safe_int(val), val),
                })
            break

    return result


def _collect_storage_wbem_sync(
    ip: str, username: str, password: str, namespace: str = "root/cimv2"
) -> dict:
    """SMI-S采集 (pywbem, 同步)。枚举 CIM_ComputerSystem/StoragePool/LogicalDisk/PhysicalDisk。"""
    import pywbem

    result = {
        "reachable": False,
        "capacity_tb": 0.0,
        "used_tb": 0.0,
        "used_percent": 0.0,
        "details": {"disks": [], "controllers": [], "pools": [], "volumes": []},
    }

    conn = None
    last_err = None
    for port in WBEM_PORTS:
        url = f"{'https' if port == 5989 else 'http'}://{ip}:{port}"
        try:
            conn = pywbem.WBEMConnection(
                url, (username, password),
                default_namespace=namespace,
                no_verification=True,  # 存储自签证书常见
                timeout=15,
            )
            conn.EnumerateClassNames()  # 触发连接验证
            last_err = None
            break
        except Exception as e:
            last_err = e
            conn = None
    if conn is None:
        if last_err is not None:
            result["error"] = str(last_err)
        return result
    result["reachable"] = True

    def _instances(class_name):
        try:
            return conn.EnumerateInstances(class_name)
        except Exception:
            return []

    def _prop(inst, *names, default=0):
        for n in names:
            if n in inst:
                return inst[n]
        return default

    def _first_status(inst) -> str:
        raw = _prop(inst, "OperationalStatus", default=[0])
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else 0
        return CIM_STATUS_MAP.get(int(raw or 0), "未知")

    # 控制器 (CIM_ComputerSystem)
    for inst in _instances("CIM_ComputerSystem"):
        result["details"]["controllers"].append({
            "name": str(_prop(inst, "ElementName", "Name", default="控制器")),
            "status": _first_status(inst),
        })

    # 存储池
    total_bytes = 0
    used_bytes = 0
    for inst in _instances("CIM_StoragePool"):
        prim = int(_prop(inst, "Primordial", default=0) or 0)
        total = int(_prop(inst, "TotalManagedSpace", default=0) or 0)
        remaining = int(_prop(inst, "RemainingManagedSpace", default=0) or 0)
        used = max(0, total - remaining) if total else 0
        if prim:
            total_bytes += total
            used_bytes += used
        result["details"]["pools"].append({
            "name": str(_prop(inst, "ElementName", "Name", default="存储池")),
            "size_tb": round(total / (1024 ** 4), 2),
            "used_tb": round(used / (1024 ** 4), 2),
            "used_percent": round(used / total * 100, 1) if total else 0,
        })

    # 卷/LUN
    for inst in _instances("CIM_LogicalDisk"):
        size = int(_prop(inst, "MaxBlockSize", default=0) or 0) * int(_prop(inst, "NumberOfBlocks", default=0) or 0)
        result["details"]["volumes"].append({
            "name": str(_prop(inst, "ElementName", "DeviceID", "Name", default="卷")),
            "size_tb": round(size / (1024 ** 4), 2),
        })

    # 物理磁盘
    for inst in _instances("CIM_PhysicalDisk") or _instances("CIM_MediaAccessDevice"):
        cap = int(_prop(inst, "Capacity", default=0) or 0)
        result["details"]["disks"].append({
            "name": str(_prop(inst, "ElementName", "DeviceID", "Name", default="磁盘")),
            "size_tb": round(cap / (1024 ** 4), 2),
            "status": _first_status(inst),
        })

    if total_bytes:
        result["capacity_tb"] = round(total_bytes / (1024 ** 4), 2)
        result["used_tb"] = round(used_bytes / (1024 ** 4), 2)
        result["used_percent"] = round(used_bytes / total_bytes * 100, 1)

    return result


async def collect_storage(
    protocol: str, ip: str,
    snmp_community="public", snmp_version: str = "2c",
    username: str = "", password: str = "",
) -> dict:
    """按协议采集存储, 返回统一结构。protocol: snmp / smi-s / none。

    snmp_version="3" 时 snmp_community 需传 USM 参数字典(dict) —— 见
    snmp_service._creds 的字段说明; 其余情况传团体名字符串。
    """
    if protocol == "snmp":
        return await collect_storage_snmp(ip, snmp_community, snmp_version)
    if protocol == "smi-s":
        return await asyncio.to_thread(
            _collect_storage_wbem_sync, ip, username, password
        )
    return {"reachable": False, "capacity_tb": 0.0, "used_tb": 0.0,
            "used_percent": 0.0, "details": {}}
