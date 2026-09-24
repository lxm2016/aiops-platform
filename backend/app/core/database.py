"""Async SQLAlchemy database setup.

=======================================================================
 稳定性说明 (2026-09 修复"长时间运行后全站超时")
=======================================================================
1. 连接池显式化: 之前依赖 SQLAlchemy 默认值, 在长时间运行 + 突发并发下
   容易出现连接等待不设上限、失效连接未被剔除的情况。现在显式设置
   pool_size / max_overflow / pool_timeout / pool_recycle / pool_pre_ping。
2. get_db 增加安全回滚: 请求中途异常时保证连接归还前先 rollback,
   避免把带事务的连接放回池里, 造成后续请求排队等锁而超时。
3. SQLite 保持 WAL 模式 + busy_timeout, 读写不互斥, 等锁最长 30 秒。
4. 补充索引: server_metrics(server_id, collected_at) 复合索引, 让历史曲线
   查询走索引而不是全表扫描 (数据量到百万级后差别巨大)。
"""
import logging
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_IS_SQLITE = settings.database_url.startswith("sqlite")

# SQLite需要特殊参数防止长时间不用后连接失效(502根因)
_engine_kwargs = dict(echo=False, future=True)

if _IS_SQLITE:
    # SQLAlchemy 对 aiosqlite 默认且强制使用 NullPool(每次用完即关)。
    # 这对 SQLite 反而是正确的选择: 连接池里的多个连接会互相争抢写锁,
    # 不如现用现建。显式声明它, 免得启动日志里刷一堆参数不支持的告警。
    _engine_kwargs.update(
        poolclass=NullPool,
        connect_args={
            "timeout": 30,      # busy_timeout: 等锁最多30秒而非立即报错
            "check_same_thread": False,
        },
    )
else:
    # MySQL / PostgreSQL: 建连有成本, 用真正的连接池
    _engine_kwargs.update(
        poolclass=AsyncAdaptedQueuePool,
        pool_pre_ping=True,     # 取用前探活, 自动重建失效连接
        pool_recycle=1800,      # 30分钟回收, 防止连接老化
        pool_size=10,
        max_overflow=20,
        pool_timeout=15,        # 池满时最多等15秒, 超时快速失败而不是无限挂起
    )

try:
    engine = create_async_engine(settings.database_url, **_engine_kwargs)
except TypeError as e:
    # 某些方言/驱动不接受全部池参数时优雅降级, 保证服务仍能启动
    logger.warning(f"[DB] 连接池参数不被当前驱动支持({e}), 使用最小参数集")
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# SQLite启用WAL模式: 允许并发读写, 长时间不用后不会锁死(502根因修复)
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if not _IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")      # WAL模式, 读写不互斥
        cursor.execute("PRAGMA busy_timeout=30000")    # 等锁30秒
        cursor.execute("PRAGMA synchronous=NORMAL")    # WAL下NORMAL足够安全
        cursor.execute("PRAGMA cache_size=-64000")     # 64MB缓存
        cursor.execute("PRAGMA wal_autocheckpoint=1000")
    finally:
        cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI 依赖: 每个请求一个会话, 异常时保证回滚并归还连接。

    这里的 try/except 是为了防止"请求异常 -> 连接带着未结束的事务回到池里",
    那会让后续请求在 SQLite 上持续等待写锁, 表现就是接口大面积超时。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            try:
                await session.rollback()
            except Exception:
                logger.debug("[DB] 异常回滚失败", exc_info=True)
            raise


async def ping_db() -> bool:
    """健康检查: 用一个独立连接执行 SELECT 1。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"[DB] 健康检查失败: {e}")
        return False


def _migrate_sqlite():
    """SQLite不支持ALTER COLUMN, 这里对存量库补新增列 (幂等)。"""
    if not _IS_SQLITE:
        return
    db_path = settings.database_url.split("///")[-1]
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for table, columns in {
            "switch_ports": [("physical", "INTEGER DEFAULT 1")],
            "servers": [
                ("diag_user", "VARCHAR(128) DEFAULT ''"),
                ("diag_password", "VARCHAR(256) DEFAULT ''"),
                ("diag_port", "INTEGER DEFAULT 22"),
            ],
            "alerts": [            # 可配置告警规则改造新增的列
                ("rule_id", "INTEGER"),
                ("metric", "TEXT DEFAULT ''"),
                ("value", "REAL"),
                ("hit_count", "INTEGER DEFAULT 1"),
                ("last_notify_at", "DATETIME"),
                ("last_notify_level", "TEXT"),
                ("updated_at", "DATETIME"),
            ],
            "notify_channels": [    # 短信/电话渠道: 被叫号码独立成一列, 模板里用变量引用
                ("targets", "TEXT DEFAULT ''"),
            ],
            "env_points": [         # 动环点位: 分组字段(科士达UPS等多点位设备分类显示)
                # group 是 SQLite 保留字, 必须带双引号
                ('"group"', "VARCHAR(32) DEFAULT ''"),
            ],
            "storage_devices": [
                ("protocol", "TEXT DEFAULT 'none'"),
                ("snmp_community", "TEXT DEFAULT 'public'"),
                ("snmp_version", "TEXT DEFAULT '2c'"),
                ("username", "TEXT DEFAULT ''"),
                ("password", "TEXT DEFAULT ''"),
                ("details", "TEXT DEFAULT '{}'"),
                # 华为 OceanStor 默认关闭 SNMPv1&v2c 开关, 只留 USM 用户,
                # 所以监控这类存储必须能走 v3
                ("snmp_v3_user", "TEXT DEFAULT ''"),
                ("snmp_v3_auth_proto", "TEXT DEFAULT 'sha'"),
                ("snmp_v3_auth_pass", "TEXT DEFAULT ''"),
                ("snmp_v3_priv_proto", "TEXT DEFAULT 'aes'"),
                ("snmp_v3_priv_pass", "TEXT DEFAULT ''"),
                ("snmp_context", "TEXT DEFAULT ''"),
            ],
        }.items():
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue  # 新库由create_all建表
            cur.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            for col, ddl in columns:
                # col 可能带双引号(如 "group", SQLite 保留字), 必须去引号后再与
                # PRAGMA 返回的列名比较, 否则 '"group"' 永远匹配不到现有列 'group',
                # 会让重复 ALTER 报 "duplicate column name" 并导致后端启动失败。
                col_unquoted = col.strip('"')
                if col_unquoted in existing:
                    continue
                try:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                    logger.info(f"[DB] 迁移: {table} 新增列 {col_unquoted}")
                except sqlite3.OperationalError as e:
                    # 并发/重复部署等极端情况下, 列可能已被其它进程加上
                    if "duplicate column" in str(e).lower():
                        logger.warning(f"[DB] 迁移: {table}.{col_unquoted} 已存在, 跳过")
                    else:
                        raise

        # ---- 索引优化 (幂等) ----
        # 历史曲线查询: WHERE server_id=? AND collected_at BETWEEN ... ORDER BY collected_at
        indexes = [
            ("ix_metrics_server_time", "server_metrics", "(server_id, collected_at)"),
            ("ix_env_readings_sensor_time", "env_readings", "(sensor_id, collected_at)"),
            ("ix_alerts_status_created", "alerts", "(status, created_at)"),
        ]
        for name, table, cols in indexes:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,)
            )
            if cur.fetchone():
                continue
            cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue
            try:
                cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")
                logger.info(f"[DB] 已创建索引 {name} ON {table}{cols}")
            except sqlite3.Error as e:
                logger.warning(f"[DB] 创建索引 {name} 失败: {e}")
        conn.commit()
    finally:
        conn.close()


async def init_db():
    from app import models  # noqa: F401 - ensure models registered
    _migrate_sqlite()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("[DB] 数据库初始化完成 (WAL + 连接池 + 索引)")


async def dispose_engine():
    """优雅关闭: 释放所有连接 (供 lifespan 退出时调用)。"""
    try:
        await engine.dispose()
        logger.info("[DB] 数据库连接池已释放")
    except Exception:
        logger.debug("[DB] 释放连接池失败", exc_info=True)
