# -*- coding: utf-8 -*-
"""告警功能端到端验证:
    创建渠道 -> 绑定规则 -> 模拟上报触发告警 -> 检查告警与通知记录

用法: python tools/test_alert_flow.py
前置: 后端已启动(8080), 模拟网关已启动(8899)
"""
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("AIOPS_BASE", "http://127.0.0.1:8080")
GATEWAY = os.environ.get("AIOPS_GATEWAY", "http://127.0.0.1:8899/sms/send")
TOKEN = "aiops-agent-shared-token"

# trust_env=False: 忽略系统代理, 本机/内网地址直连
_client = httpx.Client(base_url=BASE, timeout=60, trust_env=False)
_auth = {}


def login():
    r = _client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    _auth["headers"] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    print("登录成功")


def step(n, title):
    print(f"\n{'=' * 60}\n[{n}] {title}\n{'=' * 60}")


def main():
    login()

    # ---------------------------------------------------------------
    step(1, "创建通知渠道: 院内短信平台 (指向本地模拟网关)")
    channel = {
        "name": "院内短信平台(联调)",
        "type": "sms",
        "enabled": True,
        "http_method": "POST",
        "http_url": GATEWAY,
        "http_headers": json.dumps({"Content-Type": "application/json"}),
        "http_body": json.dumps({
            "mobile": "13800138000",
            "msg": "{short}",
            "level": "{level}",
            "time": "{time}",
        }),
        "success_keyword": '"code":0',
        "timeout_seconds": 10,
        "remark": "对接院内短信告警平台 - 联调用",
    }
    r = _client.post("/api/notify/channels", json=channel, **_auth)
    print("HTTP", r.status_code)
    if r.status_code >= 400:
        print(r.text[:400])
        sys.exit(1)
    ch = r.json()
    print(f"  渠道已创建: #{ch['id']} {ch['name']} type={ch['type']}")

    # ---------------------------------------------------------------
    step(2, "测试发送 (验证配置是否正确)")
    r = _client.post(f"/api/notify/channels/{ch['id']}/test", json={
        "title": "配置联调测试",
        "content": "如果您收到这条消息, 说明短信渠道配置正确。",
    }, **_auth)
    res = r.json()
    print("  ", "成功" if res.get("ok") else "失败", "-", res.get("message"))

    # ---------------------------------------------------------------
    step(3, "把该渠道绑定到 CPU 规则")
    rule = {
        "name": "CPU使用率告警", "category": "server", "metric": "cpu_percent",
        "operator": "gte", "warning_threshold": 80, "critical_threshold": 90,
        "duration_times": 1, "silence_minutes": 0,
        "notify_levels": ["warning", "critical"], "channel_ids": [ch["id"]],
        "enabled": True, "notify_on_recovery": True,
        "remark": "服务器CPU: 80%提示, 90%告警",
    }
    r = _client.put("/api/alerts/rules/1", json=rule, **_auth)
    print("  HTTP", r.status_code, "绑定渠道:", r.json().get("channel_ids_list"))

    # ---------------------------------------------------------------
    step(4, "模拟 Agent 上报 CPU=95.5% (应触发【严重告警】)")
    r = _client.post("/api/servers/report", json={
        "ip": "10.0.0.99", "hostname": "web-prod-01", "os_type": "linux",
        "cpu_percent": 95.5, "mem_percent": 60.0, "disk_percent": 70.0,
    }, headers={"x-agent-token": TOKEN})
    print("  上报结果:", r.json())

    time.sleep(1)
    r = _client.get("/api/alerts", params={"limit": 5}, **_auth)
    alerts = r.json()
    print(f"  当前告警 {len(alerts)} 条:")
    for a in alerts:
        print(f"    [{a['level']:<8}] {a['source']} | {a['title']} | 状态={a['status']}")

    # ---------------------------------------------------------------
    step(5, "上报 CPU=85% (应降级为【提示】)")
    r = _client.post("/api/servers/report", json={
        "ip": "10.0.0.99", "hostname": "web-prod-01", "os_type": "linux",
        "cpu_percent": 85.0, "mem_percent": 60.0, "disk_percent": 70.0,
    }, headers={"x-agent-token": TOKEN})
    time.sleep(1)
    r = _client.get("/api/alerts", params={"limit": 5}, **_auth)
    for a in r.json():
        print(f"    [{a['level']:<8}] {a['source']} | 值={a.get('value')} | 命中{a.get('hit_count')}次")

    # ---------------------------------------------------------------
    step(6, "上报 CPU=30% (应自动恢复并推送恢复通知)")
    r = _client.post("/api/servers/report", json={
        "ip": "10.0.0.99", "hostname": "web-prod-01", "os_type": "linux",
        "cpu_percent": 30.0, "mem_percent": 60.0, "disk_percent": 70.0,
    }, headers={"x-agent-token": TOKEN})
    time.sleep(1)
    r = _client.get("/api/alerts", params={"limit": 5}, **_auth)
    for a in r.json():
        print(f"    [{a['level']:<8}] {a['source']} | 状态={a['status']} | 恢复时间={a.get('resolved_at')}")

    # ---------------------------------------------------------------
    step(7, "通知发送记录")
    r = _client.get("/api/notify/logs", params={"limit": 10}, **_auth)
    logs = r.json()
    print(f"  共 {len(logs)} 条记录:")
    for lg in logs:
        flag = "✓" if lg["success"] else "✗"
        print(f"    {flag} {lg['sent_at'][11:19]} {lg['channel_name']}({lg['channel_type']}) "
              f"level={lg['level']} | {lg['target'][:40]}")
        if not lg["success"]:
            print(f"       失败原因: {lg['error'][:120]}")

    ok = sum(1 for l in logs if l["success"])
    print(f"\n结果: 发送 {len(logs)} 次, 成功 {ok} 次, 失败 {len(logs) - ok} 次")


if __name__ == "__main__":
    main()
