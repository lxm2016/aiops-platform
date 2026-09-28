#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存储容量采集探针 —— 定位"已用TB / 使用率 统计不出来"的真凶。

背景
----
华为 OceanStor(Dorado 全闪等)不暴露标准 HOST-RESOURCES-MIB, 容量只能走两条路:

  1) 华为私有 MIB (34774.4.1): 平台内置。能拿到**存储池**总/可用(MB) -> 由此算
     阵列级"已用TB/使用率"; 但 LUN 表(11列)里**没有"已用容量"列**, 所以
     **卷/LUN 级已用**在 SNMP 下永远算不出(界面显示 '-', 这是 MIB 限制不是 bug)。
  2) SMI-S / WBEM (5988/5989, pywbem): 走 CIM_StorageVolume.ConsumableBlocks,
     才能补齐**卷级已用容量**。需要: 存储管理账号 + 放通端口 + 后端装 pywbem。

本探针在内网服务器上跑, 把三件事一次性查清并打印结论:
  · SNMP 到底能不能拿到存储池(阵列已用) / 整机能不能拿到;
  · SMI-S(若给了账号密码)能不能拿到卷级 ConsumableBlocks(卷已用);
  · 该装什么、该放通什么端口、前端该选哪个协议。

依赖
----
需要 pysnmp (后端 venv 已装)。SMI-S 探测需要 pywbem (未装会明确提示怎么装)。
请用**后端 venv 的解释器**运行, 否则会找不到这些库:

    backend/venv/bin/python tools/probe_storage.py --ip 10.0.0.5 --community public
    backend/venv/bin/python tools/probe_storage.py --ip 10.0.0.5 \
        --v3-user snmpuser --v3-auth-pass xxxx --v3-priv-pass yyyy
    backend/venv/bin/python tools/probe_storage.py --ip 10.0.0.5 \
        --smis-user admin --smis-pass xxxx

把输出贴回来, 据此把平台配置定死。
"""
import argparse
import asyncio
import os
import sys

# 把 backend 目录加入导入路径, 这样才能 import app.*
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "backend"))

from app.services.storage_service import (  # noqa: E402
    collect_storage,
    collect_huawei_snmp,
    HW_POOL,
    HW_CAP_TOTAL,
    HW_CAP_USED,
)
from app.services.snmp_service import snmp_walk, snmp_get  # noqa: E402


def _cred(args):
    """组装 SNMP 凭据(与平台后端 _snmp_cred 一致)。"""
    if args.v3_user:
        return {
            "user": args.v3_user,
            "auth_proto": args.v3_auth_proto,
            "auth_pass": args.v3_auth_pass,
            "priv_proto": args.v3_priv_proto,
            "priv_pass": args.v3_priv_pass,
            "context": args.v3_context,
        }
    return args.community


def _brief(d):
    return (
        f"reachable={d.get('reachable')} source={d.get('source')} "
        f"capacity_tb={d.get('capacity_tb')} used_tb={d.get('used_tb')} "
        f"used_percent={d.get('used_percent')}"
    )


async def _probe_snmp(ip, cred, version):
    print("=" * 72)
    print("【1】SNMP 采集（华为私有 MIB 优先，识别失败退回标准 HOST-RESOURCES）")
    print("=" * 72)
    snmp = await collect_storage("snmp", ip, cred, version)
    print("  collect_storage 汇总: " + _brief(snmp))
    if snmp.get("error"):
        print("  error:", snmp["error"])
    det = snmp.get("details", {}) or {}
    pools = det.get("pools", []) or []
    vols = det.get("volumes", []) or []
    print(f"  存储池数={len(pools)}  卷/LUN数={len(vols)}")
    if pools:
        p = pools[0]
        print(f"  样例池: {p.get('name')}  size_tb={p.get('size_tb')}  "
              f"used_tb={p.get('used_tb')}  used%={p.get('used_percent')}")
    vol_used = [v for v in vols if v.get("used_tb") is not None]
    print(f"  卷中能算出已用的={len(vol_used)}/{len(vols)} "
          f"（华为 SNMP LUN 表通常无已用列 -> 这里应为 0）")

    # 直接探测华为私有 MIB 存储池表, 给出原始证据
    print("  -- 原始私有 MIB 探测（存储池表 .23.4.2.1）--")
    for oid, label in (
        (f"{HW_POOL}.2", "池名"),
        (f"{HW_POOL}.7", "池总(MB)"),
        (f"{HW_POOL}.9", "池可用(MB)"),
        (f"{HW_POOL}.8", "池已分配(MB)"),
    ):
        try:
            vals = await snmp_walk(ip, cred, oid, version)
        except Exception as e:  # noqa: BLE001
            print(f"    {label}: 探测异常 {e}")
            vals = None
        if vals:
            sample = list(vals.items())[:3]
            for k, v in sample:
                print(f"    {label} {k} = {v}")
        else:
            print(f"    {label}: 无返回（设备未答此 OID）")
    # 整机标量兜底
    try:
        t = await snmp_get(ip, cred, HW_CAP_TOTAL, version)
        u = await snmp_get(ip, cred, HW_CAP_USED, version)
        print(f"  -- 整机标量兜底: HW_CAP_TOTAL={t}  HW_CAP_USED={u} --")
    except Exception as e:  # noqa: BLE001
        print(f"  -- 整机标量探测异常 {e} --")

    return snmp


async def _probe_smis(ip, user, password):
    print()
    print("=" * 72)
    print("【2】SMI-S / WBEM 采集（卷级已用容量走 CIM_StorageVolume.ConsumableBlocks）")
    print("=" * 72)
    smis = await collect_storage("smi-s", ip, "", "2c", user, password)
    print("  collect_storage 汇总: " + _brief(smis))
    if smis.get("error"):
        print("  error:", smis["error"])
        print("  >> 多半是后端没装 pywbem，或 5988/5989 端口未放通 / 账号密码不对。")
        return smis
    det = smis.get("details", {}) or {}
    vols = det.get("volumes", []) or []
    vol_used = [v for v in vols if v.get("used_tb") is not None]
    print(f"  卷数={len(vols)}  能算出已用的={len(vol_used)}")
    for v in vols[:3]:
        print(f"    卷: {v.get('name')}  size_tb={v.get('size_tb')}  "
              f"used_tb={v.get('used_tb')}  used%={v.get('used_percent')}")
    return smis


async def main():
    ap = argparse.ArgumentParser(description="存储容量采集探针")
    ap.add_argument("--ip", required=True, help="存储管理IP")
    ap.add_argument("--community", default="public", help="SNMP团体名(v2c)")
    ap.add_argument("--version", default="2c", help="SNMP版本(v1/v2c/3)，v3时忽略--community")
    # SNMPv3 USM
    ap.add_argument("--v3-user", default="", help="SNMPv3 USM 用户名(华为通常必填)")
    ap.add_argument("--v3-auth-proto", default="sha")
    ap.add_argument("--v3-auth-pass", default="")
    ap.add_argument("--v3-priv-proto", default="aes")
    ap.add_argument("--v3-priv-pass", default="")
    ap.add_argument("--v3-context", default="")
    # SMI-S
    ap.add_argument("--smis-user", default="", help="SMI-S/WBEM 管理账号(走卷已用容量时填)")
    ap.add_argument("--smis-pass", default="")
    args = ap.parse_args()

    version = "3" if args.v3_user else args.version
    cred = _cred(args)

    print(f"\n目标: {args.ip}  SNMP版本={'v3(USM)' if args.v3_user else version}\n")

    snmp = await _probe_snmp(args.ip, cred, version)

    if args.smis_user:
        await _probe_smis(args.ip, args.smis_user, args.smis_pass)

    print()
    print("=" * 72)
    print("【结论】")
    print("=" * 72)
    snmp_cap = snmp.get("capacity_tb") or 0
    if snmp.get("reachable") and snmp_cap:
        print(f"  ✓ 阵列级 已用TB/使用率 可从 SNMP 算出："
              f"已用 {snmp.get('used_tb')}TB / 共 {snmp_cap}TB，"
              f"使用率 {snmp.get('used_percent')}%。")
        print("    前端『存储设备管理』卡片会自动显示；采集前请确认设备已填 SNMP 凭据且能连通。")
    else:
        print("  ✗ 阵列级 已用TB/使用率 当前从 SNMP 算不出（capacity_tb=0）。")
        if args.v3_user:
            print("    - 你用的是 v3，请确认 USM 用户名/认证/加密/上下文与设备侧一致；")
        else:
            print("    - 若是华为且 v2c 失败，请在设备 DeviceManager 打开『SNMPv1&SNMPv2c开关』，")
            print("      或改用 v3（--v3-user 等参数）；华为默认只开 v3。")
        print("    - 也可把协议改成 smi-s 试（需填管理账号、放通 5988/5989）。")

    # 卷级已用
    snmp_vols = (snmp.get("details", {}) or {}).get("volumes", []) or []
    snmp_vol_used = [v for v in snmp_vols if v.get("used_tb") is not None]
    if not snmp_vol_used:
        print("  ✗ 卷/LUN 级 已用TB/使用率 在 SNMP 下算不出（华为 LUN 表无已用列，属 MIB 限制）。")
        if args.smis_user:
            print("    - 已用 --smis-user 探测 SMI-S，看上方【2】结果；能拿到则前端明细『卷/LUN』会显示已用。")
        else:
            print("    - 想拿到卷级已用，请加 --smis-user/--smis-pass 探测 SMI-S（需后端装 pywbem、放通 5988/5989）。")
            print("    - 之后在平台把该设备协议改为 smi-s，或在 SNMP 凭证之外补充 SMI-S 账号后重新采集。")
    else:
        print(f"  ✓ 卷级 已用 可从 SNMP 算出（{len(snmp_vol_used)}/{len(snmp_vols)} 个卷有值）。")

    print("\n提示：本探针复用平台后端同一套采集代码，结果即平台采集的真实表现。")


if __name__ == "__main__":
    asyncio.run(main())
