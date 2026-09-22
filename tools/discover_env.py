#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动环设备批量发现 + 自动判类型 + 自动建档 (零依赖, 仅标准库)。

一条命令把串口服务器上所有端口 × 所有从站扫一遍, 对每个从站**探测多个功能码**
生成"指纹", 据此判断它是温湿度 / 开关量(烟感/水浸) / 其它, 列出实时读数,
并可直接写入平台设备表。

为什么要多功能码指纹
--------------------
Modbus 本身不带"设备类型"字段。温湿度走保持寄存器(FC03), 烟感/水浸走
开关量(FC02 离散输入 / FC01 线圈)。只看 FC03 会把烟感读成全 0 并误判。
本工具对每个从站依次试 FC03 / FC02 / FC01, 用"哪个区有数据、什么值"
来推断类型, 并且**全 0 的从站不会建档**(避免把幽灵响应建成设备)。

寄存器约定(现场实测, 别改!)
----------------------------
    温湿度: 保持寄存器 0x0000 = 湿度 ×10 (u16), 0x0001 = 温度 ×10 (s16)
            **0=湿度、1=温度**, 是反直觉的! 配成 0=温度 会把 60% 湿度显示成 60℃。
    烟感/水浸: 开关量 0=正常, 1=报警。

关于"同一个端口上烟感和水浸怎么区分"
------------------------------------
协议层面**无法**区分 —— 两者都是 0/1 开关量, 读出来一模一样。必须靠:
  ① 从站地址规划(老平台就是这样: 比如 9~12=烟感、13~16=水浸), 或
  ② 安装位置(拿读数去机房对)。
所以本工具支持 `--types 9=smoke,10=smoke,13=water` 显式指定每个从站的类型;
没指定的开关量设备默认按 `--di-category`(默认 smoke) 建档, 并会**明确提示**
需要你去界面按实际位置核对。

用法
----
    # 1) 只探测、只列指纹(不改任何东西) —— 先看清每个从站是什么
    python3 discover_env.py --host 192.168.204.71 --ports 5006 --slaves 1-16 --rtu

    # 2) 温湿度总线, 一键建档(自动跳过已在平台的设备)
    python3 discover_env.py --host 192.168.204.71 --ports 5001 --slaves 1-16 \
            --rtu --enroll --cluster 南院区

    # 3) 烟感/水浸混合总线: 显式指定类型再建档
    python3 discover_env.py --host 192.168.204.71 --ports 5002 --slaves 9-16 \
            --rtu --enroll --cluster 南院区 --types 9=smoke,10=smoke,13=water,14=water

    # 4) 总部(Advantech iCom, 端口 5300 起的透传口, 与南院区一样用 RTU)
    python3 discover_env.py --host 172.16.0.238 --ports 5300-5307 --slaves 1-16 --rtu

参数说明见 -h。
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_modbus import ModbusTCP  # noqa: E402  复用已验证的 RTU/TCP 收发实现

# 功能码
FC_COIL, FC_DISCRETE, FC_HOLDING, FC_INPUT = 1, 2, 3, 4
# 烟雾/水浸等开关量从 0 号位开始看的位数
DI_COUNT = 8


def parse_range(s):
    out = []
    for part in str(s).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def _read(host, port, slave, fc, addr, count, timeout, rtu):
    """读一段寄存器/位, 返回列表; 失败返回 None。"""
    try:
        with ModbusTCP(host, port, slave, timeout=timeout, rtu=rtu) as m:
            val = m.read(fc, addr, count)
            if count == 1:
                return [int(val)]
            return [int(x) for x in val]
    except Exception:
        return None


def fingerprint(host, port, slave, timeout, rtu, count):
    """探测一个从站, 返回 (kind, detail, note)。

    kind ∈ temp_humidity / di / other / none
    """
    hold = _read(host, port, slave, FC_HOLDING, 0, count, timeout, rtu)
    di = _read(host, port, slave, FC_DISCRETE, 0, DI_COUNT, timeout, rtu)
    coil = _read(host, port, slave, FC_COIL, 0, DI_COUNT, timeout, rtu)

    # --- 温湿度: FC03 的 0/1 号是合理的湿度/温度 ---
    if hold and len(hold) >= 2:
        h_raw, t_raw = hold[0], hold[1]
        hum, temp = h_raw / 10.0, s16(t_raw) / 10.0
        if 0 < hum <= 100 and -40 <= temp <= 90:
            return ("temp_humidity", (h_raw, t_raw),
                    f"湿度 {hum:.1f}%  温度 {temp:.1f}℃")

    # --- 开关量(烟感/水浸等): FC02 或 FC01 有位数据 ---
    for name, bits in (("离散输入FC02", di), ("线圈FC01", coil)):
        if bits is not None:
            ones = [i for i, b in enumerate(bits) if b]
            return ("di", (name, bits),
                    f"{name} 位值={bits}" + (f" 置位位={ones}" if ones else " (全0=正常)"))

    # --- 其它: FC03 有非零数据但不像温湿度 ---
    if hold and any(v != 0 for v in hold):
        nz = {i: v for i, v in enumerate(hold) if v}
        return ("other", (hold,), f"FC03 非零寄存器 {nz}")

    return ("none", None, "各功能码均无有效数据(疑似幽灵响应, 不建档)")


def get_existing(api_url, timeout=5):
    try:
        req = urllib.request.Request(
            f"{api_url.rstrip('/')}/api/env/devices",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {(d.get("ip"), d.get("port"), d.get("slave_id")): d.get("name") for d in data}
    except Exception as e:  # noqa: BLE001
        print(f"  [提示] 读不到现有设备列表({type(e).__name__}), 跳过去重判断")
        return {}


def enroll(api_url, devices, timeout=15):
    payload = json.dumps(devices).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/env/devices/batch", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


NAME_SUFFIX = {"temp_humidity": "温湿度", "smoke": "烟感", "water": "水浸",
               "power": "配电", "door": "门禁", "other": "设备", "ups": "UPS"}


def main():
    ap = argparse.ArgumentParser(description="动环设备批量发现 + 自动判类型 + 建档")
    ap.add_argument("--host", required=True)
    ap.add_argument("--ports", required=True)
    ap.add_argument("--slaves", default="1-16")
    ap.add_argument("--rtu", action="store_true", default=True)
    ap.add_argument("--tcp", action="store_true")
    ap.add_argument("--cluster", default="南院区")
    ap.add_argument("--category", default="", help="强制所有新设备用此类型(默认按指纹自动判)")
    ap.add_argument("--di-category", default="smoke",
                    choices=["smoke", "water"], help="开关量设备默认类型, 默认 smoke")
    ap.add_argument("--types", default="",
                    help='指定个别从站类型, 如 "9=smoke,13=water"(应对同端口烟感/水浸混挂)')
    ap.add_argument("--protocol", default="modbus_rtu")
    ap.add_argument("--enroll", action="store_true")
    ap.add_argument("--api-url", default="http://127.0.0.1:8080")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--count", type=int, default=8, help="FC03 扫描寄存器个数, 默认 8")
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=0.8)
    args = ap.parse_args()

    rtu = not args.tcp
    ports = parse_range(args.ports)
    slaves = parse_range(args.slaves)
    type_map = {}
    for kv in args.types.replace(" ", "").split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            type_map[int(k)] = v

    mode = "RTU over TCP(透传)" if rtu else "Modbus TCP"
    print("=" * 78)
    print(f" 动环设备批量发现   {args.host}   {mode}")
    print(f" 端口 {args.ports}   从站 {args.slaves}   院区 {args.cluster}")
    print(" 指纹: FC03保持/FC02离散/FC01线圈; 温湿度约定 0=湿度 1=温度; 全0不建档")
    print("=" * 78)

    existing = get_existing(args.api_url) if args.enroll else {}
    found, skipped, empty = [], [], []

    for port in ports:
        print(f"\n---- {args.host}:{port} ----")
        print(f"  {'从站':<5}{'判定':<14}读数/指纹")
        any_row = False
        for slave in slaves:
            kind, detail, note = fingerprint(args.host, port, slave, args.timeout, rtu, args.count)
            if kind == "none":
                continue
            any_row = True
            # 类型决策: --types 指定 > --category 强制 > 指纹推断
            if slave in type_map:
                cat, how = type_map[slave], "指定"
            elif args.category:
                cat, how = args.category, "强制"
            elif kind == "temp_humidity":
                cat, how = "temp_humidity", "指纹"
            elif kind == "di":
                cat, how = args.di_category, "指纹(开关量默认)"
            else:
                cat, how = "other", "指纹"
            dup = existing.get((args.host, port, slave))
            flag = f"已在平台: {dup}" if dup else "新增"
            print(f"  {slave:<5}{cat + '(' + how + ')':<14}{note}   [{flag}]")
            if dup and not args.force:
                skipped.append(slave)
                continue
            suffix = NAME_SUFFIX.get(cat, cat)
            name = f"{args.cluster}_{suffix}_p{port}_s{slave}"
            found.append({
                "name": name, "category": cat, "protocol": args.protocol,
                "ip": args.host, "port": port, "slave_id": slave,
                "location": "", "cluster": args.cluster, "resource_group": "",
                "poll_interval": 60, "enabled": True,
                "remark": f"discover_env 自动发现 {args.host}:{port} 从站{slave}: {note}",
            })
        if not any_row:
            empty.append(port)
            print("  无响应")

    print("\n" + "=" * 78)
    print(f"可建档 {len(found)} 台" + (f"，已在平台跳过 {len(skipped)} 台" if skipped else "")
          + (f"，无数据端口 {empty}" if empty else ""))
    print("=" * 78)

    if any(d["category"] in ("smoke", "water") for d in found):
        print("\n⚠ 注意: 开关量设备(烟感/水浸)协议层无法自动区分, 已按默认值建档。")
        print("  请到界面按【安装位置】核对类型, 或下次用 --types 9=smoke,13=water 精确指定。")

    if args.out and found:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(found, f, ensure_ascii=False, indent=2)
        print(f"已导出 -> {args.out}")

    if not args.enroll:
        if found:
            print("\n这是预演(dry-run)。确认无误后加 --enroll 写入平台。")
        return
    if not found:
        print("没有需要写入的设备。")
        return
    try:
        res = enroll(args.api_url, found)
        print(f"\n写入结果: 新建 {len(res.get('created', []))} 台")
        for n in res.get("created", []):
            print(f"    + {n}")
        if res.get("skipped"):
            print(f"  平台侧跳过 {len(res['skipped'])} 台")
        print("\n提示: 设备名里 p<端口>s<从站> 是临时编号, 请按实际房间改名并核对类型。")
    except urllib.error.HTTPError as e:
        print(f"\n[错误] 写入失败 HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"\n[错误] 写入失败: {type(e).__name__}: {e}")
        print("      确认平台在运行(--api-url), 或改用 --out 导出后手动导入。")
        sys.exit(1)


if __name__ == "__main__":
    main()
