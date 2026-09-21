# -*- coding: utf-8 -*-
"""复现脚本: 验证 '每次新建 SnmpEngine' 是否导致句柄/任务泄漏。

模拟 scheduler 每轮 SNMP 轮询的行为: 对一台设备连续做 GETNEXT。
对比两种写法:
  A. 现状写法  : 每次调用 new SnmpEngine(), 且提前 return 不关闭 walk 生成器
  B. 修复写法  : 复用单例 SnmpEngine + aclosing 正确关闭
"""
import asyncio
import gc
import os
import sys

try:
    import psutil
except ImportError:
    print("需要 psutil")
    sys.exit(1)

from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity, get_cmd, walk_cmd,
)

TARGET = os.environ.get("SNMP_TARGET", "127.0.0.1")
ROUNDS = int(os.environ.get("ROUNDS", "30"))
TIMEOUT = 0.3
COMMUNITY = "public"


def handles() -> int:
    """当前进程句柄数 (fd/socket/文件统称)。"""
    p = psutil.Process()
    if hasattr(p, "num_handles"):        # Windows
        return p.num_handles()
    return p.num_fds()                    # Linux


def task_count() -> int:
    try:
        return len(asyncio.all_tasks())
    except RuntimeError:
        return -1


async def _target(ip):
    return await UdpTransportTarget.create((ip, 161), timeout=TIMEOUT, retries=0)


OID = "1.3.6.1.2.1.2.2.1.2"


async def old_style() -> None:
    """现状: 每次新建 engine, walk 取首条后直接 return (生成器未关闭)。"""
    try:
        async for (ei, es, idx, vbs) in walk_cmd(
            SnmpEngine(),                                  # <-- 每调用一次就 new
            CommunityData(COMMUNITY, mpModel=1),
            await _target(TARGET),
            ContextData(),
            ObjectType(ObjectIdentity(OID)),
            lexicographicMode=False,
            lookupMib=False,
        ):
            if ei or es:
                break
            for vb in vbs:
                return                                     # <-- 提前返回, 生成器被丢弃
            break
    except Exception:
        pass


_ENGINE = None
_ENGINE_LOCK = asyncio.Lock()


async def _engine() -> SnmpEngine:
    global _ENGINE
    async with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = SnmpEngine()
        return _ENGINE


async def new_style() -> None:
    """修复: 复用单例 engine, 并用 aclosing 保证生成器正确关闭。"""
    from contextlib import aclosing
    try:
        gen = walk_cmd(
            await _engine(),
            CommunityData(COMMUNITY, mpModel=1),
            await _target(TARGET),
            ContextData(),
            ObjectType(ObjectIdentity(OID)),
            lexicographicMode=False,
            lookupMib=False,
        )
        async with aclosing(gen):
            async for (ei, es, idx, vbs) in gen:
                if ei or es:
                    break
                for vb in vbs:
                    return
                break
    except Exception:
        pass


async def run(label, fn):
    gc.collect()
    base_h, base_t = handles(), task_count()
    for _ in range(ROUNDS):
        await fn()
    gc.collect()
    await asyncio.sleep(0.2)
    end_h, end_t = handles(), task_count()
    print(f"{label:<28} 句柄 {base_h:>5} -> {end_h:>5}  (增长 {end_h - base_h:>4})   "
          f"asyncio任务 {base_t:>4} -> {end_t:>4}  (增长 {end_t - base_t:>3})")
    return end_h - base_h, end_t - base_t


async def main():
    print(f"目标 {TARGET}:161, 轮次 {ROUNDS}, 超时 {TIMEOUT}s\n")
    print(f"起始句柄数: {handles()}, 起始任务数: {task_count()}\n")

    a = await run("[A] 现状写法", old_style)
    await asyncio.sleep(0.5)
    b = await run("[B] 修复写法", new_style)

    print()
    if a[0] > b[0] * 2 and a[0] > 5:
        print(f"结论: 现状写法泄漏明显 —— {ROUNDS} 次调用泄漏 {a[0]} 个句柄;")
        print(f"      修复写法仅 {b[0]} 个。按 scheduler 每2分钟一轮推算:")
        print(f"      每小时泄漏约 {a[0] * 30} 个句柄 -> 数小时即可耗尽默认 1024 fd 上限。")
    else:
        print("结论: 本轮未观察到显著差异(目标端口立即拒绝时泄漏可能不明显),")
        print("      但仍建议采用复用 engine + aclosing 的写法。")


if __name__ == "__main__":
    asyncio.run(main())
