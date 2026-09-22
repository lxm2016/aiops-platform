#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单设备寄存器全扫 —— 定位"未知设备"的真实寄存器映射 (零依赖)。

用途
----
当一台设备能连上、但某个功能码/地址读不到时, 用它把该从站的
**FC01 线圈 / FC02 离散输入 / FC03 保持寄存器 / FC04 输入寄存器**
从 start 到 start+count 全扫一遍, 只列出**有值**的地址, 并给出映射建议。

典型场景
--------
* 烟感/水浸: 状态在 FC02 或 FC01 的某一位 (0=正常 1=报警), 不在保持寄存器。
* 精密空调: 回风温度/湿度/设定值/告警字 常在 FC03/FC04 的中高地址段。
* 只看 FC03 又恰好是开关量设备 -> 全 0, 看着像"没数据", 其实是找错寄存器区。

用法
----
    # 扫 5002 上的烟感(地址12) 全部寄存器区
    python3 sweep_device.py --host 192.168.204.71 --port 5002 --slave 12 --rtu

    # 一次扫多个从站(水浸/烟感一起): 
    python3 sweep_device.py --host 192.168.204.71 --port 5002 --slaves 9-31 --rtu

    # 精密空调(5004 地址1), 扩大地址范围
    python3 sweep_device.py --host 192.168.204.71 --port 5004 --slave 1 --rtu --count 128

    # 标准 Modbus TCP(非透传)去掉 --rtu
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_modbus import ModbusTCP  # noqa: E402

FC_COIL, FC_DISCRETE, FC_HOLDING, FC_INPUT = 1, 2, 3, 4


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


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def scan_fc(host, port, slave, fc, start, count, timeout, rtu, chunk=32):
    """分块读一个功能码区间, 返回 (ok, items)。items = [(addr, value)] 只保留非零。"""
    items = []
    connected = False
    addr = start
    while addr < start + count:
        n = min(chunk, start + count - addr)
        try:
            with ModbusTCP(host, port, slave, timeout=timeout, rtu=rtu) as m:
                val = m.read(fc, addr, n)
            connected = True
        except Exception as e:  # noqa: BLE001
            if not connected:
                return False, [], f"{type(e).__name__}: {e}"
            break
        if n == 1:
            vals = [int(val)]
        else:
            vals = [int(x) for x in val]
        for i, v in enumerate(vals):
            if v != 0:
                items.append((addr + i, v))
        addr += n
    return True, items, ""


def describe(fc):
    return {FC_COIL: "FC01 线圈", FC_DISCRETE: "FC02 离散输入",
            FC_HOLDING: "FC03 保持寄存器", FC_INPUT: "FC04 输入寄存器"}[fc]


def sweep_one(host, port, slave, start, count, timeout, rtu):
    print(f"\n{'=' * 74}")
    print(f" 从站 {slave}  @ {host}:{port}   {'RTU over TCP(透传)' if rtu else 'Modbus TCP'}")
    print("=" * 74)
    summary = {}
    for fc in (FC_HOLDING, FC_INPUT, FC_COIL, FC_DISCRETE):
        ok, items, err = scan_fc(host, port, slave, fc, start, count, timeout, rtu)
        name = describe(fc)
        if not ok:
            print(f"  [{name}] 无响应: {err[:80]}")
            continue
        if not items:
            print(f"  [{name}] {start}~{start + count - 1} 全为 0 或读不到")
            continue
        print(f"  [{name}] 非零点位:")
        for addr, v in items:
            extra = ""
            if fc in (FC_HOLDING, FC_INPUT):
                extra = f"   (×0.1={v / 10:.1f}, s16={_s16(v)})"
            else:
                extra = "   (开关量)"
            print(f"      addr {addr:<5} = {v}{extra}")
        summary[fc] = items

    # ---- 映射建议 ----
    print(f"  ── 建议 ──")
    hint = []
    hold = dict(summary.get(FC_HOLDING, []))
    if FC_COIL in summary or FC_DISCRETE in summary:
        which = "FC02离散输入" if FC_DISCRETE in summary else "FC01线圈"
        bits = (summary.get(FC_DISCRETE) or summary.get(FC_COIL))
        first_addr = bits[0][0] if bits else 0
        hint.append(f"像是**开关量设备**(烟感/水浸): 判定位在 {which} 的 addr {first_addr}, 值 1=报警/0=正常")
        hint.append(f"  平台点位: 功能码选 {which[-8:]}, 地址 {first_addr}, 数据类型 bit, 报警值 1")
    if 0 in hold and 1 in hold:
        h, t = hold[0] / 10.0, _s16(hold[1]) / 10.0
        if 0 < h <= 100 and -40 <= t <= 90:
            hint.append(f"前两个寄存器像温湿度: 0=湿度 {h:.1f}%, 1=温度 {t:.1f}℃ (注意 0=湿度!)")
    if not hint:
        hint.append("未识别出常见模式。把上面的非零点位贴回来, 我据此配映射。")
    for h in hint:
        print(f"    · {h}")


def main():
    ap = argparse.ArgumentParser(description="单设备寄存器全扫(定位未知设备映射)")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--slave", type=int, default=1, help="单个从站地址")
    ap.add_argument("--slaves", default="", help="多个从站, 如 9-31(与 --slave 二选一)")
    ap.add_argument("--rtu", action="store_true", default=True)
    ap.add_argument("--tcp", action="store_true")
    ap.add_argument("--start", type=int, default=0, help="起始地址, 默认 0")
    ap.add_argument("--count", type=int, default=32, help="扫描个数, 默认 32")
    ap.add_argument("--timeout", type=float, default=1.0)
    args = ap.parse_args()

    rtu = not args.tcp
    slaves = parse_range(args.slaves) if args.slaves else [args.slave]
    print(f"单设备寄存器全扫   目标 {args.host}:{args.port}   从站 {slaves}   "
          f"{'RTU透传' if rtu else 'Modbus TCP'}   地址 {args.start}~{args.start + args.count - 1}")
    for s in slaves:
        sweep_one(args.host, args.port, s, args.start, args.count, args.timeout, rtu)


if __name__ == "__main__":
    main()
