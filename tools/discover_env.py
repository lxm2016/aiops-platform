#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动环设备批量发现 + 自动建档 (零依赖, 仅标准库)。

一条命令把串口服务器上所有端口 × 所有从站扫一遍, 列出每台设备的实时读数,
并可直接生成 / 写入平台设备表。用于「把某个院区的监测一次性补齐」。

为什么需要它
------------
手动一台台加设备要反复试 从站地址/寄存器/波特率, 串口服务器有 8 个口、
每个口的总线上可能挂十几台温湿度探头 —— 人工配又慢又容易漏。

寄存器约定(现场实测, 别改!)
----------------------------
    保持寄存器 0x0000 = 湿度 ×10 (u16)
    保持寄存器 0x0001 = 温度 ×10 (s16, 有符号)
**0=湿度、1=温度**, 是反直觉的。配成 0=温度 会把 60% 湿度显示成 60℃。

用法
----
    # 扫南院区串口服务器的 8 个口 × 从站 1-16, 只列出(不改任何东西)
    python3 discover_env.py --host 192.168.204.71 --ports 5001-5008 --slaves 1-16 --rtu

    # 确认无误后写入平台(自动跳过已存在的 IP+端口+从站)
    python3 discover_env.py --host 192.168.204.71 --ports 5001-5008 --slaves 1-16 \
            --rtu --enroll --cluster 南院区

    # 标准 Modbus TCP(非透传)网关: 去掉 --rtu
    python3 discover_env.py --host 172.16.0.238 --ports 5305 --slaves 1 --category ups

    # 导出 JSON 自己核对
    python3 discover_env.py --host 192.168.204.71 --ports 5001 --slaves 1-16 --rtu --out found.json

参数
----
--host       串口服务器 IP(必填)
--ports      端口范围/列表, 如 5001-5008 或 5001,5002(必填)
--slaves     从站地址范围, 默认 1-16
--rtu        用裸 RTU 帧(透传模式的串口服务器必须加; 默认就是这个)
--tcp        用标准 Modbus TCP 帧(与 --rtu 二选一)
--category   设备类型, 默认 temp_humidity(决定模板点位)
--cluster    所属院区/集群名, 默认 南院区
--prefix     设备名前缀, 默认 "<cluster>_温湿度"
--protocol   写入平台时用的协议, 默认 modbus_rtu
--enroll     真正写入平台(不加则只列出)
--api-url    平台地址, 默认 http://127.0.0.1:8080
--force      已存在的从站也重新建(会产生重名对象, 一般不需要)
--out        把发现的设备导出为 JSON
--timeout    单次读取超时秒数, 默认 0.6(扫描要快, 别太长)
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_modbus import ModbusTCP  # noqa: E402  复用已验证的 RTU/TCP 收发实现

FC_HOLDING = 3


def parse_range(s):
    """把 "1-16" / "1,3,5" / "5001-5008" 解析成整数列表。"""
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
    """把 16 位原码按有符号解释(零下温度用)。"""
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def read_pair(host, port, slave, timeout, rtu):
    """读保持寄存器 0 号起 2 个。返回 (raw0, raw1) 或 None(超时/异常)。"""
    try:
        with ModbusTCP(host, port, slave, timeout=timeout, rtu=rtu) as m:
            val = m.read(FC_HOLDING, 0, 2)
            if isinstance(val, list) and len(val) >= 2:
                return int(val[0]), int(val[1])
    except Exception:
        return None
    return None


def decode(raw0, raw1):
    """按现场实测约定解码: 0=湿度, 1=温度。"""
    return raw0 / 10.0, s16(raw1) / 10.0


def get_existing(api_url, timeout=5):
    """拉现有设备列表, 返回 {(ip,port,slave_id): name}。失败返回空 dict。"""
    try:
        req = urllib.request.Request(
            f"{api_url.rstrip('/')}/api/env/devices", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {(d.get("ip"), d.get("port"), d.get("slave_id")): d.get("name") for d in data}
    except Exception as e:
        print(f"  [提示] 读不到现有设备列表({type(e).__name__}), 跳过去重判断")
        return {}


def enroll(api_url, devices, timeout=15):
    """把设备列表 POST 给平台的批量接口。"""
    payload = json.dumps(devices).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/env/devices/batch",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description="动环设备批量发现 + 自动建档")
    ap.add_argument("--host", required=True)
    ap.add_argument("--ports", required=True, help="如 5001-5008 或 5001,5002")
    ap.add_argument("--slaves", default="1-16")
    ap.add_argument("--rtu", action="store_true", default=True, help="裸 RTU 帧(透传, 默认)")
    ap.add_argument("--tcp", action="store_true", help="标准 Modbus TCP 帧")
    ap.add_argument("--category", default="temp_humidity")
    ap.add_argument("--cluster", default="南院区")
    ap.add_argument("--prefix", default="", help="设备名前缀, 默认 <院区>_温湿度")
    ap.add_argument("--protocol", default="modbus_rtu")
    ap.add_argument("--enroll", action="store_true")
    ap.add_argument("--api-url", default="http://127.0.0.1:8080")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default="", help="导出发现结果为 JSON")
    ap.add_argument("--timeout", type=float, default=0.6)
    args = ap.parse_args()

    rtu = not args.tcp            # 透传是现场主流, 默认 RTU
    ports = parse_range(args.ports)
    slaves = parse_range(args.slaves)
    prefix = args.prefix or f"{args.cluster}_温湿度"
    mode = "RTU over TCP(透传)" if rtu else "Modbus TCP"

    print("=" * 74)
    print(f" 动环设备批量发现   目标 {args.host}")
    print(f" 方式 {mode}   端口 {args.ports}   从站 {args.slaves}")
    print(f" 类型 {args.category}   院区 {args.cluster}")
    print(" 寄存器约定(现场实测): 0x0000=湿度×10, 0x0001=温度×10")
    print("=" * 74)

    existing = get_existing(args.api_url) if args.enroll else {}

    found, skipped_exist = [], []
    for port in ports:
        rows = []
        for slave in slaves:
            pair = read_pair(args.host, port, slave, args.timeout, rtu)
            if pair is None:
                continue
            raw0, raw1 = pair
            hum, temp = decode(raw0, raw1)
            rows.append((slave, raw0, raw1, hum, temp))
        print(f"\n---- {args.host}:{port} ----")
        if not rows:
            print("  无响应")
            continue
        print(f"  {'从站':<6}{'湿度%':>9}{'温度℃':>9}   备注")
        for slave, raw0, raw1, hum, temp in rows:
            name = f"{prefix}_p{port}_s{slave}"
            dup = existing.get((args.host, port, slave))
            if dup:
                note = f"已在平台: {dup}"
                skipped_exist.append(slave)
            else:
                note = "新增"
            print(f"  {slave:<6}{hum:>9.1f}{temp:>9.1f}   {note}")
            if not dup or args.force:
                if dup and args.force:
                    name = f"{name}_new"
                found.append({
                    "name": name,
                    "category": args.category,
                    "protocol": args.protocol,
                    "ip": args.host,
                    "port": port,
                    "slave_id": slave,
                    "location": "",
                    "cluster": args.cluster,
                    "resource_group": "",
                    "poll_interval": 60,
                    "enabled": True,
                    "remark": f"discover_env 自动发现 {args.host}:{port} 从站{slave}"
                              f"(发现时读数 湿度{hum:.1f}% 温度{temp:.1f}℃)",
                })

    print("\n" + "=" * 74)
    print(f"发现 {len(found)} 台新设备" + (f"，另有 {len(skipped_exist)} 台已在平台" if skipped_exist else ""))
    print("=" * 74)

    if args.out and found:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(found, f, ensure_ascii=False, indent=2)
        print(f"已导出 -> {args.out}")

    if not args.enroll:
        if found:
            print("\n这是预演(dry-run)。确认无误后加 --enroll 写入平台。")
            print("写入后平台会自动套用模板点位(温湿度: 0=湿度/1=温度, 温度 s16)。")
        return

    if not found:
        print("没有新设备需要写入。")
        return
    try:
        res = enroll(args.api_url, found)
        print("\n写入结果:")
        print(f"  新建 {len(res.get('created', []))} 台")
        for n in res.get("created", []):
            print(f"    + {n}")
        if res.get("skipped"):
            print(f"  跳过 {len(res['skipped'])} 台")
        print("\n提示: 设备名里的 p<端口>s<从站> 是临时编号, 去机房确认房间后")
        print("      在界面改成名如「南院区_温湿度_3(操作间北侧)」即可。")
    except urllib.error.HTTPError as e:
        print(f"\n[错误] 写入失败 HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[错误] 写入失败: {type(e).__name__}: {e}")
        print("      确认平台在运行(--api-url), 或改用 --out 导出后手动导入。")
        sys.exit(1)


if __name__ == "__main__":
    main()
