"""Background scheduler: periodic polling of network devices, offline detection.

=======================================================================
 稳定性说明 (2026-09 修复"长时间运行后全站超时")
=======================================================================
历史问题: 定时任务没有任何护栏
    1. 没有 max_instances / coalesce, 上一轮没跑完时下一轮继续排队;
    2. 单轮任务没有总超时。SNMP 采集在设备不可达时极慢(每台最多 8~10 次
       操作 × 每次数秒), 十几台设备就能把一轮拖到几十分钟;
    3. 任务体里异常虽然被捕获, 但耗时与堆积情况完全不可见。

结果: 定时任务长期堆积, 叠加 SNMP 句柄泄漏, 进程内存/句柄持续上涨,
最终所有 HTTP 请求超时, 必须重启服务。

现在的护栏:
    - 每个任务 max_instances=1 + coalesce=True (错过就合并, 不补跑);
    - 每个任务外层 asyncio.wait_for 总超时, 超时就放弃本轮;
    - 记录每轮耗时/失败次数, 通过 get_scheduler_stats() 暴露给 /api/health;
    - 清理历史数据改为分批删除, 避免一个巨大的 DELETE 长时间占住写锁。
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models import (
    Server, NetworkDevice, ServerMetric, EnvReading, VmwareHost, StorageDevice,
)
from app.services.snmp_service import collect_network_device
from app.services.storage_service import collect_storage
from app.services.vmware_service import collect_vmware_data
from app.services.alert_engine import (
    evaluate_metric, evaluate_device_offline, flush_notifications,
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# --------------------------------------------------------------------------
# 任务运行状态 (供健康检查展示, 便于现场定位"卡在哪一轮")
# --------------------------------------------------------------------------
JOB_STATS: dict = {}

# 各任务单轮总超时 (必须明显小于执行间隔, 否则会变成排队)
JOB_TIMEOUTS = {
    "offline_check": 30,
    "snmp_poll": 100,        # 间隔 2 分钟
    "storage_poll": 120,     # 间隔 5 分钟
    "vmware_sync": 240,      # 间隔 5 分钟 (pyvmomi 较慢)
    "metric_cleanup": 120,
    "self_check": 20,
}

# 每个任务一把锁, 双保险防止重入 (max_instances 之外再兜一层)
_JOB_LOCKS: dict = {}


def _lock_for(job_id: str) -> asyncio.Lock:
    lock = _JOB_LOCKS.get(job_id)
    if lock is None:
        lock = asyncio.Lock()
        _JOB_LOCKS[job_id] = lock
    return lock


def guarded(job_id: str):
    """装饰器: 给定时任务加 锁 + 总超时 + 耗时统计 + 异常隔离。

    任何情况下都不会把异常抛回调度器, 单次超时也只影响本轮, 不会累积。
    """
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            st = JOB_STATS.setdefault(
                job_id, {"runs": 0, "failures": 0, "timeouts": 0,
                         "last_duration": 0.0, "last_run": None, "last_error": ""},
            )
            lock = _lock_for(job_id)
            if lock.locked():
                st["skipped_running"] = st.get("skipped_running", 0) + 1
                logger.warning(f"[scheduler] {job_id} 上一轮仍在执行, 跳过本轮")
                return
            async with lock:
                started = time.monotonic()
                timeout = JOB_TIMEOUTS.get(job_id, 60)
                try:
                    await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
                    st["last_error"] = ""
                except asyncio.TimeoutError:
                    st["timeouts"] += 1
                    st["last_error"] = f"超时(>{timeout}s)"
                    logger.error(f"[scheduler] {job_id} 单轮执行超时(>{timeout}s), 已放弃本轮")
                except Exception as e:
                    st["failures"] += 1
                    st["last_error"] = str(e)[:200]
                    logger.error(f"[scheduler] {job_id} 执行失败: {e}", exc_info=True)
                finally:
                    st["runs"] += 1
                    st["last_duration"] = round(time.monotonic() - started, 2)
                    st["last_run"] = datetime.utcnow().isoformat(timespec="seconds")
                    if st["last_duration"] > timeout * 0.8:
                        logger.warning(
                            f"[scheduler] {job_id} 本轮耗时 {st['last_duration']}s, "
                            f"接近上限 {timeout}s, 请关注设备可达性"
                        )
        wrapper.__name__ = f"guarded_{job_id}"
        return wrapper
    return decorator


def get_scheduler_stats() -> dict:
    """供 /api/health 使用的调度器诊断信息。"""
    jobs = []
    for job in scheduler.get_jobs():
        st = JOB_STATS.get(job.id, {})
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat(timespec="seconds") if job.next_run_time else None,
            "last_run": st.get("last_run"),
            "last_duration": st.get("last_duration"),
            "runs": st.get("runs", 0),
            "failures": st.get("failures", 0),
            "timeouts": st.get("timeouts", 0),
            "last_error": st.get("last_error", ""),
        })
    return {"running": scheduler.running, "jobs": jobs}


# --------------------------------------------------------------------------
# 任务实现
# --------------------------------------------------------------------------
@guarded("offline_check")
async def mark_offline_servers():
    """Mark servers offline if agent hasn't reported for 3 minutes."""
    async with AsyncSessionLocal() as db:
        threshold = datetime.utcnow() - timedelta(minutes=3)
        result = await db.execute(
            select(Server).where(
                Server.status == "online",
                (Server.last_seen < threshold) | (Server.last_seen.is_(None)),
            )
        )
        offline_names = []
        for server in result.scalars().all():
            server.status = "offline"
            offline_names.append(server.name)
        if offline_names:
            await db.commit()
            logger.info(f"[scheduler] {len(offline_names)} 台服务器标记为离线")

    # 离线告警单独用独立会话评估: 通知可能耗时, 不能一直占着写事务
    for name in offline_names:
        try:
            async with AsyncSessionLocal() as db2:
                await evaluate_device_offline(db2, "server", name, True)
                await db2.commit()
                await flush_notifications(db2)
        except Exception as e:
            logger.error(f"[scheduler] 服务器离线告警评估失败 {name}: {e}")


@guarded("snmp_poll")
async def poll_all_network_devices():
    """SNMP poll all registered network devices (含端口明细刷新)。

    设备逐个采集(不并发), 避免瞬间打开大量 UDP 句柄;
    每台设备内部已有 DEVICE_TIMEOUT 保护, 整个任务外层还有总超时。
    """
    from app.api.devices import upsert_ports  # 延迟导入, 避免循环依赖

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(NetworkDevice))
        devices = result.scalars().all()
        # 提前取出需要的字段, 避免会话关闭后访问已过期属性
        device_list = [
            {"id": d.id, "ip": d.ip, "name": d.name, "community": d.snmp_community,
             "vendor": d.vendor, "version": d.snmp_version}
            for d in devices
        ]

    if not device_list:
        return

    ok = bad = 0
    for dev in device_list:
        try:
            data = await collect_network_device(
                dev["ip"], dev["community"], dev["vendor"], dev["version"]
            )
            async with AsyncSessionLocal() as db:
                d = await db.get(NetworkDevice, dev["id"])
                if d:
                    d.status = "online" if data["reachable"] else "offline"
                    if data["reachable"]:
                        d.cpu_percent = data["cpu_percent"]
                        d.mem_percent = data["mem_percent"]
                        d.port_total = data["port_total"]
                        d.port_up = data["port_up"]
                        d.last_seen = datetime.utcnow()
                        await upsert_ports(db, dev["id"], data["ports"])
                    await db.commit()

            # 告警评估: 用独立会话, 因为通知可能耗时, 不能一直占着采集会话的写事务
            try:
                async with AsyncSessionLocal() as db2:
                    name = dev["name"] or dev["ip"]
                    if data["reachable"]:
                        await evaluate_metric(db2, "network", name, "cpu_percent", data["cpu_percent"])
                        await evaluate_metric(db2, "network", name, "mem_percent", data["mem_percent"])
                        await evaluate_device_offline(db2, "network", name, False)
                    else:
                        await evaluate_device_offline(db2, "network", name, True)
                    await db2.commit()
                    await flush_notifications(db2)
            except Exception as e:
                logger.error(f"[scheduler] 网络设备告警评估失败 {dev['ip']}: {e}")

            ok += 1 if data["reachable"] else 0
            bad += 0 if data["reachable"] else 1
        except Exception as e:
            bad += 1
            logger.error(f"[scheduler] 轮询网络设备 {dev['ip']} 失败: {e}", exc_info=True)
        # 每台设备之间让出一次事件循环, 保证 HTTP 请求不会被长时间饿死
        await asyncio.sleep(0)
    logger.info(f"[scheduler] SNMP 轮询完成: 在线 {ok}, 不可达 {bad}")


@guarded("storage_poll")
async def poll_all_storage():
    """周期采集所有配置了协议的存储设备 (SNMP/SMI-S)。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(StorageDevice))
        devices = result.scalars().all()
        device_list = []
        for d in devices:
            # v3 时凭据是 USM 参数字典(华为 OceanStor 默认关闭 v1/v2c 开关)
            if (d.snmp_version or "").strip() == "3":
                cred = {"user": d.snmp_v3_user or "",
                        "auth_proto": d.snmp_v3_auth_proto or "sha",
                        "auth_pass": d.snmp_v3_auth_pass or "",
                        "priv_proto": d.snmp_v3_priv_proto or "aes",
                        "priv_pass": d.snmp_v3_priv_pass or "",
                        "context": d.snmp_context or ""}
            else:
                cred = d.snmp_community
            device_list.append(
                {"id": d.id, "ip": d.ip, "name": d.name, "protocol": d.protocol,
                 "community": cred, "version": d.snmp_version,
                 "username": d.username, "password": d.password})

    for dev in device_list:
        if dev["protocol"] not in ("snmp", "smi-s"):
            continue
        try:
            data = await asyncio.wait_for(
                collect_storage(
                    dev["protocol"], dev["ip"],
                    dev["community"], dev["version"],
                    dev["username"], dev["password"],
                ),
                timeout=90,
            )
            async with AsyncSessionLocal() as db:
                d = await db.get(StorageDevice, dev["id"])
                if not d:
                    continue
                if data.get("reachable"):
                    d.status = "online"
                    d.last_seen = datetime.utcnow()
                    if data["capacity_tb"]:
                        d.capacity_tb = data["capacity_tb"]
                        d.used_tb = data["used_tb"]
                        d.used_percent = data["used_percent"]
                    d.details = data["details"]
                else:
                    d.status = "offline"
                await db.commit()

            # 告警评估 (独立会话)
            try:
                async with AsyncSessionLocal() as db2:
                    name = dev["name"] or dev["ip"]
                    if data.get("reachable"):
                        if data.get("used_percent"):
                            await evaluate_metric(db2, "storage", name, "used_percent",
                                                  data["used_percent"])
                        await evaluate_device_offline(db2, "storage", name, False)
                    else:
                        await evaluate_device_offline(db2, "storage", name, True)
                    await db2.commit()
                    await flush_notifications(db2)
            except Exception as e:
                logger.error(f"[scheduler] 存储设备告警评估失败 {dev['ip']}: {e}")
        except asyncio.TimeoutError:
            logger.warning(f"[scheduler] 存储设备 {dev['ip']} 采集超时, 跳过")
        except Exception as e:
            logger.error(f"[scheduler] 轮询存储设备 {dev['ip']} 失败: {e}", exc_info=True)
        await asyncio.sleep(0)


@guarded("vmware_sync")
async def sync_all_vmware():
    """周期同步所有 vCenter/ESXi 的虚拟机清单与实时使用率。"""
    from app.api.vmware import upsert_vms  # 延迟导入, 避免循环依赖

    async with AsyncSessionLocal() as db:
        hosts = (await db.execute(select(VmwareHost))).scalars().all()
        host_list = [
            {"id": h.id, "host": h.host, "username": h.username,
             "password": h.password, "port": h.port}
            for h in hosts
        ]

    for host in host_list:
        try:
            # pyvmomi 是同步阻塞库, 必须放线程池; 外层再套硬超时防止无限挂起
            data = await asyncio.wait_for(
                asyncio.to_thread(
                    collect_vmware_data,
                    host["host"], host["username"], host["password"], host["port"],
                ),
                timeout=180,
            )
            async with AsyncSessionLocal() as db:
                h = await db.get(VmwareHost, host["id"])
                if not h:
                    continue
                if data.get("error") and not data.get("vms"):
                    h.status = "error"
                else:
                    h.status = "online"
                    h.last_sync = datetime.utcnow()
                await db.commit()
                if data.get("vms"):
                    await upsert_vms(db, host["id"], data["vms"])
        except asyncio.TimeoutError:
            logger.warning(f"[scheduler] 同步VMware {host['host']} 超时(>180s), 跳过")
        except Exception as e:
            logger.error(f"[scheduler] 同步VMware {host['host']} 失败: {e}", exc_info=True)
        await asyncio.sleep(0)


@guarded("metric_cleanup")
async def poll_all_env_devices():
    """周期轮询动环设备 (Modbus TCP): 温湿度/烟感/水浸/UPS。

    读到的值既更新到点位缓存(界面实时显示), 也写时序(画曲线),
    同时送进告警引擎 —— 阈值规则和通知渠道完全复用告警模块。
    """
    from app.models import EnvDevice, EnvPoint
    from app.services import modbus_service
    from app.services.alert_engine import flush_notifications

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(EnvDevice).where(EnvDevice.enabled == True)  # noqa: E712
        )).scalars().all()
        devices = [(d.id, d.name, d.poll_interval or 0) for d in rows]

    for did, name, interval in devices:
        if not interval:
            continue
        try:
            async with AsyncSessionLocal() as db:
                d = await db.get(EnvDevice, did)
                if not d:
                    continue
                res = await modbus_service.poll_device(db, d)
                if res.get("ok"):
                    pts = (await db.execute(
                        select(EnvPoint).where(EnvPoint.device_id == did)
                    )).scalars().all()
                    by_name = {p.name: p for p in pts}
                    for item in res.get("values", []):
                        item["point_obj"] = by_name.get(item["point"])
                    await modbus_service.evaluate_device_points(
                        db, d, res.get("values", []))
                await db.commit()
                await flush_notifications(db)
                if res.get("fail"):
                    logger.warning(f"[动环] {name} 部分点位读取失败: {res.get('error')}")
        except Exception as e:
            logger.error(f"[动环] {name} 轮询异常: {type(e).__name__}: {e}")


async def cleanup_old_metrics():
    """清理超过保留期(默认90天)的历史监控数据, 防止磁盘无限增长。

    分批删除: 一次一个时间段, 每批之间提交并让出事件循环,
    避免单个巨大 DELETE 长时间占住 SQLite 写锁导致接口超时。
    """
    threshold = datetime.utcnow() - timedelta(days=get_settings().metric_retention_days)
    batch = 5000

    total_metrics = 0
    while True:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(ServerMetric.id).where(ServerMetric.collected_at < threshold).limit(batch)
            )).scalars().all()
            if not rows:
                break
            await db.execute(delete(ServerMetric).where(ServerMetric.id.in_(rows)))
            await db.commit()
            total_metrics += len(rows)
            if len(rows) < batch:
                break
        await asyncio.sleep(0.05)     # 让出事件循环, 让HTTP请求插进来

    total_env = 0
    while True:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(EnvReading.id).where(EnvReading.collected_at < threshold).limit(batch)
            )).scalars().all()
            if not rows:
                break
            await db.execute(delete(EnvReading).where(EnvReading.id.in_(rows)))
            await db.commit()
            total_env += len(rows)
            if len(rows) < batch:
                break
        await asyncio.sleep(0.05)

    if total_metrics or total_env:
        logger.info(f"[cleanup] 清理过期数据: 服务器指标 {total_metrics} 条, 环境读数 {total_env} 条")


@guarded("self_check")
async def self_check():
    """自检: 记录进程句柄/FD/内存/事件循环任务数, 异常增长时提前告警。

    这是"长时间运行后卡死"的早期预警 —— 如果句柄数持续上涨,
    日志里会先出现告警, 而不是等到全站超时才被发现。
    """
    try:
        import psutil
        proc = psutil.Process()
        mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)
        if hasattr(proc, "num_handles"):
            fds = proc.num_handles()
        else:
            fds = proc.num_fds()
    except Exception:
        return

    tasks = len(asyncio.all_tasks())
    soft_limit = _fd_soft_limit()
    JOB_STATS["self_check"] = JOB_STATS.get("self_check", {})
    JOB_STATS["self_check"].update(
        {"fds": fds, "memory_mb": mem_mb, "asyncio_tasks": tasks, "fd_soft_limit": soft_limit}
    )

    level = logging.INFO
    if soft_limit and fds > soft_limit * 0.8:
        level = logging.ERROR
    elif tasks > 500:
        level = logging.WARNING
    logger.log(
        level,
        f"[自检] fd={fds}/{soft_limit or '?'} 内存={mem_mb}MB asyncio任务={tasks}"
        + ("  <== 资源逼近上限, 请检查!" if level >= logging.WARNING else ""),
    )


def _fd_soft_limit() -> int:
    """当前进程可用的文件描述符上限 (Linux), Windows 返回 0 表示不适用。"""
    try:
        import resource
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        return int(soft)
    except Exception:
        return 0


def start_scheduler():
    common = dict(coalesce=True, max_instances=1, misfire_grace_time=30)
    jobs = [
        (mark_offline_servers, dict(minutes=1), "offline_check"),
        (poll_all_network_devices, dict(minutes=2), "snmp_poll"),
        (poll_all_storage, dict(minutes=5), "storage_poll"),
        (poll_all_env_devices, dict(minutes=1), "env_device_poll"),
        (sync_all_vmware, dict(minutes=5), "vmware_sync"),
        (cleanup_old_metrics, dict(hours=6), "metric_cleanup"),
        (self_check, dict(minutes=2), "self_check"),
    ]
    for fn, trigger, job_id in jobs:
        scheduler.add_job(fn, "interval", id=job_id, name=job_id, **trigger, **common)
    scheduler.start()
    logger.info("[scheduler] 定时任务已启动 (含自检/超时保护/防堆积)")


def shutdown_scheduler():
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("[scheduler] 定时任务已停止")
    except Exception:
        logger.debug("[scheduler] 停止调度器失败", exc_info=True)
