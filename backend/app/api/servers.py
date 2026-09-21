"""Server monitoring API: CRUD + agent metric ingestion + history."""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Server, ServerMetric, Alert
from app.schemas.schemas import (
    ServerCreate, ServerOut, MetricReport, ServerMetricOut,
)
from app.services.alert_engine import (
    evaluate_server_metrics, evaluate_device_offline, flush_notifications,
    invalidate_ip_cache,
)

router = APIRouter(prefix="/api/servers", tags=["servers"])
settings = get_settings()
logger = logging.getLogger(__name__)


@router.get("", response_model=List[ServerOut])
async def list_servers(
    status: Optional[str] = None,
    os_type: Optional[str] = None,
    keyword: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Server)
    if status:
        stmt = stmt.where(Server.status == status)
    if os_type:
        stmt = stmt.where(Server.os_type == os_type)
    if keyword:
        stmt = stmt.where(Server.name.contains(keyword) | Server.ip.contains(keyword))
    result = await db.execute(stmt.order_by(Server.id))
    return result.scalars().all()


@router.post("", response_model=ServerOut)
async def create_server(data: ServerCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Server).where(Server.ip == data.ip))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该IP已存在")
    server = Server(**data.model_dump())
    db.add(server)
    await db.commit()
    await db.refresh(server)
    invalidate_ip_cache()          # 告警文案要按名字反查 IP, 缓存得失效
    return server


@router.delete("/{server_id}")
async def delete_server(server_id: int, db: AsyncSession = Depends(get_db)):
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    await db.delete(server)
    await db.commit()
    invalidate_ip_cache()
    return {"ok": True}


@router.get("/{server_id}/metrics", response_model=List[ServerMetricOut])
async def get_metrics(
    server_id: int,
    hours: int = 1,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """查询历史指标。支持 hours 相对范围, 或 start/end 自定义时间段(UTC ISO)。"""
    conditions = [ServerMetric.server_id == server_id]
    if start and end:
        try:
            since = datetime.fromisoformat(start.replace("Z", ""))
            until = datetime.fromisoformat(end.replace("Z", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="时间格式错误")
        if until <= since:
            raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")
        if until - since > timedelta(days=90):
            raise HTTPException(status_code=400, detail="时间范围不能超过90天")
        conditions.append(ServerMetric.collected_at >= since)
        conditions.append(ServerMetric.collected_at <= until)
    else:
        hours = min(max(hours, 1), 24 * 90)  # 最长支持查询90天
        since = datetime.utcnow() - timedelta(hours=hours)
        conditions.append(ServerMetric.collected_at >= since)
    # ------------------------------------------------------------------
    # 性能保护: 先数总量, 再决定是否在 SQL 层降采样。
    # 旧实现是"全量查出来再在内存里切片", 当用户选 90 天时单台服务器就有
    # 十几万行(每行还带 raw JSON), 会瞬间吃掉几百 MB 内存并长时间占住连接,
    # 轻则接口超时, 重则被 OOM Killer 杀掉 —— 这也是"用着用着就卡死"的
    # 原因之一。现在把采样下推到 SQL, 数据库只返回需要的行。
    # ------------------------------------------------------------------
    MAX_POINTS = 2000           # 前端最多画约 2000 个点
    HARD_LIMIT = 4000           # 无论如何一次最多取 4000 行
    total = (await db.execute(
        select(func.count(ServerMetric.id)).where(*conditions)
    )).scalar() or 0
    step = max(1, total // MAX_POINTS)

    stmt = select(ServerMetric).where(*conditions)
    if step > 1:
        # 用窗口函数按时间顺序均匀取点 (SQLite 3.25+ / MySQL 8+ / PG 均支持)
        rn = func.row_number().over(order_by=ServerMetric.collected_at).label("rn")
        sub = select(ServerMetric.id.label("mid"), rn).where(*conditions).subquery()
        stmt = select(ServerMetric).where(
            ServerMetric.id.in_(select(sub.c.mid).where(sub.c.rn % step == 0))
        )
    stmt = stmt.order_by(ServerMetric.collected_at).limit(HARD_LIMIT)

    try:
        rows = (await db.execute(stmt)).scalars().all()
    except Exception:
        # 老版本数据库不支持窗口函数: 回退为"硬限制行数 + 内存降采样"
        await db.rollback()
        rows = (await db.execute(
            select(ServerMetric).where(*conditions)
            .order_by(ServerMetric.collected_at).limit(HARD_LIMIT)
        )).scalars().all()
        rows = rows[::max(1, len(rows) // MAX_POINTS)]

    if total > HARD_LIMIT:
        logger.info(f"[metrics] server={server_id} 共 {total} 行, 已降采样为 {len(rows)} 个点返回")

    # 附加各分区磁盘历史 (从raw.disks提取, 供前端分区分开画线)
    out = []
    for r in rows:
        item = ServerMetricOut.model_validate(r)
        item.disks = (r.raw or {}).get("disks") or []
        out.append(item)
    return out


@router.get("/{server_id}/detail")
async def get_detail(server_id: int, db: AsyncSession = Depends(get_db)):
    """Latest metric with raw detail (disks/ports/services)."""
    result = await db.execute(
        select(ServerMetric)
        .where(ServerMetric.server_id == server_id)
        .order_by(ServerMetric.collected_at.desc())
        .limit(1)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        return {"raw": None}
    return {"raw": metric.raw, "collected_at": metric.collected_at}


@router.post("/report")
async def report_metrics(
    data: MetricReport,
    x_agent_token: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Agent pushes metrics here. Auto-registers unknown servers."""
    if x_agent_token != settings.agent_token:
        raise HTTPException(status_code=401, detail="Agent令牌无效")

    result = await db.execute(select(Server).where(Server.ip == data.ip))
    server = result.scalar_one_or_none()
    if not server:
        server = Server(
            name=data.hostname or data.ip,
            ip=data.ip,
            os_type=data.os_type,
            os_distro=data.os_distro,
            os_version=data.os_version,
            cpu_cores=data.cpu_cores,
            cpu_model=data.cpu_model,
            mem_total_gb=data.mem_total_gb,
            agent_installed=True,
        )
        db.add(server)
        await db.flush()

    # Update server info
    was_offline = server.status != "online"
    server.status = "online"
    server.last_seen = datetime.utcnow()
    # 重新上线: 自动关闭之前的离线告警 (只在状态真正变化时才查库, 避免每次上报都查)
    if was_offline:
        await evaluate_device_offline(db, "server", server.name, False)
    server.agent_installed = True
    if data.cpu_cores:
        server.cpu_cores = data.cpu_cores
    if data.mem_total_gb:
        server.mem_total_gb = data.mem_total_gb
    if data.cpu_model:
        server.cpu_model = data.cpu_model
    if data.os_distro:
        server.os_distro = data.os_distro
    if data.os_version:
        server.os_version = data.os_version

    metric = ServerMetric(
        server_id=server.id,
        cpu_percent=data.cpu_percent,
        mem_percent=data.mem_percent,
        mem_used_gb=data.mem_used_gb,
        disk_percent=data.disk_percent,
        disk_used_gb=data.disk_used_gb,
        disk_total_gb=data.disk_total_gb,
        net_rx_mbps=data.net_rx_mbps,
        net_tx_mbps=data.net_tx_mbps,
        load_avg=data.load_avg,
        process_count=data.process_count,
        raw={
            "disks": data.disks,
            "ports": data.ports,
            "services": data.services,
            "hostname": data.hostname,
        },
        collected_at=datetime.utcnow(),
    )
    db.add(metric)

    # Alert evaluation (只入队告警与通知, 不在这时发送)
    await evaluate_server_metrics(db, server.name, data.model_dump())
    await db.commit()
    # 事务提交后再发送通知: 若在事务内发送, 读渠道配置会撞上SQLite写锁白等30秒
    await flush_notifications(db)
    return {"ok": True, "server_id": server.id}


@router.get("/stats/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """Dashboard summary stats."""
    total = (await db.execute(select(func.count(Server.id)))).scalar() or 0
    online = (await db.execute(
        select(func.count(Server.id)).where(Server.status == "online")
    )).scalar() or 0
    linux = (await db.execute(
        select(func.count(Server.id)).where(Server.os_type == "linux")
    )).scalar() or 0
    windows = (await db.execute(
        select(func.count(Server.id)).where(Server.os_type == "windows")
    )).scalar() or 0
    open_alerts = (await db.execute(
        select(func.count(Alert.id)).where(Alert.status == "open")
    )).scalar() or 0
    return {
        "total": total, "online": online, "offline": total - online,
        "linux": linux, "windows": windows, "open_alerts": open_alerts,
    }
