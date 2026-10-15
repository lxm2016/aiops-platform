"""Server monitoring API: CRUD + agent metric ingestion + history."""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import delete as sa_delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Server, ServerMetric, Alert
from app.schemas.schemas import (
    ServerCreate, ServerOut, MetricReport, ServerMetricOut, ServerTestIn,
    EnabledIn, BatchEnabledIn,
)
from app.services.alert_engine import (
    evaluate_server_metrics, evaluate_device_offline, flush_notifications,
    invalidate_ip_cache,
)
from app.services import diagnostic_service

router = APIRouter(prefix="/api/servers", tags=["servers"])
settings = get_settings()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 一键登记 DGX: 尽量自动带出 DGX 的 IP 与 SSH 凭据模板
# ---------------------------------------------------------------------------
# dgx-onboard.sh 纳管成功后写入的状态文件(多候选路径, 顺序取第一个存在的)
DGX_STATE_CANDIDATES = [
    "/var/lib/aiops/dgx-state.json",
    "/opt/aiops-deploy/dgx-state.json",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "dgx-state.json",
    ),
]


def _read_dgx_state() -> dict:
    for path in DGX_STATE_CANDIDATES:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("dgx_ip"):
                    return data
        except Exception:
            continue
    return None


@router.get("/dgx-template")
async def dgx_template(db: AsyncSession = Depends(get_db)):
    """一键登记 DGX 的引导模板。

    优先级:
      1) dgx-onboard.sh 写入的状态文件(含 DGX IP + 用户 + 端口);
      2) 已自动上报(agent_installed)但还没配置诊断凭据的在线服务器(多半是刚纳管的 DGX);
      3) 兜底默认值(用户=nvidia, 端口=22)。
    """
    default = {
        "ip": "", "name": "DGX-Spark-01", "os_type": "linux",
        "diag_user": "nvidia", "diag_port": 22, "source": "default",
        "hint": "运行 tools/dgx-onboard.sh 纳管后, 这里会自动带出 DGX 的 IP; 也可手动填写。",
    }
    # 1) 纳管脚本写入的状态文件
    state = _read_dgx_state()
    if state:
        return {
            "ip": state.get("dgx_ip", ""),
            "name": state.get("dgx_name") or "DGX-Spark-01",
            "os_type": "linux",
            "diag_user": state.get("dgx_user") or "nvidia",
            "diag_port": state.get("dgx_port") or 22,
            "source": "onboard-state",
            "hint": f"来自 DGX 纳管记录 (onboarded_at={state.get('onboarded_at', '')})",
        }
    # 2) 找一台"已装 Agent 但还没配诊断凭据"的在线服务器(大概率是 DGX)
    result = await db.execute(
        select(Server)
        .where(Server.agent_installed == True, Server.status == "online")
        .where((Server.diag_user == None) | (Server.diag_user == ""))
        .order_by(Server.last_seen.desc())
        .limit(1)
    )
    s = result.scalar_one_or_none()
    if s:
        return {
            "ip": s.ip,
            "name": s.name or "DGX-Spark-01",
            "os_type": s.os_type or "linux",
            "diag_user": "nvidia",
            "diag_port": s.diag_port or 22,
            "source": "auto-detected",
            "hint": "已检测到一台上报中的 DGX/服务器, IP 已自动带出, 请补全 SSH 凭据。",
        }
    return default


@router.post("/dgx-onboard-notify")
async def dgx_onboard_notify(
    payload: dict,
    x_agent_token: Optional[str] = Header(default=None),
):
    """dgx-onboard.sh 纳管成功后回调: 把 DGX 的 IP/凭据模板登记到平台,
    供前端「一键登记 DGX」向导自动带出。与 /report 共用 agent_token。"""
    if x_agent_token != settings.agent_token:
        raise HTTPException(status_code=401, detail="Agent令牌无效")
    data = payload or {}
    dgx_ip = data.get("dgx_ip")
    if not dgx_ip:
        raise HTTPException(status_code=400, detail="缺少 dgx_ip")
    state = {
        "dgx_ip": dgx_ip,
        "dgx_name": data.get("dgx_name") or "DGX-Spark-01",
        "dgx_user": data.get("dgx_user") or "nvidia",
        "dgx_port": data.get("dgx_port") or 22,
        "onboarded_at": datetime.utcnow().isoformat(),
    }
    try:
        os.makedirs("/var/lib/aiops", exist_ok=True)
        with open("/var/lib/aiops/dgx-state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[DGX] 写状态文件失败(非致命): {e}")
    return {"ok": True}


@router.get("", response_model=List[ServerOut])
async def list_servers(
    status: Optional[str] = None,
    os_type: Optional[str] = None,
    keyword: Optional[str] = None,
    enabled: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Server)
    if status:
        stmt = stmt.where(Server.status == status)
    if os_type:
        stmt = stmt.where(Server.os_type == os_type)
    if keyword:
        stmt = stmt.where(Server.name.contains(keyword) | Server.ip.contains(keyword))
    if enabled is not None:
        stmt = stmt.where(Server.enabled == enabled)
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
    # 历史指标(server_metrics)是分钟级采集的大表, 不能靠 ORM 级联删
    # (会把整表加载成对象再逐行 DELETE, 跑了几个月的服务器必然超时)。
    # 这里用单条 SQL 按 server_id 索引批量删; 配合模型上的 passive_deletes,
    # 删服务器本体时也不再对 server_metrics 做全列 SELECT —— 即使存量老库
    # 缺新列也能删干净(列表页只查 servers 主表, 所以之前"列表正常但删不掉")。
    try:
        await db.execute(
            sa_delete(ServerMetric).where(ServerMetric.server_id == server_id)
        )
        await db.delete(server)
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"[服务器] 删除 id={server_id} 失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    invalidate_ip_cache()          # 告警文案按名字反查 IP 的缓存要失效
    return {"ok": True}


@router.put("/{server_id}", response_model=ServerOut)
async def update_server(
    server_id: int, data: ServerCreate, db: AsyncSession = Depends(get_db)
):
    """编辑服务器（含只读诊断凭据）。只更新请求中提供的字段。"""
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    # IP 唯一性（排除自身）
    if data.ip is not None and data.ip != server.ip:
        dup = await db.execute(
            select(Server).where(Server.ip == data.ip, Server.id != server_id)
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该IP已存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(server, k, v)
    await db.commit()
    await db.refresh(server)
    invalidate_ip_cache()
    return server


@router.patch("/{server_id}/enabled", response_model=ServerOut)
async def toggle_server_enabled(
    server_id: int, data: EnabledIn, db: AsyncSession = Depends(get_db)
):
    """暂停/恢复监测一台服务器。

    enabled=False: 不再采集指标、不参与离线判定、不触发告警;
    前端 Agent 上报会被忽略(见 report_metrics 的早返回)。
    """
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    server.enabled = data.enabled
    await db.commit()
    await db.refresh(server)
    invalidate_ip_cache()
    return server


@router.post("/batch-enabled")
async def batch_set_enabled(data: BatchEnabledIn, db: AsyncSession = Depends(get_db)):
    """批量暂停/恢复监测多台服务器(配合前端列表复选框)。"""
    if not data.ids:
        return {"ok": True, "updated": 0}
    result = await db.execute(select(Server).where(Server.id.in_(data.ids)))
    rows = result.scalars().all()
    for s in rows:
        s.enabled = data.enabled
    await db.commit()
    invalidate_ip_cache()
    return {"ok": True, "updated": len(rows)}


@router.post("/{server_id}/diagnose")
async def diagnose_server_endpoint(server_id: int, db: AsyncSession = Depends(get_db)):
    """只读诊断 + AI 分析：连到服务器采集 CPU/内存/磁盘/进程, 由大模型给原因与人工处置建议。

    注：全程只读, 不执行任何修改操作。
    """
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    try:
        result = await diagnostic_service.diagnose_and_analyze(server)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"诊断失败: {e}")
    return result


@router.post("/{server_id}/test-connection")
async def test_connection_endpoint(
    server_id: int, data: ServerTestIn, db: AsyncSession = Depends(get_db)
):
    """快速测试只读诊断凭据能否连上（只跑一条最小命令，全程只读）。

    支持用表单里尚未保存的密码直接测试（diag_* 覆盖已存值，留空则用已存密码）。
    """
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    try:
        return await diagnostic_service.test_connection(
            server,
            diag_user=data.diag_user,
            diag_password=data.diag_password,
            diag_port=data.diag_port,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"测试失败: {e}")


@router.get("/{server_id}/process-list")
async def process_list_endpoint(server_id: int, db: AsyncSession = Depends(get_db)):
    """点击"进程数"卡片下钻：只读列出资源占用 TOP 进程（名称/PID/CPU/内存）。

    全程只读, 不执行任何修改操作。
    """
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="服务器不存在")
    try:
        return await diagnostic_service.process_list(server)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取进程列表失败: {e}")


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
    # 已禁用(暂停监测)的服务器: 不改状态、不写指标, 相当于"平台不监测它"
    if not server.enabled:
        return {"ok": True, "skipped": True, "detail": "该服务器已暂停监测(enabled=False)"}

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
    """Dashboard summary stats.

    已暂停监测(enabled=False)的服务器: 不计入「离线」, 单独归入 paused。
    这样在列表里把某台服务器设为「停用监测」后, 它不会再拉高离线数量 ——
    否则就会出现"明明已暂停却还显示离线"的困惑(例如 50.177)。
    """
    total = (await db.execute(select(func.count(Server.id)))).scalar() or 0
    enabled_count = (await db.execute(
        select(func.count(Server.id)).where(Server.enabled == True)
    )).scalar() or 0
    online = (await db.execute(
        select(func.count(Server.id)).where(
            Server.status == "online", Server.enabled == True
        )
    )).scalar() or 0
    # 离线 = 在监测中(enabled)但状态不是 online 的服务器
    offline = enabled_count - online
    paused = total - enabled_count
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
        "total": total, "online": online, "offline": offline, "paused": paused,
        "linux": linux, "windows": windows, "open_alerts": open_alerts,
    }
