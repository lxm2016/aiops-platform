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

    # 多个从站返回同一个可疑值? 抓原始帧定性(看应答从站字节/CRC/尾随字节)
    python3 sweep_device.py --host 192.168.204.71 --port 5002 --slave 12 --rtu --raw

    # 数值到底是"活的"还是"死的"? 连读 10 次看哪些地址会变
    python3 sweep_device.py --host 192.168.204.71 --port 5002 --slave 12 --rtu --watch 10

    # 怀疑还有别的主机(原监控平台)在抢这条 485? 什么都不发, 纯听 15 秒
    python3 sweep_device.py --host 192.168.204.71 --port 5002 --rtu --listen 15
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_modbus import (  # noqa: E402
    ModbusTCP, explain_frame, split_rtu_frames,
)

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
                # 每段的首块先排空缓冲: 透传口若被别的主机轮询过, 会残留陈旧应答帧,
                # 不吸干就会"问 12 拿到 16 的帧"。只排首块, 兼顾速度与正确性。
                if addr == start:
                    m.drain()
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


def probe_resp_slave(host, port, slave, timeout, rtu):
    """抓一帧, 取应答里的从站字节。用来判断"回话的是不是你问的那台"。

    返回 (应答从站, 请求从站) ; 读不到返回 (None, slave)。
    """
    try:
        with ModbusTCP(host, port, slave, timeout=timeout, rtu=rtu) as m:
            m.drain()          # 必须排空, 否则读到的是别人留下的陈旧帧(首字节不是自己的)
            req, resp = m.raw_exchange(FC_HOLDING, 0, 8, settle=1.0)
        if not resp:
            return None, slave
        return resp[0], req[0]
    except Exception:  # noqa: BLE001
        return None, slave


def sweep_one(host, port, slave, start, count, timeout, rtu):
    print(f"\n{'=' * 74}")
    print(f" 从站 {slave}  @ {host}:{port}   {'RTU over TCP(透传)' if rtu else 'Modbus TCP'}")
    print("=" * 74)
    summary = {}
    resp_slave = None
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
        v0, v1 = hold[0] / 10.0, _s16(hold[1]) / 10.0
        # 关键判据: 机房温度不可能超过 ~45℃, 谁大谁就只能是湿度。
        # 现场两种顺序都见过: 南院区独立探头是 0=湿度/1=温度,
        # 海瑞弗精密空调是 0=温度/1=湿度 —— 靠这条物理判据区分, 不能想当然。
        if v0 > 45 and v1 <= 45:
            hint.append(f"温湿度: **0=湿度 {v0:.1f}%, 1=温度 {v1:.1f}℃**"
                        f"  (依据: {v0:.1f} 超过45℃, 不可能是温度)")
        elif v1 > 45 and v0 <= 45:
            hint.append(f"温湿度: **0=温度 {v0:.1f}℃, 1=湿度 {v1:.1f}%**"
                        f"  (依据: {v1:.1f} 超过45℃, 不可能是温度)")
        elif 0 < v0 <= 100 and 0 < v1 <= 100:
            hint.append(f"温湿度(顺序无法从数值判定): 可能 0=温度 {v0:.1f}℃/1=湿度 {v1:.1f}%, "
                        f"也可能 0=湿度 {v0:.1f}%/1=温度 {v1:.1f}℃")
            hint.append("  确认办法: 隔半小时再读一次 —— 几乎不动的是温度, 会波动的是湿度")
    if not hint:
        hint.append("未识别出常见模式。把上面的非零点位贴回来, 我据此配映射。")
    for h in hint:
        print(f"    · {h}")

    # ---- 应答从站字节校验: 回话的是不是这台? ----
    if summary and rtu:
        rs, qs = probe_resp_slave(host, port, slave, timeout, rtu)
        resp_slave = rs
        if rs is None:
            print(f"  ⚠ 复核读不到应答(总线不稳定)")
        elif rs != qs:
            print(f"  ⚠ 应答从站字节={rs}, 但请求的是 {qs} "
                  f"—— 回话的不是这台, 是总线上的另一台在代答!")
        else:
            print(f"  ✓ 应答从站字节={rs}, 与请求一致")
    return summary, resp_slave


def listen_only(host, port, seconds, rtu):
    """连上后什么都不发, 纯监听 —— 判断有没有第二个主机在轮询这个口。"""
    print(f"\n纯监听   目标 {host}:{port}   {seconds}s   "
          f"{'RTU透传' if rtu else 'Modbus TCP'}   (一个字节都不发)")
    print("=" * 74)
    try:
        with ModbusTCP(host, port, 1, timeout=3.0, rtu=rtu) as m:
            buf = m.listen(seconds)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 连不上: {type(e).__name__}: {e}")
        return
    if not buf:
        print(f"  ✓ {seconds}s 内一个字节都没有 —— 这个口目前只有你在说话。")
        print("    若后面扫描仍串帧, 那就是串口服务器自身的多主机缓存/边缘采集功能。")
        return
    print(f"  ⚠ 什么都没发, 却收到 {len(buf)}B —— **有别的主机在轮询这个口**")
    print(f"    原始: {buf[:120].hex(' ').upper()}{' ...' if len(buf) > 120 else ''}")
    frames = split_rtu_frames(buf) if rtu else []
    if frames:
        print(f"\n    拆出 {len(frames)} 个合法帧:")
        for off, slave, fc, vals in frames[:20]:
            nz = [(i, v) for i, v in enumerate(vals) if v]
            print(f"      +{off:<4} 从站={slave:<4} FC=0x{fc:02X} "
                  f"{('非零 ' + str(nz)) if nz else '(全 0)'}")
        slaves = sorted({f[1] for f in frames})
        print(f"\n    被轮询的从站地址: {slaves}")
        print("    → 去串口服务器 WEB 的『连接状态/当前连接』页看对端 IP, "
              "多半是还没下线的原监控平台。")
    print("\n  处置: 让另一个主机停掉(或改端口), 再重扫一次。")
    print("    两个主机同时问一条 RS-485, 应答会互相串, 数据不可信。")


def diag_raw(host, port, slave, timeout, rtu):
    """原始帧诊断 —— 多个从站返回同一可疑值时, 用它定性。"""
    print(f"\n原始帧诊断   目标 {host}:{port}   从站 {slave}   "
          f"{'RTU透传' if rtu else 'Modbus TCP'}")
    print("=" * 74)
    print("  看三件事: ①应答从站==请求从站? ②CRC/帧长自洽? ③有没有尾随字节?")
    cases = [
        (FC_HOLDING, 0, 8, "FC03 保持寄存器 0~7"),
        (FC_HOLDING, 0, 32, "FC03 保持寄存器 0~31"),
        (FC_HOLDING, 7, 1, "FC03 只读 addr7"),
        (FC_INPUT, 0, 32, "FC04 输入寄存器 0~31"),
        (FC_DISCRETE, 0, 16, "FC02 离散输入 0~15"),
        (FC_COIL, 0, 16, "FC01 线圈 0~15"),
    ]
    for fc, addr, cnt, label in cases:
        print(f"\n  ── {label} " + "─" * (60 - len(label)))
        try:
            with ModbusTCP(host, port, slave, timeout=timeout, rtu=rtu) as m:
                stale = m.drain()          # 先吸干别人留下的陈旧帧
                req, resp = m.raw_exchange(fc, addr, cnt, settle=1.2)
            if stale:
                print(f"    ⚠ 开连即发现 {len(stale)}B 残留帧(已丢弃): "
                      f"{stale[:40].hex(' ').upper()}{' ...' if len(stale) > 40 else ''}")
                fr = split_rtu_frames(stale) if rtu else []
                if fr:
                    print(f"      残留帧来自从站 {sorted({f[1] for f in fr})} "
                          f"—— 有别的主机在轮询")
            for line in explain_frame(req, resp, rtu, fc, cnt):
                print(line)
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ {type(e).__name__}: {e}")

    print("\n" + "=" * 74)
    print("  怎么判")
    print("=" * 74)
    print("    · 应答从站 != 请求从站  → 回话的不是这台, 总线上有设备应答所有地址")
    print("    · CRC 校验失败          → 波特率/数据位/校验位不对, 或线路干扰/没加终端电阻")
    print("    · 有尾随字节            → 串口服务器缓冲残留, 解析会整体错位")
    print("    · FC03 与 FC04 数据完全相同  → 设备连功能码都不区分, 疑似罐装应答")
    print("    · 应答全空              → 这条 485 总线上没有 Modbus 从站")
    print("\n  最硬的一招(建议必做): 把该端口的 485 接线拔掉, 再扫一次。")
    print("    数据还在 → 不是来自现场设备, 是网关回显/缓存;")
    print("    数据消失 → 确实是现场设备, 再按上面的自洽性逐条查。")


def watch(host, port, slave, count, timeout, rtu, times, interval):
    """反复读同一段寄存器, 挑出"会变"的地址 —— 区分活数据与死数据。"""
    print(f"\n连读观测   目标 {host}:{port}   从站 {slave}   地址 0~{count - 1}   "
          f"{'RTU透传' if rtu else 'Modbus TCP'}   {times} 次 × {interval}s")
    print("=" * 74)
    series = {}
    for i in range(times):
        for fc in (FC_HOLDING, FC_INPUT):
            try:
                with ModbusTCP(host, port, slave, timeout=timeout, rtu=rtu) as m:
                    m.drain()              # 排空陈旧帧, 否则读到的是上一轮的应答
                    vals = m.read(fc, 0, count)
                series.setdefault(fc, {})
                for a, v in enumerate(vals):
                    series[fc].setdefault(a, []).append(v)
            except Exception as e:  # noqa: BLE001
                if i == 0:
                    print(f"  [{'FC03' if fc == 3 else 'FC04'}] 读不到: {type(e).__name__}: {e}")
        if i < times - 1:
            time.sleep(interval)
        print(f"  第 {i + 1}/{times} 次 done", end="\r", flush=True)
    print(" " * 30, end="\r")

    print("\n结果")
    print("=" * 74)
    for fc in (FC_HOLDING, FC_INPUT):
        if fc not in series:
            continue
        changed, frozen = {}, {}
        for a, vs in sorted(series[fc].items()):
            if not any(vs):
                continue
            (changed if len(set(vs)) > 1 else frozen)[a] = vs
        name = describe(fc)
        if not changed and not frozen:
            print(f"  [{name}] 全 0")
            continue
        print(f"  [{name}]")
        for a, vs in changed.items():
            print(f"     addr {a:<4} 会变  {vs}   ← 活数据")
        for a, vs in frozen.items():
            print(f"     addr {a:<4} 恒定  {vs[0]}" + (f"  (0x{vs[0]:04X})" if vs[0] else ""))
    print("\n  判读: 会漂移的是真传感器读数(温湿度天然波动);")
    print("        纹丝不动且数值可疑(如 259=0x0103)的, 高度怀疑是回显/残留帧。")


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
    ap.add_argument("--raw", action="store_true",
                    help="原始帧诊断模式: 打印线上真实字节并做自洽性分析"
                         "(排查幽灵从站/可疑数值)")
    ap.add_argument("--watch", type=int, default=0, metavar="N",
                    help="连读 N 次, 挑出会变的地址(区分活数据与死数据)")
    ap.add_argument("--interval", type=float, default=3.0, help="--watch 的间隔秒数")
    ap.add_argument("--listen", type=float, default=0, metavar="S",
                    help="纯监听 S 秒(一个字节都不发), 判断有没有第二个主机在轮询")
    args = ap.parse_args()

    rtu = not args.tcp
    slaves = parse_range(args.slaves) if args.slaves else [args.slave]

    if args.listen:
        listen_only(args.host, args.port, args.listen, rtu)
        return
    if args.raw:
        diag_raw(args.host, args.port, slaves[0], args.timeout, rtu)
        return
    if args.watch:
        watch(args.host, args.port, slaves[0], args.count, args.timeout, rtu,
              args.watch, args.interval)
        return

    print(f"单设备寄存器全扫   目标 {args.host}:{args.port}   从站 {slaves}   "
          f"{'RTU透传' if rtu else 'Modbus TCP'}   地址 {args.start}~{args.start + args.count - 1}")
    results, resp_slaves = {}, {}
    for s in slaves:
        summ, rs = sweep_one(args.host, args.port, s, args.start, args.count, args.timeout, rtu)
        results[s] = summ
        if rs is not None:
            resp_slaves[s] = rs

    # ---- 幽灵从站检测: 多个从站返回完全相同的数据 ----
    # 现场出现过: 5002 上从站 1 和 12 都读到 addr7=259 完全相同的值,
    # 说明总线上其实只有一台设备在应答所有地址(或网关透传给了同一台)。
    # 这种情况下若按从站挨个建设备, 会凭空多出一堆重复设备。
    if len(slaves) > 1:
        print("\n" + "=" * 74)
        print(" 幽灵从站检测")
        print("=" * 74)
        sigs = {}
        for s, summ in results.items():
            if not summ:
                continue
            sig = json.dumps({str(k): sorted(v) for k, v in summ.items()}, sort_keys=True)
            sigs.setdefault(sig, []).append(s)
        dup = [v for v in sigs.values() if len(v) > 1]

        # 先看"应答从站字节" —— 它比数据相同不强
        by_resp = {}
        for q, r in resp_slaves.items():
            by_resp.setdefault(r, []).append(q)
        if resp_slaves:
            print()
            for r, qs in sorted(by_resp.items()):
                flag = "  ⚠" if len(qs) > 1 else "   "
                print(f"{flag} 请求从站 {qs} → 应答从站字节均为 {r}")

        ghost = [v for v in by_resp.values() if len(v) > 1]
        if ghost:
            print("\n  ⚠ 结论: 不同请求地址拿到**同一个应答从站字节**, 说明总线上")
            print("     只有一台设备在代答。它们不是多台设备, 千万别挨个建。")
            print("     下一步: 用 --raw 看原始帧, 并核对这段 485 的接线。")
        elif dup:
            # 数据相同, 但每台都能正确回显自己的地址 —— 那是 N 台同型号设备
            # 读数恰好一致(同一个机房里的温湿度/烟感本来就该一样), 不是幽灵。
            print()
            for group in dup:
                print(f"  ✓ 从站 {group} 数据相同, 但**应答从站字节各自正确**")
            print("     → 这是 " + str(len(dup[0])) + " 台同型号设备读数恰好一致"
                  "(同一机房内的温湿度/烟感本就该相同), 不是幽灵。可以分别建设备。")
            print("     → 但要确认它们是否真是不同位置的设备, 别把同一点位建两遍。")
        else:
            if not resp_slaves:
                print("  各从站数据不同, 未发现幽灵响应。")


if __name__ == "__main__":
    main()
