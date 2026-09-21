# -*- coding: utf-8 -*-
"""动环设备(Modbus)端到端验证:
建 4 类设备 -> 轮询 -> 核对点位值 -> 确认告警触发与恢复。
只在本机跑 mock, 不碰真实设备。
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8123/api/env"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "detail": e.read().decode()[:400]}


def main():
    ok = True

    # 0) 清掉历史测试数据
    for d in req("GET", "/devices"):
        if str(d["ip"]).startswith("127.0.0.1"):
            req("DELETE", f"/devices/{d['id']}")
    print("[0] 清空本机测试设备")

    # 1) 批量建 4 台, 覆盖 温湿度 / 烟感 / 水浸 / UPS
    items = [
        {"name": "E2E_温湿度", "category": "temp_humidity", "ip": "127.0.0.1",
         "port": 5001, "slave_id": 1, "location": "机房A-1排", "poll_interval": 60},
        {"name": "E2E_烟感", "category": "smoke", "ip": "127.0.0.1",
         "port": 5002, "slave_id": 9, "location": "机房A-顶棚", "poll_interval": 60},
        {"name": "E2E_水浸", "category": "water", "ip": "127.0.0.1",
         "port": 5302, "slave_id": 1, "location": "机房A-空调下", "poll_interval": 60},
        {"name": "E2E_UPS", "category": "ups", "ip": "127.0.0.1",
         "port": 5305, "slave_id": 1, "location": "机房A-配电柜", "poll_interval": 60},
    ]
    r = req("POST", "/devices/batch", items)
    print(f"[1] 批量创建: created={r.get('created')} skipped={r.get('skipped')}")

    devs = {d["name"]: d for d in req("GET", "/devices")}
    if len(devs) != 4:
        print(f"    !! 期望 4 台, 实际 {len(devs)} 台")
        ok = False

    # 2) 烟感是多路报警控制器: 报警在第 3 路 -> 离散输入 地址 2
    smoke = devs.get("E2E_烟感")
    if smoke:
        p = smoke["points"][0]
        req("PUT", f"/points/{p['id']}", {
            "name": p["name"], "key": p["key"], "fc": 2, "address": 2,
            "data_type": "u16", "bit_index": 0, "scale": 1.0, "offset": 0.0,
            "unit": "", "alarm_value": 1, "sort": 0, "enabled": True,
        })
        print("[2] 烟感点位改为 离散输入 地址=2 (第3路报警)")

    # 3) 逐台轮询
    print("[3] 轮询结果:")
    for name in ("E2E_温湿度", "E2E_烟感", "E2E_水浸", "E2E_UPS"):
        d = devs.get(name)
        if not d:
            print(f"    {name}: 未创建")
            ok = False
            continue
        res = req("POST", f"/devices/{d['id']}/poll")
        pts = req("GET", f"/devices/{d['id']}/points")
        vals = " | ".join(
            f"{p['name']}={p['value']}{p['unit']}(raw {p['raw']})" for p in pts
        )
        print(f"    {name:12s} ok={res.get('ok')} fail={res.get('fail')} "
              f"评估={res.get('evaluated')} 告警={res.get('alerted')}  {vals}")

    # 4) 核对具体读数
    print("[4] 读数核对:")
    expects = {
        ("E2E_温湿度", "温度"): 25.6,
        ("E2E_温湿度", "湿度"): 63.4,
        ("E2E_烟感", "烟雾状态"): 1.0,
        ("E2E_水浸", "漏水状态"): 1.0,
        ("E2E_UPS", "输入电压"): 220.0,
        ("E2E_UPS", "电池容量"): 75.0,
        ("E2E_UPS", "负载率"): 50.0,
    }
    got = {}
    for name, d in devs.items():
        for p in req("GET", f"/devices/{d['id']}/points"):
            got[(name, p["name"])] = p["value"]
    for k, v in expects.items():
        actual = got.get(k)
        mark = "OK " if actual == v else "!! "
        if actual != v:
            ok = False
        print(f"    {mark}{k[0]:12s} {k[1]:8s} 期望 {v}  实际 {actual}")

    # 5) 告警是否真的落库
    print("[5] 告警记录:")
    with urllib.request.urlopen(
            "http://127.0.0.1:8123/api/alerts?page=1&page_size=50", timeout=30) as resp:
        aj = json.loads(resp.read().decode())
    rows = aj if isinstance(aj, list) else (aj.get("items") or aj.get("data") or [])
    env_rows = [a for a in rows if str(a.get("source", "")).startswith("E2E_")]
    for a in env_rows:
        print(f"    [{a.get('level')}] {a.get('title')}")
    if not env_rows:
        print("    (没有产生动环告警)")
        ok = False

    # 6) 恢复验证: 把水浸点位挪到读 0 的地址 -> 告警应自动关单
    print("[6] 恢复验证:")
    water = devs.get("E2E_水浸")
    if water:
        p = water["points"][0]
        before = [a for a in env_rows if a.get("status") != "resolved"]
        req("PUT", f"/points/{p['id']}", {
            "name": p["name"], "key": p["key"], "fc": 1, "address": 5,
            "data_type": "u16", "bit_index": 0, "scale": 1.0, "offset": 0.0,
            "unit": "", "alarm_value": 1, "sort": 0, "enabled": True,
        })
        req("POST", f"/devices/{water['id']}/poll")
        with urllib.request.urlopen(
                "http://127.0.0.1:8123/api/alerts?page=1&page_size=50",
                timeout=30) as resp:
            aj2 = json.loads(resp.read().decode())
        rows2 = aj2 if isinstance(aj2, list) else (aj2.get("items") or [])
        wrows = [a for a in rows2 if "E2E_水浸" in str(a.get("source", ""))]
        for a in wrows:
            print(f"    status={a.get('status')} {a.get('title')}")
        recovered = any(a.get("status") == "resolved" for a in wrows)
        print(f"    {'OK ' if recovered else '!! '}告警已自动恢复关单")
        if not recovered:
            ok = False

    print()
    print("=" * 60)
    print("端到端验证:", "全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
