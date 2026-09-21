# -*- coding: utf-8 -*-
"""稳定性压力测试: 反复模拟交换机轮询, 观察句柄/asyncio任务/内存是否持续增长。

这是"长时间无操作后卡死"问题的验收测试:
    - 修复前: 每轮采集都会泄漏句柄和永不退出的 asyncio 任务, 曲线单调上升;
    - 修复后: 曲线应当保持平稳 (首次初始化后不再增长)。
"""
import asyncio
import gc
import os
import sys
import time

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aiops-platform", "backend")
sys.path.insert(0, os.path.abspath(BACKEND))
os.chdir(os.path.abspath(BACKEND))

import psutil                                    # noqa: E402
import app.services.snmp_service as s            # noqa: E402

# 加速测试: 把网络超时调小, 让"不可达设备"快速失败
s.UDP_TIMEOUT = 1
s.SINGLE_OP_TIMEOUT = 2.0
s.WALK_TIMEOUT = 4.0

ROUNDS = int(os.environ.get("ROUNDS", "60"))
UNREACHABLE = "127.0.0.1"      # 未监听 161 端口 -> 模拟不可达交换机(最坏情况)

proc = psutil.Process()


def snap():
    gc.collect()
    fds = proc.num_handles() if hasattr(proc, "num_handles") else proc.num_fds()
    return fds, len(asyncio.all_tasks()), round(proc.memory_info().rss / 1024 / 1024, 1)


async def one_round():
    """模拟对一台交换机做一轮完整采集 (最坏情况: 设备不可达)。"""
    await s.snmp_get(UNREACHABLE, "public", s.OID_SYS_NAME)
    await s.snmp_get(UNREACHABLE, "public", s.OID_SYS_DESCR)
    await s.snmp_get_next(UNREACHABLE, "public", "1.3.6.1.4.1.25506.2.6.1.1.1.1.6")
    await s.snmp_walk(UNREACHABLE, "public", s.OID_IF_DESCR)
    await s.snmp_walk(UNREACHABLE, "public", s.OID_IF_OPER)


async def main():
    print(f"稳定性压力测试: {ROUNDS} 轮交换机采集 (目标 {UNREACHABLE}:161 不可达, 最坏情况)")
    print(f"{'轮次':>6} {'句柄':>8} {'asyncio任务':>12} {'内存MB':>10}   变化")
    print("-" * 60)

    base = snap()
    print(f"{0:>6} {base[0]:>8} {base[1]:>12} {base[2]:>10.1f}   基线")

    started = time.time()
    for i in range(1, ROUNDS + 1):
        await one_round()
        if i % 10 == 0 or i == ROUNDS:
            cur = snap()
            d_fd, d_task, d_mem = cur[0] - base[0], cur[1] - base[1], cur[2] - base[2]
            print(f"{i:>6} {cur[0]:>8} {cur[1]:>12} {cur[2]:>10.1f}   "
                  f"句柄{d_fd:+d} 任务{d_task:+d} 内存{d_mem:+.1f}MB")

    elapsed = time.time() - started
    final = snap()
    d_fd, d_task, d_mem = final[0] - base[0], final[1] - base[1], final[2] - base[2]

    print("-" * 60)
    print(f"耗时 {elapsed:.1f}s, 共 {ROUNDS} 轮 x 5 次SNMP操作 = {ROUNDS * 5} 次操作")
    print(f"累计增长: 句柄 {d_fd:+d}, asyncio任务 {d_task:+d}, 内存 {d_mem:+.1f}MB")
    print()

    per_op_fd = d_fd / (ROUNDS * 5)
    print(f"平均每次SNMP操作句柄增长: {per_op_fd:+.4f} 个")
    if d_fd <= 10 and d_task <= 5:
        print("判定: 通过 —— 资源占用平稳, 无泄漏。")
    else:
        print("判定: 未通过 —— 仍存在资源增长, 需继续排查。")

    # 关闭引擎后应回落到基线
    await s.close_engine()
    await asyncio.sleep(0.3)
    after = snap()
    print(f"关闭共享 engine 后: 句柄 {after[0]} (较基线 {after[0] - base[0]:+d}), "
          f"任务 {after[1]} (较基线 {after[1] - base[1]:+d})")


if __name__ == "__main__":
    asyncio.run(main())
