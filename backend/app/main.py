"""AIOps Platform - FastAPI application entry point.

=======================================================================
 稳定性说明 (2026-09 修复"长时间运行后全站超时、只能重启")
=======================================================================
本文件承担三件事:
  1. 启动顺序与优雅关闭 (数据库 -> 默认管理员 -> LLM配置 -> 定时任务);
  2. 多进程保护: uvicorn 若被配成 --workers N, 定时任务只允许在一个进程里跑,
     否则 N 个进程会同时轮询 SNMP/VMware, 把设备和数据库打爆;
  3. 深度健康检查 /api/health: 前端与 systemd watchdog 都依赖它判断
     "进程还活着但已经卡死" 这种状态。
"""
import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import init_db, AsyncSessionLocal, ping_db, dispose_engine
from app.api import (
    auth, servers, vmware, devices, alerts, chat, agents, racks,
    notify, settings as settings_api, env_device,
)
from app.api.auth import ensure_admin_user
from app.services import llm_service
from app.services.scheduler import start_scheduler, shutdown_scheduler, get_scheduler_stats
from app.services import snmp_service

settings = get_settings()

# 配置日志: SNMP和服务调度信息输出到journalctl
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aiops")

APP_START_TS = time.time()

# 慢请求阈值: 超过就记一条警告日志, 便于现场排查"卡在哪里"
SLOW_REQUEST_SECONDS = 5.0


# --------------------------------------------------------------------------
# 多进程保护: 定时任务全局单例
# --------------------------------------------------------------------------
_scheduler_lock_file = None


def _acquire_scheduler_lock() -> bool:
    """尝试独占"定时任务执行权"。

    uvicorn 用多 worker 时, 每个 worker 都是独立进程、各有一份 scheduler。
    如果不加锁, 2 个 worker 会让 SNMP 轮询频率翻倍, 泄漏与设备压力同步翻倍。
    这里用文件锁让只有第一个抢到的进程真正启动调度器。
    """
    global _scheduler_lock_file
    try:
        import fcntl  # Linux
    except ImportError:
        # Windows(本地开发)没有 fcntl, 默认允许启动
        return True
    try:
        db_path = settings.database_url.split("///")[-1]
        lock_path = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".scheduler.lock")
        f = open(lock_path, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
        _scheduler_lock_file = f
        return True
    except Exception:
        return False


def _release_scheduler_lock():
    global _scheduler_lock_file
    if _scheduler_lock_file is None:
        return
    try:
        import fcntl
        fcntl.flock(_scheduler_lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        _scheduler_lock_file.close()
    except Exception:
        pass
    _scheduler_lock_file = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 62)
    logger.info(f"[启动] AIOps 平台后端 PID={os.getpid()} Python={sys.version.split()[0]}")
    await init_db()
    async with AsyncSessionLocal() as db:
        await ensure_admin_user(db)
    await llm_service.reload_config()  # 加载界面保存的LLM配置

    if _acquire_scheduler_lock():
        start_scheduler()
    else:
        logger.warning(
            "[启动] 未取得定时任务锁 -> 本进程不运行定时任务 "
            "(说明存在多个 worker, 只有第一个 worker 负责轮询)"
        )

    logger.info("[启动] 就绪, 健康检查: /api/health")
    try:
        yield
    finally:
        logger.info("[关闭] 正在优雅退出...")
        shutdown_scheduler()
        await snmp_service.close_engine()
        await llm_service.aclose_clients()
        await dispose_engine()
        _release_scheduler_lock()
        logger.info("[关闭] 已完成")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AIOps运维监控平台 - 服务器/VMware/网络设备/存储/温湿度监控 + 千问大模型智能分析",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def slow_request_logger(request: Request, call_next):
    """记录慢请求 + 捕获未处理异常, 避免一个请求异常影响整个服务。"""
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as e:
        logger.error(f"[请求异常] {request.method} {request.url.path}: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误: {e}"})
    elapsed = time.monotonic() - started
    if elapsed > SLOW_REQUEST_SECONDS and request.url.path != "/api/health":
        logger.warning(f"[慢请求] {elapsed:.1f}s {request.method} {request.url.path}")
    return response


# Register routers
app.include_router(auth.router)
app.include_router(servers.router)
app.include_router(vmware.router)
app.include_router(devices.router)
app.include_router(alerts.router)
app.include_router(notify.router)
app.include_router(chat.router)
app.include_router(agents.router)
app.include_router(racks.router)
app.include_router(settings_api.router)
# 动环设备(Modbus) 路由: devices.py 里的旧 /env 被动接口已下线,
# /api/env/* 现在全部归属 env_device.py
app.include_router(env_device.router)


@app.get("/api/health")
async def health():
    """深度健康检查 (无需登录)。

    返回进程资源、数据库连通性、定时任务执行状态。
    前端用它判断"服务是否卡死"; watchdog 用它决定要不要自动重启。
    """
    info = {
        "status": "ok",
        "version": settings.app_version,
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - APP_START_TS, 1),
    }

    # 进程资源
    try:
        import psutil
        proc = psutil.Process()
        info["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
        if hasattr(proc, "num_handles"):
            info["fds"] = proc.num_handles()
        else:
            info["fds"] = proc.num_fds()
        try:
            import resource
            info["fd_limit"] = int(resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        except Exception:
            info["fd_limit"] = None
        info["threads"] = proc.num_threads()
    except Exception:
        pass

    info["asyncio_tasks"] = len(asyncio.all_tasks())

    # 数据库连通性: 这是"进程活着但接口全挂"最关键的检测点
    t0 = time.monotonic()
    info["database"] = "ok" if await ping_db() else "error"
    info["db_latency_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # 定时任务状态 (含自检采集到的 fd/内存)
    info["scheduler"] = get_scheduler_stats()
    info["snmp"] = snmp_service.stats()

    if info["database"] != "ok":
        info["status"] = "degraded"

    # fd 逼近上限也视为不健康, 让 watchdog 提前介入
    fds, limit = info.get("fds"), info.get("fd_limit")
    if fds and limit and fds > limit * 0.9:
        info["status"] = "degraded"
        info["warning"] = f"文件描述符使用率过高: {fds}/{limit}"

    status_code = 200 if info["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=info)


@app.get("/api/health/live")
async def health_live():
    """极轻量存活探针 (不查库), 供 nginx/systemd 高频探测。"""
    return {"status": "ok", "pid": os.getpid()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)
