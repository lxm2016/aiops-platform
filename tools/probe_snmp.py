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
    )
except Exception as e:  # noqa: BLE001
    print(f"[错误] 无法导入 pysnmp: {e}")
    print("请用后端虚拟环境的解释器运行, 例如:")
    print("  /opt/aiops-deploy/backend/venv/bin/python probe_snmp.py --ip <交换机IP>")
    sys.exit(1)

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


async def _get(engine, target, community, oid, version):
    try:
        err_ind, err_stat, _idx, vbs = await get_cmd(
            engine, community, target, ContextData(),
            ObjectType(ObjectIdentity(oid)), lookupMib=False)
        if err_ind or err_stat:
            return None
        return str(vbs[0][1])
    except Exception:
        return None


async def _walk(engine, target, community, oid, version):
    """返回 [(实例后缀, 值字符串)]。"""
    out = []
    try:
        gen = walk_cmd(engine, community, target, ContextData(),
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


async def main_async(args):
    engine = SnmpEngine()
    target = await UdpTransportTarget.create(
        (args.ip, args.port), timeout=args.timeout, retries=1)
    community = CommunityData(args.community, mpModel=_mp(args.version))

    name = await _get(engine, target, community, OID_SYS_NAME, args.version)
    descr = await _get(engine, target, community, OID_SYS_DESCR, args.version)
    if name is None and descr is None:
        print(f"[错误] {args.ip} 无 SNMP 响应 (团体名/版本/网络?). 团体名试: public / <你的只读团体名>")
        return 1
    print("=" * 78)
    print(f" 目标 {args.ip}   sysName={name}")
    print(f" sysDescr={descr}")
    print("=" * 78)

    ram_idx = None
    for label, oid in CANDIDATES:
        rows = await _walk(engine, target, community, oid, args.version)
        print(f"\n{label}  {oid}")
        if not rows:
            print("    (无数据 / 不支持)")
            continue
        if rows and rows[0][0] == "__error__":
            print(f"    出错: {rows[0][1]}")
            continue
        for suffix, val in rows[:12]:
            mark = ""
            try:
                f = float(val)
                if 0 < f <= 100:
                    mark = "   <== 合理百分比(可能是使用率)"
            except ValueError:
                pass
            print(f"    实例 {suffix:<12} = {val}{mark}")
        if len(rows) > 12:
            print(f"    ... 共 {len(rows)} 个实例")
        if oid == "1.3.6.1.2.1.25.2.3.1.2":   # hrStorageType, 记下 RAM 分区索引
            for suffix, val in rows:
                if str(val).strip() == HR_STORAGE_RAM:
                    ram_idx = suffix
                    print(f"    -> RAM 分区索引 = {suffix}(hrStorageRam)")

    if ram_idx is not None:
        sz = dict(await _walk(engine, target, community, "1.3.6.1.2.1.25.2.3.1.5", args.version))
        us = dict(await _walk(engine, target, community, "1.3.6.1.2.1.25.2.3.1.6", args.version))
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
    ap = argparse.ArgumentParser(description="SNMP CPU/内存 OID 诊断")
    ap.add_argument("--ip", required=True, help="交换机 IP")
    ap.add_argument("--community", default="public", help="只读团体名, 默认 public")
    ap.add_argument("--version", default="2c", choices=["2c", "1"], help="SNMP 版本")
    ap.add_argument("--port", type=int, default=161)
    ap.add_argument("--timeout", type=float, default=3.0)
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
