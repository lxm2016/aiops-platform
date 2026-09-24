#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SNMP CPU/内存 OID 诊断工具 —— 定位"交换机 CPU/内存永远显示 1%"的真凶。

背景
----
华三(hh3cEntityExt*)、华为等把 CPU/内存放在**按实体索引的表**里。如果只做
一次 GETNEXT 取第一个实例, 很可能拿到某个"恒为 1"的实体, 面板于是永远显示
1%。正确做法是 walk 整张表, 看每个实例的真实值, 取合理的百分比。

本工具把常见厂商候选 OID 全部 walk 一遍, 逐个打印 "实例 -> 值",
一眼就能看出到底哪个 OID、哪个实体才是真实占用。

依赖
----
需要 pysnmp (后端 venv 里已装)。请用后端解释器运行:
    /opt/aiops-deploy/backend/venv/bin/python /opt/aiops-deploy/tools/probe_snmp.py --ip 10.0.0.1
    # 团体名不是 public 时:
    --ip 10.0.0.1 --community <团体名> --version 2c

用法提示
--------
* 输出里标注 [合理] 的行, 就是可作为 CPU/内存使用率的候选。
* 若某 OID 的值都不在 0~100, 说明该列不是百分比(可能是字节数/空闲率),
  不要拿它当使用率。
* 把输出贴回来, 我据此把平台里的 OID 定死。
"""
import argparse
import asyncio
import sys

try:
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity, get_cmd, walk_cmd,
        UsmUserData, usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol,
        usmDESPrivProtocol, usmAesCfb128Protocol,
    )
except Exception as e:  # noqa: BLE001
    print(f"[错误] 无法导入 pysnmp: {e}")
    print("请用后端虚拟环境的解释器运行, 例如:")
    print("  /opt/aiops-deploy/backend/venv/bin/python probe_snmp.py --ip <交换机IP>")
    sys.exit(1)

AUTH_PROTOCOLS = {"md5": usmHMACMD5AuthProtocol, "sha": usmHMACSHAAuthProtocol,
                  "sha1": usmHMACSHAAuthProtocol}
PRIV_PROTOCOLS = {"des": usmDESPrivProtocol, "aes": usmAesCfb128Protocol,
                  "aes128": usmAesCfb128Protocol}

# 现代设备(含华为 OceanStor)常配 SHA-256 / AES-256, 必须支持, 否则认证必然失败。
# 这些算法在 pysnmp 的部分版本里才有, 装了才挂上; 没装就保持上表的子集。
try:  # pragma: no cover
    from pysnmp.hlapi.v3arch.asyncio import (
        usmHMAC128SHA224AuthProtocol as _sha224,
        usmHMAC192SHA256AuthProtocol as _sha256,
        usmHMAC256SHA384AuthProtocol as _sha384,
        usmHMAC384SHA512AuthProtocol as _sha512,
    )
    AUTH_PROTOCOLS.update({"sha224": _sha224, "sha256": _sha256,
                           "sha384": _sha384, "sha512": _sha512})
except ImportError:
    pass
try:  # pragma: no cover
    from pysnmp.hlapi.v3arch.asyncio import (
        usmAesCfb192Protocol as _aes192,
        usmAesCfb256Protocol as _aes256,
    )
    PRIV_PROTOCOLS.update({"aes192": _aes192, "aes256": _aes256})
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 华为 OceanStor 私有 MIB (企业号 34774) —— 存储探测用
# ---------------------------------------------------------------------------
# 关键: 华为存储**不暴露标准 hrStorage**, 只答自己的私有 MIB。只 walk hrStorage
# 会一无所获, 这就是"平台采不到华为存储容量"的根本原因。
# OID 取自 Zabbix 官方模板 huawei_5300v5_snmp / huawei_dorado_snmp。
HW = "1.3.6.1.4.1.34774.4.1"
# 存储池容量三件套 —— 单独拎出来是因为它们的**口径容易搞错**, 见 _warn_capacity_gap()
HW_POOL_TOTAL = f"{HW}.23.4.2.1.7"
HW_POOL_ALLOC = f"{HW}.23.4.2.1.8"
HW_POOL_FREE = f"{HW}.23.4.2.1.9"
HW_LUN_CAP = f"{HW}.19.9.4.1.5"
# LUN 表(索引 hwStorageLunID): .2名称 .3WWN .4池ID .5容量(KB) .11状态
HW_LUN_TABLE = f"{HW}.19.9.4.1"
OID_HW_STORAGE = [
    ("HW 系统运行状态      ", f"{HW}.1.3.0"),
    ("HW 已用容量(单位MB)  ", f"{HW}.1.4.0"),
    ("HW 总容量  (单位MB)  ", f"{HW}.1.5.0"),
    ("HW 设备版本          ", f"{HW}.1.6.0"),
    ("HW 存储池名称        ", f"{HW}.23.4.2.1.2"),
    ("HW 存储池健康状态    ", f"{HW}.23.4.2.1.5"),
    ("HW 存储池运行状态    ", f"{HW}.23.4.2.1.6"),
    ("HW 存储池总容量(MB)  ", f"{HW}.23.4.2.1.7"),
    ("HW 存储池已分配(MB)  ", f"{HW}.23.4.2.1.8"),
    ("HW 存储池可用(MB)    ", f"{HW}.23.4.2.1.9"),
    ("HW 控制器 ID         ", f"{HW}.23.5.2.1.1"),
    ("HW 控制器健康        ", f"{HW}.23.5.2.1.2"),
    ("HW 控制器运行        ", f"{HW}.23.5.2.1.3"),
    ("HW 控制器角色        ", f"{HW}.23.5.2.1.6"),
    ("HW 控制器 CPU%       ", f"{HW}.23.5.2.1.8"),
    ("HW 控制器 内存%      ", f"{HW}.23.5.2.1.9"),
    ("HW 硬盘 位置         ", f"{HW}.23.5.1.1.4"),
    ("HW 硬盘 型号         ", f"{HW}.23.5.1.1.12"),
    ("HW 硬盘 健康         ", f"{HW}.23.5.1.1.2"),
    ("HW 硬盘 运行         ", f"{HW}.23.5.1.1.3"),
    ("HW 硬盘 温度℃        ", f"{HW}.23.5.1.1.11"),
    ("HW 硬盘 健康分       ", f"{HW}.23.5.1.1.25"),
    ("HW LUN 名称          ", f"{HW}.19.9.4.1.2"),
    ("HW LUN 容量(KB)      ", f"{HW}.19.9.4.1.5"),
    ("HW LUN 状态          ", f"{HW}.19.9.4.1.11"),
    ("HW 风扇 健康         ", f"{HW}.23.5.4.1.3"),
    ("HW 电源 健康         ", f"{HW}.23.5.5.1.3"),
]

# (标签, OID) —— 覆盖华三/华为/思科/标准, 便于一次性比对
CANDIDATES = [
    ("H3C  CPU  hh3cEntityExtCpuUsage .6", "1.3.6.1.4.1.25506.2.6.1.1.1.1.6"),
    ("H3C  CPU  hh3c .4                  ", "1.3.6.1.4.1.25506.2.6.1.1.1.1.4"),
    ("H3C  CPU  hh3c .3 (老的C5)         ", "1.3.6.1.4.1.25506.2.6.1.1.1.1.3"),
    ("H3C  MEM  hh3cEntityExtMemUsage .8 ", "1.3.6.1.4.1.25506.2.6.1.1.1.1.8"),
    ("H3C  MEM  hh3c .2 (老的C5)         ", "1.3.6.1.4.1.25506.2.6.1.1.1.1.2"),
    ("H3C  MEM  hh3c .7 (含义存疑)       ", "1.3.6.1.4.1.25506.2.6.1.1.1.1.7"),
    ("HW   CPU  hwEntityCpuUsage         ", "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5"),
    ("HW   MEM  hwEntityMemUsage         ", "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7"),
    ("STD  CPU  hrProcessorLoad          ", "1.3.6.1.2.1.25.3.3.1.2"),
    ("STD  MEM  hrStorageType            ", "1.3.6.1.2.1.25.2.3.1.2"),
    ("STD  MEM  hrStorageSize            ", "1.3.6.1.2.1.25.2.3.1.5"),
    ("STD  MEM  hrStorageUsed            ", "1.3.6.1.2.1.25.2.3.1.6"),
]
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
HR_STORAGE_RAM = "1.3.6.1.2.1.25.2.1.2"


def _mp(version):
    return 1 if version == "2c" else 0


def _resolve(proto, table, kind):
    """查算法, 查不到**直接报错** —— 不能静默退回默认算法。

    现场教训: 设备配的是 AES-256, 工具若悄悄按 AES-128 发, 表现就是
    "始终无响应", 极难排查。宁可当场告诉你这个算法不支持。
    """
    if proto in table:
        return table[proto]
    raise SystemExit(
        f"[错误] 不支持的{kind}算法 {proto!r}。"
        f"当前 pysnmp 支持的: {sorted(table)}\n"
        f"      若确实需要该算法, 请升级 pysnmp: "
        f"./backend/venv/bin/pip install -U pysnmp")


def _build_auth(args):
    """按命令行参数构造 SNMP 凭据 (v1/v2c 返回团体名, v3 返回 USM 参数)。"""
    if args.v3:
        auth_proto = _resolve(args.auth_proto, AUTH_PROTOCOLS, "认证")
        priv_proto = _resolve(args.priv_proto, PRIV_PROTOCOLS, "加密")
        return UsmUserData(
            args.user,
            **({"authKey": args.auth_pass, "authProtocol": auth_proto}
               if args.auth_pass else {}),
            **({"privKey": args.priv_pass, "privProtocol": priv_proto}
               if args.priv_pass else {}),
        )
    return CommunityData(args.community, mpModel=_mp(args.version))


def _build_ctx(args):
    # ContextData 第一个位置参数是 contextEngineId, 不是 contextName, 必须写关键字
    return ContextData(contextName=args.context or "") if args.v3 else ContextData()


async def _get(engine, target, auth, ctx, oid):
    try:
        err_ind, err_stat, _idx, vbs = await get_cmd(
            engine, auth, target, ctx,
            ObjectType(ObjectIdentity(oid)), lookupMib=False)
        if err_ind or err_stat:
            return None
        return str(vbs[0][1])
    except Exception:
        return None


async def _walk(engine, target, auth, ctx, oid):
    """返回 [(实例后缀, 值字符串)]。"""
    out = []
    try:
        gen = walk_cmd(engine, auth, target, ctx,
                       ObjectType(ObjectIdentity(oid)),
                       lexicographicMode=False, lookupMib=False)
        async for (err_ind, err_stat, _idx, vbs) in gen:
            if err_ind or err_stat:
                break
            for vb in vbs:
                full = str(vb[0])
                if full.startswith(oid + "."):
                    out.append((full[len(oid) + 1:], str(vb[1])))
    except Exception as e:  # noqa: BLE001
        out.append(("__error__", f"{type(e).__name__}: {e}"))
    return out


def _warn_capacity_gap(total_rows, alloc_rows, free_rows, lun_rows):
    """存储池容量口径体检 —— 直接算出**正确**的使用率并打印。

    踩过的坑(172.16.10.199 实测):
        .7 总容量 = 28519332 MB (27.20 TiB)
        .8        = 138566656       (按 MB 折算是 132.15 TiB)
        .9 可用   = 12058318 MB (11.50 TiB)
      · 若照着字段名把 .8 当"已用", 使用率 = 486% → 满盘假告警
      · .8 其实是**已分配(thin 供给)容量**, 超配 4.86 倍
      · 真实已用 = 总 - 可用 = 15.70 TiB, 使用率 57.7%
    所以这里一律用 (总-可用)/总 给数, 并顺带核对 .8 的单位。
    """
    def to_map(rows):
        out = {}
        for sfx, val in rows or []:
            try:
                out[sfx] = int(str(val).strip())
            except (TypeError, ValueError):
                continue
        return out

    tot, alloc, free = (to_map(total_rows), to_map(alloc_rows), to_map(free_rows))
    if not tot:
        return
    tib = 1048576.0
    lun_kb = sum(to_map(lun_rows).values())

    for sfx, t in sorted(tot.items()):
        if t <= 0:
            continue
        f = free.get(sfx)
        a = alloc.get(sfx)
        name = f"实例 {sfx}"
        if f is not None and 0 <= f <= t:
            used = t - f
            print(f"\n  [容量口径] 存储池 {name}: 总 {t / tib:.2f} TiB, "
                  f"可用 {f / tib:.2f} TiB")
            print(f"             → 真实已用 {used / tib:.2f} TiB, "
                  f"使用率 {used / t * 100:.1f}%   (= (总-可用)/总)")
        else:
            print(f"\n  [容量口径] 存储池 {name}: 总 {t / tib:.2f} TiB, "
                  f"可用字段缺失/异常, 无法用(总-可用)推算")

        if a and a > t:
            # 已分配 > 总容量: 精简配置超配。判断 .8 的单位到底是不是 MB
            as_mb, as_kb = a / tib, a / tib / 1024
            print(f"  ⚠ 该池 .8(已分配)= {a} 折算: MB→{as_mb:.2f} TiB, KB→{as_kb:.2f} TiB")
            if lun_kb:
                lun_tib = lun_kb / tib / 1024   # LUN 单位是 KB
                print(f"    Σ LUN 容量 = {lun_tib:.2f} TiB; 与之同量级的是 "
                      f"{'MB' if abs(as_mb - lun_tib) < abs(as_kb - lun_tib) else 'KB'} 口径")
            print(f"    → 这是**已分配/thin 供给**容量, 超配 {a / t:.2f} 倍, 属正常现象")
            print(f"    → 切勿用 已分配/总 = {a / t * 100:.1f}% 当使用率, 那是假告警")
        elif a:
            print(f"    .8(已分配) = {a} MB ({a / tib:.2f} TiB), 未超过总容量, "
                  f"此池未超配")


async def dump_table(engine, target, auth, ctx, table_oid, max_col=40, label=""):
    """把一张表**每一列**都走一遍, 报出哪列有数据。

    为什么需要它: 华为 MIB 只给列号不给中文名, 而"某张表有没有某个字段"光靠
    文档和厂商模板都靠不住 —— 挨列走一遍是唯一能拿到确凿答案的办法。
    输出里会标出"像容量"的列(数值大且随实例变化), 便于定位 LUN 的已用容量。
    """
    print("\n" + "=" * 78)
    print(f" 整表列扫描 {label or table_oid}")
    print(f" 表 OID = {table_oid}   逐列走 .1 ~ .{max_col}")
    print("=" * 78)
    hits = []
    for col in range(1, max_col + 1):
        oid = f"{table_oid}.{col}"
        rows = await _walk(engine, target, auth, ctx, oid)
        if rows and rows[0][0] == "__error__":
            continue
        if not rows:
            continue
        vals = [v for _, v in rows]
        nums = []
        for v in vals:
            try:
                nums.append(int(str(v).strip()))
            except (TypeError, ValueError):
                nums = []
                break
        # 数值列且量级大 -> 很可能是容量; 但**先识别无效哨兵值**
        # 真机踩过: Dorado 5600 V6 的 LUN 表 .6/.10 恒为 4294967295(0xFFFFFFFF),
        # 那是"该字段本型号不支持"的占位, 不是容量 —— 不识别就会被当容量误报。
        hint = ""
        if nums:
            mx = max(nums)
            sentinels = {2 ** 32 - 1, 2 ** 31 - 1, 2 ** 64 - 1}
            if all(n in sentinels for n in nums):
                hint = "  <== 全为无效哨兵值(0xFFFFFFFF/0x7FFFFFFF), 该字段未实现"
            elif mx >= 10 ** 7:
                hint = "  <== 数值很大, 像容量(B/KB/扇区口径需再判)"
            elif mx <= 100:
                hint = "  <== 0~100, 像百分比/状态码"
        else:
            hint = "  <== 文本"
        hits.append((col, len(rows), vals[:3], hint))

    if not hits:
        print(" 该表无任何列有数据(可能表不存在, 或凭据/上下文不对)")
        return

    # 索引列(实例数最多的那一列)先亮出来, 方便对照设备界面
    for col, n, sample, hint in hits:
        shown = ", ".join(str(s)[:28] for s in sample)
        print(f"  列 .{col:<3} 实例 {n:<4} 前几个值: {shown}{hint}")
    print(f"\n  共 {len(hits)} 列有数据。把本段整体贴回来即可定死每个列号的含义。")


async def sniff_lun_used(engine, target, auth, ctx, max_col=30):
    """在 LUN 表里自动定位"已用容量"列 —— **自校验, 不靠猜**。

    以 .5(总容量, 单位 KB) 为基准, 逐列检查三条硬判据:
      ① 全是数值(文本列如名称直接排除)
      ② 每个 LUN 都满足 0 <= v <= 总容量     —— 已用不可能大于总容量
      ③ 至少有一个 LUN 的 v < 总容量          —— 否则它只是容量的副本, 不是已用
    三条同时满足 -> 高度疑似"已用容量", 并把"每 LUN 使用率"算出来给你看。
    """
    print("\n" + "=" * 78)
    print(" LUN 表「已用容量」列自动定位")
    print("=" * 78)

    size_rows = await _walk(engine, target, auth, ctx, f"{HW_LUN_TABLE}.5")
    name_rows = await _walk(engine, target, auth, ctx, f"{HW_LUN_TABLE}.2")
    if not size_rows:
        print(" 取不到 .5(总容量), 无法做基准比对。先确认凭据/上下文是否正确。")
        return None

    sizes = {}
    for sfx, v in size_rows:
        try:
            sizes[sfx] = int(str(v).strip())
        except (TypeError, ValueError):
            pass
    names = dict(name_rows) if name_rows else {}
    if not sizes:
        print(" .5 返回的不是数值, 无法比对。")
        return None

    print(f" 基准 .5 总容量(KB): {len(sizes)} 个 LUN\n")

    found = []
    for col in range(1, max_col + 1):
        if col == 5:
            continue
        rows = await _walk(engine, target, auth, ctx, f"{HW_LUN_TABLE}.{col}")
        if not rows or rows[0][0] == "__error__":
            continue
        # 按实例对齐到容量表
        cand = {}
        numeric = True
        for sfx, v in rows:
            try:
                cand[sfx] = int(str(v).strip())
            except (TypeError, ValueError):
                numeric = False
                break
        if not numeric or not cand:
            continue
        # 判据 ②: 0 <= v <= 总容量
        common = [s for s in sizes if s in cand]
        if len(common) < max(2, len(sizes) // 2):
            continue
        if any(cand[s] < 0 or cand[s] > sizes[s] for s in common):
            continue
        # 判据 ③: 至少有一个 LUN 明显小于总容量
        if all(cand[s] == sizes[s] for s in common):
            continue
        # 判据 ④: 量级必须与容量同量级 —— 否则是状态码/百分比这类小整数列。
        #   实测踩过: 状态列 .11 恒为 1, 满足 0<=1<=总容量 且 1!=总容量,
        #   会被误当成"已用容量"。要求该列最大值 >= 最大总容量的千分之一,
        #   状态码(0/1/2)直接出局, 且这个阈值随设备规模自适应。
        if max(cand[s] for s in common) < max(sizes.values()) * 0.001:
            continue
        found.append((col, cand, common))

    if not found:
        print(" ✗ 未找到符合条件的列 —— 该型号 SNMP 的 LUN 表**很可能没有已用容量字段**。")
        print("   (Zabbix 官方华为模板对 LUN 也只取容量+状态, 与此结论一致)")
        print("   若仍需监控卷使用率, 只能改走 SMI-S/REST, 或按存储池粒度告警。")
        return None

    print(f" ✓ 找到 {len(found)} 个候选列:\n")
    sig_total = sum(sizes.values()) or 1
    for col, cand, common in found:
        sig = sum(cand[s] for s in common)
        ratio = sig / sig_total * 100
        print(f" ── 列 .{col} ──  Σ该列 / Σ总容量 = {ratio:.1f}%"
              f"{'   (单位可能与 .5 不一致, 需人工确认)' if ratio > 100 else ''}")
        shown = 0
        for sfx in sorted(common, key=lambda s: -sizes.get(s, 0)):
            nm = names.get(sfx, f"LUN{sfx}")
            sz, us = sizes[sfx], cand[sfx]
            pct = us / sz * 100 if sz else 0
            print(f"    {nm:<24} 总 {sz / 1048576:>8.2f} TiB"
                  f"  该列 {us / 1048576:>8.2f} TiB  → {pct:5.1f}%")
            shown += 1
            if shown >= 8:
                rest = len(common) - shown
                if rest:
                    print(f"    ... 另有 {rest} 个 LUN")
                break
        print()
    best = found[0][0]
    print(f" 结论: 最可能是「已用容量」的列 = .{best}"
          f"   候选: {', '.join('.' + str(c[0]) for c in found)}")
    print(f" 把上面整段贴回给开发即可 —— 平台会按 .{best} 取已用容量并启用卷级 80/90 告警。")
    return best


async def scan_storage(engine, target, auth, ctx):
    """存储专用: 先探华为私有 MIB, 再探标准 hrStorage, 顺带验证 SNMPv3。"""
    print("\n" + "=" * 78)
    print(" 存储探测 (华为 OceanStor 私有 MIB / 标准 hrStorage 对照)")
    print("=" * 78)

    hub_ok = 0
    grabbed = {}
    for label, oid in OID_HW_STORAGE:
        rows = await _walk(engine, target, auth, ctx, oid)
        if oid in (HW_POOL_TOTAL, HW_POOL_ALLOC, HW_POOL_FREE, HW_LUN_CAP):
            grabbed[oid] = rows or []
        if rows and rows[0][0] == "__error__":
            print(f"{label} {oid}\n    出错: {rows[0][1]}")
            continue
        if not rows:
            print(f"{label} {oid}\n    (无数据)")
            continue
        hub_ok += 1
        shown = rows[:8]
        for sfx, val in shown:
            print(f"{label} {oid}\n    实例 {sfx:<10} = {val}")
        if len(rows) > len(shown):
            print(f"    ... 共 {len(rows)} 个实例(只印前 8 条)")

    if hub_ok:
        print(f"\n[结论] 华为私有 MIB 有 {hub_ok} 张表/标量有数据 —— "
              f"这是台华为 OceanStor, 平台会自动走私有 MIB 采集。")
        print("       容量字段单位是 MB; LUN 容量单位是 KB。")
        _warn_capacity_gap(grabbed.get(HW_POOL_TOTAL, []),
                           grabbed.get(HW_POOL_ALLOC, []),
                           grabbed.get(HW_POOL_FREE, []),
                           grabbed.get(HW_LUN_CAP, []))
    else:
        print("\n[结论] 华为私有 MIB 无数据。要么不是华为存储, 要么 SNMP 凭据/版本不对,")
        print("       要么上下文名称(context)没填对 —— 华为 DeviceManager 上那一栏要与这里一致。")

    print("\n--- 标准 HOST-RESOURCES-MIB (通用存储) ---")
    for label, oid in (("hrStorageType ", "1.3.6.1.2.1.25.2.3.1.2"),
                       ("hrStorageDescr", "1.3.6.1.2.1.25.2.3.1.3"),
                       ("hrStorageSize ", "1.3.6.1.2.1.25.2.3.1.5"),
                       ("hrStorageUsed ", "1.3.6.1.2.1.25.2.3.1.6")):
        rows = await _walk(engine, target, auth, ctx, oid)
        if not rows:
            print(f"{label} {oid}\n    (无数据 —— 华为存储不暴露这个表, 属正常)")
            continue
        print(f"{label} {oid}\n    共 {len(rows)} 个实例, 前 5 条:")
        for sfx, val in rows[:5]:
            print(f"      实例 {sfx:<10} = {val}")


async def main_async(args):
    engine = SnmpEngine()
    target = await UdpTransportTarget.create(
        (args.ip, args.port), timeout=args.timeout, retries=1)
    auth = _build_auth(args)
    ctx = _build_ctx(args)

    if args.v3:
        print(f"SNMPv3(USM): 用户={args.user!r}")
        print(f"  认证: {args.auth_proto:<8} 密码={'已设' if args.auth_pass else '未设(不认证)'}")
        print(f"  加密: {args.priv_proto:<8} "
              f"密码={'已设' if args.priv_pass else '未设(不加密)'}")
        print(f"  上下文名称: {args.context!r}")
        print("  ⚠ 算法和密码必须与设备 USM 用户页**逐项一致**, 差一个就完全无响应")
    else:
        print(f"SNMP {args.version}: community={args.community!r}")

    # 型号先报出来: 私有 .1.6.0 很多型号不答, sysDescr 一定答
    desc = await _get(engine, target, auth, ctx, OID_SYS_DESCR)
    if desc:
        print(f"设备自述(sysDescr): {str(desc).strip().splitlines()[0][:110]}")

    if args.sniff_lun:
        # 先列全貌(留证据), 再自动定位已用容量列(给结论)
        await dump_table(engine, target, auth, ctx, HW_LUN_TABLE, label="华为 LUN 表")
        await sniff_lun_used(engine, target, auth, ctx)
        return 0
    if args.dump_table:
        await dump_table(engine, target, auth, ctx, args.dump_table)
        return 0
    if args.storage:
        await scan_storage(engine, target, auth, ctx)
        return 0

    name = await _get(engine, target, auth, ctx, OID_SYS_NAME)
    descr = await _get(engine, target, auth, ctx, OID_SYS_DESCR)
    if name is None and descr is None:
        print(f"[错误] {args.ip} 无 SNMP 响应。逐项排查:")
        print("  1) v1/v2c: 团体名对不对(public/<自定>); 端口是不是 161")
        print("  2) v3: 用户名/认证算法/认证密码/加密算法/加密密码 五项必须与设备侧完全一致")
        print("     —— 认证算法和加密算法不匹配是最常见的失败原因(如设备是 SHA, 这里写了 MD5)")
        print("  3) 华为 OceanStor: DeviceManager 里「SNMPv1&SNMPv2c协议开关」若是关闭,")
        print("     必须用 --v3; 且「上下文名称」那一栏要与 --context 填的一致")
        print("  4) 网络: 本机到存储 161/udp 是否放通; 存储侧是否配了 SNMP 访问白名单/ACL")
        return 1
    print("=" * 78)
    print(f" 目标 {args.ip}   sysName={name}")
    print(f" sysDescr={descr}")
    print("=" * 78)

    ram_idx = None
    for label, oid in CANDIDATES:
        rows = await _walk(engine, target, auth, ctx, oid)
        print(f"\n{label}  {oid}")
        if not rows:
            print("    (无数据 / 不支持)")
            continue
        if rows and rows[0][0] == "__error__":
            print(f"    出错: {rows[0][1]}")
            continue
        nonzero = [(s, v) for s, v in rows if str(v).strip() not in ("", "0", "0.0")]
        for suffix, val in rows[:6]:
            print(f"    实例 {suffix:<12} = {val}")
        if len(rows) > 6:
            print(f"    ... 共 {len(rows)} 个实例 (只印前6条, 见下方非零汇总)")
        for suffix, val in nonzero[:12]:
            mark = ""
            try:
                if 0 < float(val) <= 100:
                    mark = "   <== 合理百分比(可能是使用率)"
            except ValueError:
                pass
            print(f"    ★ 非零 实例 {suffix:<12} = {val}{mark}")
        if len(nonzero) > 12:
            print(f"    ... 另有 {len(nonzero) - 12} 个非零实例")
        if not nonzero:
            print("    (全表 174/若干实例的值都为 0, 该列很可能不是使用率)")
        nums = []
        for _s, _v in rows:
            try:
                nums.append(float(_v))
            except ValueError:
                pass
        if nums:
            print(f"    本列汇总: 非零 {len(nonzero)} 个, 最大 {max(nums)}, 最小 {min(nums)}")
        if oid == "1.3.6.1.2.1.25.2.3.1.2":   # hrStorageType, 记下 RAM 分区索引
            for suffix, val in rows:
                if str(val).strip() == HR_STORAGE_RAM:
                    ram_idx = suffix
                    print(f"    -> RAM 分区索引 = {suffix}(hrStorageRam)")

    if ram_idx is not None:
        sz = dict(await _walk(engine, target, auth, ctx, "1.3.6.1.2.1.25.2.3.1.5"))
        us = dict(await _walk(engine, target, auth, ctx, "1.3.6.1.2.1.25.2.3.1.6"))
        try:
            s, u = float(sz[ram_idx]), float(us[ram_idx])
            if s > 0:
                print(f"\n[标准兜底] hrStorageRam 内存使用率 = {u / s * 100:.1f}%  ({u:.0f}/{s:.0f})")
        except Exception:
            pass

    print("\n" + "=" * 78)
    print(" 结论提示: 标注『合理百分比』且量级与 dis cpu/dis memory 相符的 OID,")
    print("          才是 CPU/内存使用率。把本输出贴回来即可定死平台 OID。")
    print("=" * 78)
    return 0


def main():
    ap = argparse.ArgumentParser(description="SNMP OID 诊断 (交换机 CPU/内存 + 存储)")
    ap.add_argument("--ip", required=True, help="设备 IP")
    ap.add_argument("--community", default="public", help="只读团体名, 默认 public")
    ap.add_argument("--version", default="2c", choices=["2c", "1"], help="SNMP 版本")
    # ---- SNMPv3 (USM) ----
    ap.add_argument("--v3", action="store_true",
                    help="用 SNMPv3。华为 OceanStor 的「SNMPv1&v2c协议开关」"
                         "默认关闭时只能走这条")
    ap.add_argument("--user", default="", help="v3 USM 用户名 (--v3 时必填)")
    # 别名 --auth-prob: 现场很容易手滑少打一个 t, 与其报"无法识别", 不如直接收下
    ap.add_argument("--auth-proto", "--auth-prob", dest="auth_proto",
                    default="sha", choices=sorted(AUTH_PROTOCOLS),
                    help="v3 认证算法, 必须与设备侧一致(写错必然无响应)")
    ap.add_argument("--auth-pass", default="", help="v3 认证密码")
    ap.add_argument("--priv-proto", default="aes", choices=sorted(PRIV_PROTOCOLS),
                    help="v3 加密算法, 必须与设备侧一致(写错必然无响应)")
    ap.add_argument("--priv-pass", default="", help="v3 加密密码")
    ap.add_argument("--context", default="",
                    help="v3 上下文名称(对应设备页面上那一栏), 不确定留空")
    # ---- 模式 ----
    ap.add_argument("--storage", action="store_true",
                    help="存储模式: 探华为 OceanStor 私有 MIB + 标准 hrStorage")
    ap.add_argument("--dump-table", dest="dump_table", default="",
                    help="整表列扫描: 把该 OID 下每一列都走一遍(如 LUN 表 "
                         "1.3.6.1.4.1.34774.4.1.19.9.4.1), 用来确认真实列号")
    ap.add_argument("--sniff-lun", action="store_true",
                    help="快捷方式: 扫描华为 LUN 表全部列, 找'已用容量'在哪一列")
    ap.add_argument("--port", type=int, default=161)
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()
    if args.v3 and not args.user:
        ap.error("--v3 必须同时给 --user")
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
