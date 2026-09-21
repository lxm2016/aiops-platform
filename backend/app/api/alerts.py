"""Alert management API: 告警列表 + 告警规则配置。"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Alert, AlertRule
from app.schemas.schemas import AlertOut, AlertRuleIn, AlertRuleOut
from app.services.alert_engine import (
    init_default_rules, invalidate_rules_cache,
    METRIC_LABELS, CATEGORY_LABELS,
)
from app.services.llm_service import analyze_alert

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _rule_out(r: AlertRule) -> AlertRuleOut:
    """ORM -> Schema, 并把逗号分隔字符串展开成数组方便前端绑定多选框。"""
    data = {c.name: getattr(r, c.name) for c in AlertRule.__table__.columns}
    data["channel_ids_list"] = [
        int(x) for x in (r.channel_ids or "").split(",") if x.strip().isdigit()
    ]
    data["notify_levels_list"] = [
        x for x in (r.notify_levels or "").split(",") if x.strip()
    ]
    return AlertRuleOut(**data)


class DefaultsIn(BaseModel):
    channel_ids: List[int] = []


@router.get("", response_model=List[AlertOut])
async def list_alerts(
    status: Optional[str] = None,
    level: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Alert)
    if status:
        stmt = stmt.where(Alert.status == status)
    if level:
        stmt = stmt.where(Alert.level == level)
    if category:
        stmt = stmt.where(Alert.category == category)
    result = await db.execute(
        stmt.order_by(Alert.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


@router.post("/{alert_id}/ack")
async def ack_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    alert = await db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")
    alert.status = "ack"
    await db.commit()
    return {"ok": True}


@router.post("/{alert_id}/resolve")
async def resolve_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    alert = await db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")
    alert.status = "resolved"
    alert.resolved_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 告警规则 (界面可视化配置)
# ---------------------------------------------------------------------------
@router.get("/rules", response_model=List[AlertRuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).order_by(AlertRule.id))
    return [_rule_out(r) for r in result.scalars().all()]


@router.post("/rules", response_model=AlertRuleOut)
async def create_rule(data: AlertRuleIn, db: AsyncSession = Depends(get_db)):
    rule = AlertRule(
        name=data.name, category=data.category, metric=data.metric,
        operator=data.operator, warning_threshold=data.warning_threshold,
        critical_threshold=data.critical_threshold, duration_times=data.duration_times,
        silence_minutes=data.silence_minutes,
        notify_levels=",".join(data.notify_levels) or "warning,critical",
        channel_ids=",".join(str(i) for i in data.channel_ids),
        enabled=data.enabled, notify_on_recovery=data.notify_on_recovery,
        remark=data.remark, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    invalidate_rules_cache()
    return _rule_out(rule)


@router.put("/rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(rule_id: int, data: AlertRuleIn, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    rule.name = data.name
    rule.category = data.category
    rule.metric = data.metric
    rule.operator = data.operator
    rule.warning_threshold = data.warning_threshold
    rule.critical_threshold = data.critical_threshold
    rule.duration_times = data.duration_times
    rule.silence_minutes = data.silence_minutes
    rule.notify_levels = ",".join(data.notify_levels) or "warning,critical"
    rule.channel_ids = ",".join(str(i) for i in data.channel_ids)
    rule.enabled = data.enabled
    rule.notify_on_recovery = data.notify_on_recovery
    rule.remark = data.remark
    rule.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(rule)
    invalidate_rules_cache()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    await db.delete(rule)
    await db.commit()
    invalidate_rules_cache()
    return {"ok": True}


@router.post("/rules/defaults")
async def create_default_rules(body: DefaultsIn, db: AsyncSession = Depends(get_db)):
    """一键生成默认规则: CPU/内存/磁盘 80%提示、90%告警, 温湿度等。"""
    added = await init_default_rules(db, body.channel_ids)
    return {"ok": True, "added": added,
            "message": f"已生成 {added} 条默认规则" if added else "默认规则已存在, 无需重复生成"}


@router.get("/meta")
async def alert_meta():
    """类别与指标选项, 供界面下拉框使用。"""
    return {
        "categories": [{"value": k, "label": v} for k, v in CATEGORY_LABELS.items()],
        "metrics": [{"value": k, "label": v} for k, v in METRIC_LABELS.items()],
        "operators": [
            {"value": "gte", "label": "大于等于 (≥)"},
            {"value": "lte", "label": "小于等于 (≤)"},
        ],
        "levels": [
            {"value": "warning", "label": "提示 (warning)"},
            {"value": "critical", "label": "严重告警 (critical)"},
        ],
    }


@router.get("/{alert_id}/analyze")
async def analyze_alert_api(alert_id: int, db: AsyncSession = Depends(get_db)):
    """Use Qwen LLM to analyze an alert and provide troubleshooting advice."""
    alert = await db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")

    info = (
        f"告警级别: {alert.level}\n"
        f"告警类别: {alert.category}\n"
        f"来源: {alert.source}\n"
        f"标题: {alert.title}\n"
        f"详情: {alert.detail}\n"
        f"时间: {alert.created_at}"
    )
    analysis = await analyze_alert(info)
    return {"alert_id": alert_id, "analysis": analysis}
