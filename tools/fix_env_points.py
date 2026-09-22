#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修正动环「温湿度」设备的历史点位映射错误(零依赖, 只用标准库)。

背景
----
现场温湿度传感器实测: 寄存器 **0 = 湿度(%), 1 = 温度(℃)**, 倍率 0.1。
早期设备按老习惯配成 "0 = 温度", 结果把 74.3% 的湿度读成了 74.3℃,
温度那一列则显示成湿度值 —— 看着就是"温度 74.3℃"这种不可能的数。

本脚本把「温湿度」设备的点位就地校正为:
    地址 0 -> 湿度 % ,  u16, 倍率 0.1
    地址 1 -> 温度 ℃ ,  s16, 倍率 0.1   (s16 才能正确显示零下温度)
同时修正被错命名的点位(两台都叫"温度"的情况)。

特点
----
* **默认 dry-run**: 只打印要改什么, 不写库; 确认无误再加 --apply。
* **幂等**: 已经正确的点位不会被重复改动, 可反复执行。
* **只动温湿度类型设备**的 0/1 号点位, 不碰其它设备/点位。

用法(在部署服务器上)
--------------------
    # 预览(不改)
    /opt/aiops-deploy/backend/venv/bin/python /opt/aiops-deploy/tools/fix_env_points.py
    # 应用
    /opt/aiops-deploy/backend/venv/bin/python /opt/aiops-deploy/tools/fix_env_points.py --apply
    # 指定库路径(默认自动找 /opt/aiops-deploy/backend/aiops.db)
    ... fix_env_points.py --db /path/to/aiops.db
"""
import argparse
import os
import sqlite3
import sys

# 目标映射: 地址 -> 期望的点位定义
TARGET = {
    0: {"name": "湿度", "key": "humidity", "unit": "%", "data_type": "u16"},
    1: {"name": "温度", "key": "temperature", "unit": "℃", "data_type": "s16"},
}


def find_db():
    """自动定位 aiops.db。显式 --db 由调用方校验, 不走这里。"""
    cands = [
        os.environ.get("AIOPS_DB", ""),
        "/opt/aiops-deploy/backend/aiops.db",
    ]
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, os.pardir, "backend", "aiops.db"))
    for c in cands:
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


def main():
    ap = argparse.ArgumentParser(description="校正温湿度设备的点位映射")
    ap.add_argument("--apply", action="store_true", help="真正写库(不加则只预览)")
    ap.add_argument("--db", default="", help="aiops.db 路径(默认自动查找)")
    args = ap.parse_args()

    if args.db:
        # 显式指定就必须存在, 绝不静默回退到别的库(否则可能误改生产库)
        if not os.path.exists(args.db):
            print(f"[错误] 指定的数据库不存在: {args.db}")
            sys.exit(1)
        db = os.path.abspath(args.db)
    else:
        db = find_db()
    if not db:
        print("[错误] 找不到 aiops.db, 请用 --db 指定路径")
        sys.exit(1)
    print(f"数据库: {db}")
    print(f"模式  : {'应用(--apply)' if args.apply else '预览(dry-run)'}\n")

    conn = sqlite3.connect(db)
    cur = conn.cursor()
    devs = cur.execute(
        "SELECT id,name,ip,port,slave_id FROM env_devices WHERE category='temp_humidity'"
    ).fetchall()
    if not devs:
        print("没有「温湿度」类型设备, 无需处理。")
        return

    total = 0
    for did, dname, ip, port, slave in devs:
        pts = cur.execute(
            "SELECT id,name,key,address,data_type,scale,unit FROM env_points "
            "WHERE device_id=? ORDER BY address", (did,)
        ).fetchall()
        print(f"[{dname}] {ip}:{port} 从站{slave}  (共 {len(pts)} 个点位)")
        for pid, pname, pkey, addr, dt, scale, unit in pts:
            if addr not in TARGET:
                continue
            t = TARGET[addr]
            need = (pname != t["name"] or unit != t["unit"] or dt != t["data_type"])
            if not need:
                print(f"    点位#{pid} addr={addr} 已是 {t['name']}/{t['unit']} -> 跳过")
                continue
            print(f"    点位#{pid} addr={addr}: {pname}/{unit or '无单位'}/{dt} "
                  f"-> {t['name']}/{t['unit']}/{t['data_type']}")
            if args.apply:
                cur.execute(
                    "UPDATE env_points SET name=?, key=?, data_type=?, unit=? WHERE id=?",
                    (t["name"], t["key"], t["data_type"], t["unit"], pid),
                )
            total += 1
        print("")

    if args.apply:
        conn.commit()
        print(f"完成: 已修正 {total} 处点位。下一次轮询(≤1 分钟)后页面数值即为真实温湿度。")
    else:
        print(f"预览: 共 {total} 处需要修正。确认后加 --apply 应用。")
    conn.close()


if __name__ == "__main__":
    main()
