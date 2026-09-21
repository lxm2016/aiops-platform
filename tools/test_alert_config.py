# -*- coding: utf-8 -*-
"""告警配置界面 端到端验证 (走 HTTP API, 与前端界面调用完全一致)

覆盖:
  1. 通知渠道 CRUD(短信平台/电话盒子 通用HTTP网关) + 测试发送 + 模板渲染预览
  2. 一键生成默认规则 (CPU/内存 80%提示 90%告警, 磁盘 85/95 ...)
  3. 规则 CRUD (新建/编辑/删除) 与阈值校验
  4. 真实上报: 82% -> 提示, 95% -> 严重告警, 40% -> 自动恢复并推送恢复通知
  5. 通知发送记录核对

用法: python tools/test_alert_config.py
前置: 后端已启动(8080), 模拟网关已启动(8899)
注意: trust_env=False 忽略系统代理, 否则本机地址会被代理拦截
"""
import json
import os
import re
import sqlite3
import sys
import time

import httpx

BASE = os.environ.get("AIOPS_BASE", "http://127.0.0.1:8080")
GATEWAY = os.environ.get("AIOPS_GATEWAY", "http://127.0.0.1:8899")
TOKEN = "aiops-agent-shared-token"
DB_PATH = os.environ.get(
    "AIOPS_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "aiops-platform", "backend", "aiops.db"),
)

_client = httpx.Client(base_url=BASE, timeout=60, trust_env=False)
_auth = {}
FAILS = []


def login():
    r = _client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    r.raise_for_status()
    _auth["headers"] = {"Authorization": f"Bearer {r.json()['access_token']}"}


def step(n, title):
    print(f"\n{'=' * 66}\n[{n}] {title}\n{'=' * 66}")


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        FAILS.append(msg)
    return cond


def db_exec(sql):
    """直接清理测试残留数据(告警/上报指标), 界面没有提供批量删除入口。"""
    if not os.path.exists(DB_PATH):
        print(f"  ! 数据库不存在, 跳过: {DB_PATH}")
        return
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute(sql)
        con.commit()
    finally:
        con.close()


def cleanup():
    """清掉历史联调数据, 保证每次验证从干净状态开始。"""
    step(0, "清理历史联调数据")
    r = _client.get("/api/notify/channels", **_auth)
    for c in r.json():
        _client.delete(f"/api/notify/channels/{c['id']}", **_auth)
    r = _client.get("/api/alerts/rules", **_auth)
    for rl in r.json():
        _client.delete(f"/api/alerts/rules/{rl['id']}", **_auth)
    _client.delete("/api/notify/logs", **_auth)
    r = _client.get("/api/servers", **_auth)
    for s in r.json():
        if s["name"].startswith("test-server"):
            _client.delete(f"/api/servers/{s['id']}", **_auth)
    db_exec("DELETE FROM alerts")
    print("  已清理: 渠道 / 规则 / 发送记录 / 测试服务器 / 历史告警")


def main():
    login()
    cleanup()

    # -----------------------------------------------------------------
    step(1, "创建通知渠道 ① 院内短信平台 (通用HTTP网关)")
    sms = {
        "name": "院内短信平台",
        "type": "sms",
        "enabled": True,
        "targets": "13800138000",
        "http_method": "POST",
        "http_url": f"{GATEWAY}/sms/send",
        "http_headers": json.dumps({"Content-Type": "application/json"}),
        "http_body": json.dumps({
            "mobile": "{mobile}",
            "msg": "{msg}",
            "level": "{level}",
            "time": "{time}",
        }, ensure_ascii=False),
        "success_keyword": '"code":0',
        "timeout_seconds": 10,
        "remark": "对接院内短信告警平台",
    }
    r = _client.post("/api/notify/channels", json=sms, **_auth)
    check(r.status_code == 200, f"创建短信渠道 HTTP {r.status_code}")
    sms_ch = r.json()
    print(f"    渠道 #{sms_ch['id']} {sms_ch['name']}")

    step(2, "创建通知渠道 ② 电话告警盒子 (语音播报用简短摘要)")
    voice = {
        "name": "电话告警盒子",
        "type": "voice",
        "enabled": True,
        "targets": "13800138000,13900139000",
        "http_method": "POST",
        "http_url": f"{GATEWAY}/voice/call",
        "http_headers": json.dumps({"Content-Type": "application/json"}),
        "http_body": json.dumps({"callee": "{callee}", "tts": "{tts}"},
                                ensure_ascii=False),
        "success_keyword": '"code":0',
        "timeout_seconds": 10,
        "remark": "电话语音告警, 只在严重告警时呼叫",
    }
    r = _client.post("/api/notify/channels", json=voice, **_auth)
    check(r.status_code == 200, f"创建电话渠道 HTTP {r.status_code}")
    voice_ch = r.json()
    print(f"    渠道 #{voice_ch['id']} {voice_ch['name']}")

    # -----------------------------------------------------------------
    step(3, "测试发送 (界面上的「测试」按钮)")
    for ch in (sms_ch, voice_ch):
        r = _client.post(f"/api/notify/channels/{ch['id']}/test", json={
            "title": "AIOps 测试告警",
            "content": "这是一条测试消息, 用于验证通知渠道配置是否正确。",
        }, **_auth)
        res = r.json()
        check(res.get("ok") is True, f"{ch['name']} 测试发送: {res.get('message')}")

    step(4, "模板渲染预览 (界面上的「预览」按钮, 核对参数对不对)")
    r = _client.post("/api/notify/preview", params={"channel_id": voice_ch["id"]}, **_auth)
    pv = r.json()
    print(f"    {pv.get('method')} {pv.get('url')}")
    print(f"    号   码: {pv.get('targets')}")
    print(f"    渲染结果: {pv.get('rendered')}")
    rendered = pv.get("rendered") or ""
    leftover = re.findall(
        r"\{(?:title|content|short|level|source|metric|value|time|targets|phone|mobile"
        r"|tel|called|callee|to|text|msg|tts|play|message)\}", rendered)
    check(not leftover, f"变量已全部替换(无残留占位符){' 残留:' + str(leftover) if leftover else ''}")
    check("13900139000" in rendered and "13800138000" in rendered,
          "「被叫号码」已渲染进请求体(号码与模板分离, 改号码不用动模板)")

    # -----------------------------------------------------------------
    step(5, "一键生成默认规则 (界面「一键生成默认规则」按钮)")
    r = _client.post("/api/alerts/rules/defaults",
                     json={"channel_ids": [sms_ch["id"], voice_ch["id"]]}, **_auth)
    print(f"    {r.json()}")
    r = _client.get("/api/alerts/rules", **_auth)
    rules = r.json()
    check(len(rules) >= 9, f"已生成 {len(rules)} 条默认规则")
    print("    核心规则:")
    for rl in rules:
        if rl["category"] == "server" or rl["metric"] == "temperature":
            print(f"      {rl['name']:<14} {rl['metric']:<14} "
                  f"提示{rl['warning_threshold']} 告警{rl['critical_threshold']} "
                  f"渠道{rl['channel_ids_list']}")

    cpu_rule = next(x for x in rules if x["category"] == "server" and x["metric"] == "cpu_percent")
    check(cpu_rule["warning_threshold"] == 80 and cpu_rule["critical_threshold"] == 90,
          f"CPU 规则阈值正确: 提示{cpu_rule['warning_threshold']} / 告警{cpu_rule['critical_threshold']}")
    check(cpu_rule["channel_ids_list"] == [sms_ch["id"], voice_ch["id"]],
          f"CPU 规则已绑定渠道: {cpu_rule['channel_ids_list']}")

    # -----------------------------------------------------------------
    step(6, "规则 CRUD 校验")
    new_rule = {
        "name": "核心交换机温度", "category": "env", "metric": "temperature",
        "operator": "gte", "warning_threshold": 28, "critical_threshold": 33,
        "duration_times": 2, "silence_minutes": 15,
        "notify_levels": ["critical"], "channel_ids": [sms_ch["id"]],
        "enabled": True, "notify_on_recovery": False, "remark": "自定义: 只严重告警才喊人",
    }
    r = _client.post("/api/alerts/rules", json=new_rule, **_auth)
    check(r.status_code == 200, f"新建规则 HTTP {r.status_code}")
    rid = r.json()["id"]
    print(f"    新建规则 #{rid} 阈值 28/33 连续2次 静默15分 仅严重告警通知")

    r = _client.put(f"/api/alerts/rules/{rid}",
                    json={**new_rule, "warning_threshold": 30, "critical_threshold": 36}, **_auth)
    check(r.json()["critical_threshold"] == 36, f"编辑后阈值: {r.json()['warning_threshold']}/{r.json()['critical_threshold']}")

    r = _client.delete(f"/api/alerts/rules/{rid}", **_auth)
    check(r.json().get("ok") is True, "删除规则成功")

    # -----------------------------------------------------------------
    def report(cpu, tag):
        r = _client.post("/api/servers/report", json={
            "ip": "10.0.0.99", "hostname": "test-server-01", "os_type": "linux",
            "cpu_percent": cpu, "mem_percent": 45.0, "disk_percent": 60.0,
        }, headers={"x-agent-token": TOKEN})
        check(r.status_code == 200, f"{tag} 上报 HTTP {r.status_code}")
        time.sleep(1.2)

    step(7, "上报 CPU=82% → 应触发【提示 warning】")
    report(82.0, "82%")
    r = _client.get("/api/alerts", params={"limit": 5}, **_auth)
    a = r.json()
    check(len(a) >= 1 and a[0]["level"] == "warning",
          f"告警级别 = {a[0]['level'] if a else '无'} (期望 warning)  标题: {a[0]['title'] if a else '-'}")

    step(8, "上报 CPU=95% → 应升级为【严重告警 critical】")
    report(95.0, "95%")
    r = _client.get("/api/alerts", params={"limit": 5}, **_auth)
    a = r.json()
    check(len(a) >= 1 and a[0]["level"] == "critical",
          f"告警级别 = {a[0]['level'] if a else '无'} (期望 critical)  值={a[0].get('value')}")

    step(9, "上报 CPU=40% → 应自动恢复并推送恢复通知")
    report(40.0, "40%")
    r = _client.get("/api/alerts", params={"limit": 5}, **_auth)
    a = r.json()
    check(len(a) >= 1 and a[0]["status"] == "resolved",
          f"告警状态 = {a[0]['status'] if a else '无'} (期望 resolved)")
    check(any("已恢复" in (x["target"] or "") for x in
              _client.get("/api/notify/logs", params={"limit": 20}, **_auth).json()),
          "已推送恢复通知(内容带「已恢复」)")

    # -----------------------------------------------------------------
    step(10, "通知发送记录核对")
    r = _client.get("/api/notify/logs", params={"limit": 20}, **_auth)
    logs = r.json()
    print(f"    共 {len(logs)} 条:")
    for lg in logs:
        flag = "✓" if lg["success"] else "✗"
        print(f"      {flag} {lg['sent_at'][11:19]} {lg['channel_name']:<10} "
              f"level={lg['level']:<8} | {lg['target'][:46]}")
        if not lg["success"]:
            print(f"          失败原因: {lg['error'][:140]}")

    ok = sum(1 for x in logs if x["success"])
    check(ok == len(logs) and len(logs) > 0, f"发送 {len(logs)} 次, 成功 {ok} 次, 失败 {len(logs) - ok} 次")

    levels = {x["level"] for x in logs}
    check("critical" in levels, f"覆盖严重告警通知: {sorted(levels)}")
    check("info" in levels, "覆盖恢复通知")

    # -----------------------------------------------------------------
    step(11, "清理联调数据 (恢复干净状态)")
    for ch in (sms_ch, voice_ch):
        _client.delete(f"/api/notify/channels/{ch['id']}", **_auth)
    for rl in _client.get("/api/alerts/rules", **_auth).json():
        _client.put(f"/api/alerts/rules/{rl['id']}",
                    json={**rl, "notify_levels": rl["notify_levels_list"] or ["warning", "critical"],
                          "channel_ids": []}, **_auth)
    _client.delete("/api/notify/logs", **_auth)
    for s in _client.get("/api/servers", **_auth).json():
        if s["name"].startswith("test-server"):
            _client.delete(f"/api/servers/{s['id']}", **_auth)
    db_exec("DELETE FROM alerts")
    print("    已删除联调渠道、解绑规则渠道、清空记录与测试服务器")

    print("\n" + "=" * 66)
    if FAILS:
        print(f"验证失败 {len(FAILS)} 项:")
        for f in FAILS:
            print("  ✗ " + f)
        sys.exit(1)
    print("全部通过 ✓ —— 告警规则 / 通知渠道 / 分级推送 / 自动恢复 均正常")


if __name__ == "__main__":
    main()
