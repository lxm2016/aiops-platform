"""通知渠道管理 API: 钉钉 / 企业微信 / 短信平台 / 电话告警盒子 / 自定义 Webhook。

设计原则:
  1. 短信平台和电话告警盒子由院内提供, 接口各不相同, 这里不写死任何厂商适配,
     而是暴露"地址 + 方法 + 请求体模板", 界面上填模板即可对接;
  2. 提供 /types 元数据, 前端据此动态渲染不同表单并给出示例模板;
  3. 提供测试发送与渲染预览, 配置完立刻能验证, 不用等故障发生才知道配错了。
"""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import NotifyChannel, AlertNotifyLog
from app.schemas.schemas import (
    NotifyChannelIn, NotifyChannelOut, NotifyLogOut, NotifyTestIn,
)
from app.services import notify_service
from app.services.alert_engine import invalidate_rules_cache

router = APIRouter(prefix="/api/notify", tags=["notify"])


# ---------------------------------------------------------------------------
# 渠道类型元数据 (供界面动态渲染表单 + 提供示例)
# ---------------------------------------------------------------------------
CHANNEL_TYPES = {
    "dingtalk": {
        "label": "钉钉机器人",
        "desc": "钉钉群 -> 群设置 -> 智能群助手 -> 添加机器人 -> 自定义, 复制 Webhook 地址。",
        "fields": ["webhook_url", "secret", "at_mobiles", "at_all"],
        "tips": "安全设置选'加签'时, 把密钥填到'加签密钥'; 需要提醒具体人时填手机号(多个用逗号分隔)。",
    },
    "wecom": {
        "label": "企业微信机器人",
        "desc": "企业微信群 -> 右上角设置 -> 群机器人 -> 添加, 复制 Webhook 地址。消息会推送到成员微信。",
        "fields": ["webhook_url"],
        "tips": "适合需要推送到微信的场景(企业微信消息会同步到微信)。",
    },
    "sms": {
        "label": "短信平台 (院内)",
        "desc": "对接院内短信告警平台。填写平台提供的 HTTP 接口地址与参数模板即可。",
        "fields": ["targets", "http_url", "http_method", "http_headers", "http_body",
                   "success_keyword"],
        "sample_body": "phone={phone}&content={content}",
        "sample_body_json": '{"mobile":"{mobile}","msg":"{msg}"}',
        "tips": "号码填在上面的「接收号码」里, 模板里用 {phone}/{mobile} 等占位即可。"
                "参数名各家不同, 号码可用 {targets}{phone}{mobile}{tel} 任一别名; "
                "内容可用 {content}{msg}{text} 任一别名。",
    },
    "voice": {
        "label": "电话告警盒子",
        "desc": "对接电话语音告警设备。填写设备/平台提供的 HTTP 呼叫接口与参数模板。",
        "fields": ["targets", "http_url", "http_method", "http_headers", "http_body",
                   "success_keyword"],
        "sample_body": "called={called}&tts={tts}",
        "sample_body_json": '{"callee":"{callee}","tts":"{tts}"}',
        "tips": "号码填在上面的「被叫号码」里(多个用逗号分隔)。"
                "号码可用 {targets}{phone}{mobile}{tel}{called}{callee} 任一别名; "
                "播报内容用 {tts}{text}{msg}{short}, 建议短句, 念太长没人听。",
    },
    "webhook": {
        "label": "自定义 Webhook",
        "desc": "通用 HTTP 回调, 可对接 Server酱/PushPlus/自建网关/其他 IM。",
        "fields": ["http_url", "http_method", "http_headers", "http_body", "success_keyword"],
        "sample_body_json": '{"text":"{title}","desp":"{content}"}',
        "tips": "完全自定义, 想接什么就接什么。",
    },
}


@router.get("/types")
async def channel_types():
    """渠道类型与示例模板, 供界面渲染表单。"""
    return CHANNEL_TYPES


@router.get("/channels", response_model=List[NotifyChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NotifyChannel).order_by(NotifyChannel.id))
    return result.scalars().all()


@router.post("/channels", response_model=NotifyChannelOut)
async def create_channel(data: NotifyChannelIn, db: AsyncSession = Depends(get_db)):
    ch = NotifyChannel(**data.model_dump())
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    invalidate_rules_cache()
    return ch


@router.put("/channels/{channel_id}", response_model=NotifyChannelOut)
async def update_channel(
    channel_id: int, data: NotifyChannelIn, db: AsyncSession = Depends(get_db)
):
    ch = await db.get(NotifyChannel, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="渠道不存在")
    for k, v in data.model_dump().items():
        setattr(ch, k, v)
    await db.commit()
    await db.refresh(ch)
    invalidate_rules_cache()
    return ch


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    ch = await db.get(NotifyChannel, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="渠道不存在")
    await db.delete(ch)
    await db.commit()
    invalidate_rules_cache()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: int, body: NotifyTestIn, db: AsyncSession = Depends(get_db)
):
    """向该渠道发送一条测试消息, 并写入发送记录。

    配置完立刻点一下, 手机/钉钉收到就说明配对了 —— 不用等到真出故障才发现配错。
    """
    ch = await db.get(NotifyChannel, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="渠道不存在")

    ctx = {
        "title": body.title, "content": body.content, "level": "warning",
        "source": "AIOps平台", "metric": "测试指标", "value": "88.0",
        "time": notify_service.now_cn(), "short": "测试: " + body.title,
    }
    notify_service.inject_gateway_ctx(ch, ctx)

    content = body.content
    if ch.type in ("dingtalk", "wecom"):
        content = "\n".join([
            f"### {body.title}", "", f"> **来源**: AIOps 运维平台",
            f"> **时间**: {ctx['time']}", "", body.content,
        ])

    try:
        import asyncio
        ok, resp, err = await asyncio.wait_for(
            notify_service.send_to_channel(ch, body.title, content, ctx), timeout=20
        )
    except asyncio.TimeoutError:
        ok, resp, err = False, "", "发送超时(>20s)"
    except Exception as e:
        ok, resp, err = False, "", f"{type(e).__name__}: {e}"

    db.add(AlertNotifyLog(
        alert_id=None, channel_id=ch.id, channel_name=ch.name,
        channel_type=ch.type, level="test", target=body.title[:250],
        success=ok, response=(resp or "")[:1000], error=(err or "")[:500],
    ))
    await db.commit()

    if ok:
        return {"ok": True, "message": f"测试消息已发送到「{ch.name}」", "response": (resp or "")[:300]}
    return {"ok": False, "message": f"发送失败: {err}", "response": (resp or "")[:300]}


@router.post("/preview")
async def preview_body(channel_id: int, db: AsyncSession = Depends(get_db)):
    """预览模板渲染后的实际请求内容 (便于核对参数是否正确)。"""
    ch = await db.get(NotifyChannel, channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="渠道不存在")
    ctx = {
        "title": "【严重告警】 server-01 CPU使用率", "content": "测试告警正文", "level": "critical",
        "source": "server-01", "metric": "CPU使用率", "value": "92.5%",
        "time": notify_service.now_cn(), "short": "server-01 CPU使用率 92.5% 超限",
    }
    notify_service.inject_gateway_ctx(ch, ctx)
    rendered = notify_service.render(ch.http_body or "", ctx)
    return {"type": ch.type, "method": ch.http_method, "url": ch.http_url,
            "headers": ch.http_headers, "rendered": rendered,
            "targets": ch.targets or ""}


@router.get("/logs", response_model=List[NotifyLogOut])
async def list_logs(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """最近的通知发送记录 (排查"为什么没收到告警"用)。"""
    result = await db.execute(
        select(AlertNotifyLog).order_by(AlertNotifyLog.sent_at.desc()).limit(limit)
    )
    return result.scalars().all()


@router.delete("/logs")
async def clear_logs(db: AsyncSession = Depends(get_db)):
    """清空发送记录。"""
    from sqlalchemy import delete
    await db.execute(delete(AlertNotifyLog))
    await db.commit()
    return {"ok": True}
