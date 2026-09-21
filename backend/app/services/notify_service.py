"""告警通知服务: 钉钉 / 企业微信 / 短信平台 / 电话告警盒子 / 自定义 Webhook

设计要点
--------
1. 钉钉、企业微信走各自官方机器人协议;
2. **短信平台和电话告警盒子做成"通用 HTTP 网关"**: 院内系统接口千差万别,
   不可能为每个厂商写死适配。因此只要求配置 地址 / 方法 / 请求体模板,
   模板里用 {title} {content} {phone} 等变量占位, 运行时渲染后发出去。
   这样无论是华为/中兴短信网关、还是各种电话告警盒子, 只要提供 HTTP 接口
   就能在界面上配好, 不需要改代码。
3. 每次发送都写 alert_notify_logs, 界面上能直接看到"发出去了没有、
   平台回了什么", 排查"为什么没收到告警"不再靠猜。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.database import AsyncSessionLocal
from app.models import NotifyChannel, AlertNotifyLog

logger = logging.getLogger(__name__)

_VAR_PATTERN = re.compile(r"\{(\w+)\}")

# JSON 请求体模板在替换变量前是"字符串", 替换进来的正文可能含真实换行/制表符,
# 直接 json.loads 会报 "Invalid control character" —— 只要告警正文是多行就必然失败。
# 这里先把裸控制字符转成 JSON 转义形式, 再解析, 内容本身不受影响。
_CTRL_CHARS = re.compile(r"[\x00-\x1f]")
_CTRL_MAP = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def escape_json_control_chars(s: str) -> str:
    """把字符串里的裸控制字符转义, 使其可以被 json.loads 正确解析。"""
    return _CTRL_CHARS.sub(
        lambda m: _CTRL_MAP.get(m.group(0), "\\u%04x" % ord(m.group(0))), s
    )

# 单次通知的整体超时, 防止某个渠道挂住拖累整条告警链路
NOTIFY_TIMEOUT = 15


def now_cn() -> str:
    """北京时间字符串 (数据库存 UTC, 展示/推送用北京时间)。"""
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def render(template: str, ctx: dict) -> str:
    """变量替换: {title} {content} {level} ... 未知变量替换为空串。"""
    if not template:
        return ""

    def _sub(m):
        key = m.group(1)
        return str(ctx.get(key, "") if ctx.get(key) is not None else "")

    return _VAR_PATTERN.sub(_sub, template)


def level_text(level: str) -> str:
    return {"critical": "【严重告警】", "warning": "【告警提示】", "info": "【通知】"}.get(level, "【告警】")


# ---------------------------------------------------------------------------
# 各渠道发送实现
# ---------------------------------------------------------------------------
async def _send_dingtalk(ch: NotifyChannel, title: str, content: str) -> tuple:
    """钉钉自定义机器人。支持加签(secret)与@某人。"""
    if not ch.webhook_url:
        return False, "", "未配置机器人地址 (webhook_url)"

    url = ch.webhook_url
    if ch.secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{ch.secret}"
        hmac_code = hmac.new(
            ch.secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{url}&timestamp={timestamp}&sign={sign}"

    mobiles = [m.strip() for m in (ch.at_mobiles or "").split(",") if m.strip()]
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": content},
        "at": {"atMobiles": mobiles, "isAtAll": bool(ch.at_all)},
    }
    # 有@时把手机号拼到正文末尾, 否则钉钉不会真正提醒到人
    if mobiles:
        payload["markdown"]["text"] = content + "\n\n" + " ".join(f"@{m}" for m in mobiles)

    async with httpx.AsyncClient(timeout=ch.timeout_seconds or 10, trust_env=False) as client:
        r = await client.post(url, json=payload)
    body = r.text[:500]
    try:
        ok = r.json().get("errcode") == 0
    except Exception:
        ok = r.status_code == 200
    return ok, body, "" if ok else f"钉钉返回: {body}"


async def _send_wecom(ch: NotifyChannel, title: str, content: str) -> tuple:
    """企业微信群机器人 (消息会推送到成员微信)。"""
    if not ch.webhook_url:
        return False, "", "未配置机器人地址 (webhook_url)"
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    async with httpx.AsyncClient(timeout=ch.timeout_seconds or 10, trust_env=False) as client:
        r = await client.post(ch.webhook_url, json=payload)
    body = r.text[:500]
    try:
        ok = r.json().get("errcode") == 0
    except Exception:
        ok = r.status_code == 200
    return ok, body, "" if ok else f"企业微信返回: {body}"


async def _send_http_gateway(ch: NotifyChannel, title: str, content: str, ctx: dict) -> tuple:
    """通用 HTTP 网关: 短信平台 / 电话告警盒子 / 自定义 Webhook。

    http_body 支持两种写法:
      - 以 { 开头  -> 视为 JSON 模板, 渲染后作为 JSON 请求体
      - 其他       -> 视为 key=value&key2=value2 表单模板
    GET 请求时, 渲染结果作为 query string 拼到 URL 上。
    """
    url = ch.http_url or ch.webhook_url
    if not url:
        return False, "", "未配置接口地址"

    rendered = render(ch.http_body or "", ctx)

    headers = {}
    if ch.http_headers:
        try:
            headers = json.loads(ch.http_headers)
        except Exception as e:
            logger.warning(f"[通知] 渠道 {ch.name} 请求头不是合法JSON: {e}")

    method = (ch.http_method or "POST").upper()
    timeout = ch.timeout_seconds or 10

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        if method == "GET":
            sep = "&" if "?" in url else "?"
            r = await client.get(f"{url}{sep}{rendered}", headers=headers)
        elif rendered.strip().startswith("{"):
            # JSON 请求体: 正文可能含换行, 先转义控制字符再解析
            try:
                body = json.loads(escape_json_control_chars(rendered))
            except Exception as e:
                return False, "", (
                    f"请求体不是合法JSON(变量替换后): {e} | 内容: {rendered[:200]}"
                    " | 提示: 请检查模板里的引号是否成对、变量是否放在引号内"
                )
            if "application/json" not in str(headers.get("Content-Type", "")):
                headers.setdefault("Content-Type", "application/json")
            r = await client.request(method, url, json=body, headers=headers)
        else:
            # 表单请求体
            form = {}
            for part in rendered.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    form[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
            r = await client.request(method, url, data=form, headers=headers)

    body = r.text[:500]
    ok = r.status_code < 400
    # 平台返回 200 但业务失败的情况很常见(如"余额不足"), 用关键词再判一次
    if ok and ch.success_keyword:
        ok = ch.success_keyword in body
        if not ok:
            return False, body, f"返回内容未包含成功标识 '{ch.success_keyword}'"
    return ok, body, "" if ok else f"HTTP {r.status_code}: {body}"


# 电话盒子/短信平台的字段名各家不一样。与其让用户反复猜,
# 不如把号码和内容的常见叫法都做成别名 —— 用户只要对上参数名就能通,
# 号码只填一次(渠道里的"被叫号码"), 不用写死在模板里。
_TARGET_ALIASES = ("targets", "phone", "mobile", "tel", "called",
                   "callee", "to", "number", "phonenumber", "mobiles")
_CONTENT_ALIASES = ("text", "msg", "tts", "play", "message", "speak", "words")


def split_targets(s: str) -> list:
    """把渠道里填的号码拆成一个个。支持 逗号/分号/竖线/空格 分隔, 去重保序。

    为什么需要: 融智云这类告警盒子的 msg_send 接口 To 字段只收**单个号码**
    (页面校验 length[3,13]), 一次塞多个设备直接拒。所以必须拆开逐个发。
    """
    if not s or not s.strip():
        return []
    out = []
    for p in re.split(r"[,;|\s]+", s.strip()):
        p = p.strip()
        if p and p not in out:
            out.append(p)
    return out


def inject_gateway_ctx(ch: "NotifyChannel", ctx: dict, targets: str = None) -> dict:
    """给通用 HTTP 网关的模板补上号码/内容别名。

    targets 用于"多号码逐个发送"时覆盖渠道里的值; 不传就用渠道配置的。
    """
    targets = (ch.targets or "").strip() if targets is None else targets
    for k in _TARGET_ALIASES:
        ctx[k] = targets
    # 电话盒子要短句(念长了没人听), 短信/Webhook 用完整正文
    body = ctx.get("short") if ch.type == "voice" else ctx.get("content")
    for k in _CONTENT_ALIASES:
        if k == "tts" and ch.type == "voice":
            ctx["tts"] = ctx.get("short") or ""
            continue
        ctx.setdefault(k, body or "")
    return ctx


async def _send_gateway_batch(ch: NotifyChannel, title: str, content: str,
                              ctx: dict, nums: list) -> tuple:
    """一个号码一条请求。设备侧一次只认一个号码时才需要这样。

    返回 (全部成功?, 每个号码的响应拼串, 失败原因)。
    """
    ok_all, parts, errs = True, [], []
    for n in nums:
        c2 = dict(ctx)
        inject_gateway_ctx(ch, c2, targets=n)
        try:
            ok, body, err = await _send_http_gateway(ch, title, content, c2)
        except Exception as e:
            ok, body, err = False, "", f"{type(e).__name__}: {e}"
        parts.append(f"{n} -> {body[:150]}")
        if not ok:
            ok_all = False
            errs.append(f"{n}: {err}")
        await asyncio.sleep(0.3)        # 别把设备打爆, 它要一条条拨
    return ok_all, " | ".join(parts)[:500], "; ".join(errs) if errs else ""


async def send_to_channel(ch: NotifyChannel, title: str, content: str, ctx: dict) -> tuple:
    """按渠道类型分发。返回 (success, response, error)。"""
    try:
        if ch.type == "dingtalk":
            return await _send_dingtalk(ch, title, content)
        if ch.type == "wecom":
            return await _send_wecom(ch, title, content)
        if ch.type in ("sms", "voice", "webhook"):
            nums = split_targets(ch.targets)
            # 电话/短信类设备一次通常只收一个号码 -> 拆开逐个发, 全部成功才算成功
            if len(nums) > 1 and ch.type in ("sms", "voice"):
                return await _send_gateway_batch(ch, title, content, ctx, nums)
            inject_gateway_ctx(ch, ctx)
            return await _send_http_gateway(ch, title, content, ctx)
        return False, "", f"未知渠道类型: {ch.type}"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------
async def _log(db, alert_id, ch, level, title, ok, response, error):
    db.add(AlertNotifyLog(
        alert_id=alert_id,
        channel_id=ch.id,
        channel_name=ch.name,
        channel_type=ch.type,
        level=level,
        target=title[:250],
        success=ok,
        response=(response or "")[:1000],
        error=(error or "")[:500],
        sent_at=datetime.utcnow(),
    ))


async def dispatch(
    alert_id: Optional[int],
    channel_ids: list,
    level: str,
    title: str,
    content: str,
    ctx: Optional[dict] = None,
) -> dict:
    """向指定渠道发送告警, 并写发送记录。

    返回 {"total": n, "success": n, "failed": n, "details": [...]}
    """
    if not channel_ids:
        return {"total": 0, "success": 0, "failed": 0, "details": [], "skipped": "未配置通知渠道"}

    ctx = ctx or {}
    ctx.setdefault("title", title)
    ctx.setdefault("content", content)
    ctx.setdefault("level", level)
    ctx.setdefault("time", now_cn())

    result = {"total": 0, "success": 0, "failed": 0, "details": []}

    async with AsyncSessionLocal() as db:
        rows = []
        for cid in channel_ids:
            ch = await db.get(NotifyChannel, cid)
            if not ch:
                result["details"].append({"channel": f"#{cid}", "success": False, "error": "渠道不存在"})
                continue
            if not ch.enabled:
                result["details"].append({"channel": ch.name, "success": False, "error": "渠道已停用"})
                continue
            rows.append(ch)

        for ch in rows:
            try:
                ok, resp, err = await asyncio.wait_for(
                    send_to_channel(ch, title, content, ctx), timeout=NOTIFY_TIMEOUT
                )
            except asyncio.TimeoutError:
                ok, resp, err = False, "", f"发送超时(>{NOTIFY_TIMEOUT}s)"
            except Exception as e:
                ok, resp, err = False, "", f"{type(e).__name__}: {e}"

            result["total"] += 1
            result["success"] += 1 if ok else 0
            result["failed"] += 0 if ok else 1
            result["details"].append({
                "channel": ch.name, "type": ch.type,
                "success": ok, "response": (resp or "")[:200], "error": err,
            })
            await _log(db, alert_id, ch, level, title, ok, resp, err)
            if ok:
                logger.info(f"[通知] 已发送 -> {ch.name}({ch.type})")
            else:
                logger.error(f"[通知] 发送失败 -> {ch.name}({ch.type}): {err}")

        await db.commit()

    return result


def build_alert_content(level: str, source: str, metric_label: str, value: float,
                        threshold: float, category_label: str = "", detail: str = "") -> tuple:
    """生成告警标题与正文 (钉钉/企微/短信/电话共用)。"""
    title = f"{level_text(level)}{source} {metric_label} {value:.1f}%"
    lines = [
        f"### {title}",
        "",
        f"> **对象**: {source}",
        f"> **指标**: {metric_label}",
        f"> **当前值**: {value:.1f}%  (阈值 {threshold:.1f}%)",
    ]
    if category_label:
        lines.append(f"> **类别**: {category_label}")
    lines.append(f"> **时间**: {now_cn()}")
    if detail:
        lines.append("")
        lines.append(detail)
    return title, "\n".join(lines)
