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

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, event, text
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


def _column_ddl(col):
    """按 SQLAlchemy 列定义推导 SQLite 列 DDL (含默认值)。"""
    t = col.type
    if isinstance(t, Boolean):
        sql_t = "BOOLEAN"
    elif isinstance(t, Integer):
        sql_t = "INTEGER"
    elif isinstance(t, Float):
        sql_t = "REAL"
    elif isinstance(t, DateTime):
        sql_t = "DATETIME"
    else:
        # String / Text / JSON 等在 SQLite 里都落为 TEXT
        sql_t = "TEXT"

    ddl = f'"{col.name}" {sql_t}'
    # 标量默认值才可安全加列(SQLite 对存量行要求有默认值); 可调用默认(datetime.utcnow)不行
    if (
        col.default is not None
        and getattr(col.default, "is_scalar", False)
        and not callable(col.default.arg)
    ):
        v = col.default.arg
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, str):
            v = "'" + v.replace("'", "''") + "'"
        ddl += f" DEFAULT {v}"
    return ddl


def _migrate_sqlite():
    """存量库自动补列 (幂等, 通用)。

    背景(2026-10-10 修复): 旧版这里手写了一张 "表 -> 新增列" 字典,
    结果 `storage_devices` 这个 key 被写了两次, Python 字典重复 key
    后者覆盖前者, `enabled` 列的迁移被静默丢掉 —— 线上老库缺列导致
    存储设备列表查询 500 (页面表现为"之前添加的华为存储没了")、
    新增 INSERT 报 "table storage_devices has no column named enabled"。
    同类地雷还有 env_devices / env_points / notify_channels 的 enabled。

    教训: 手写映射必然漏。现在直接遍历 ORM 元数据(Base.metadata),
    与 PRAGMA table_info 对比, 缺哪列补哪列, 以后加列只改模型即可。
    """
    if not _IS_SQLITE:
        return
    from app import models  # noqa: F401 - 确保所有模型已注册到 Base.metadata

    db_path = settings.database_url.split("///")[-1]
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for table in Base.metadata.sorted_tables:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table.name,),
            )
            if not cur.fetchone():
                continue  # 新库由 create_all 建表, 无需补列
            cur.execute(f'PRAGMA table_info("{table.name}")')
            existing = {row[1] for row in cur.fetchall()}
            for col in table.columns:
                if col.name in existing:
                    continue
                if not col.nullable and not (
                    col.default is not None
                    and getattr(col.default, "is_scalar", False)
                ):
                    # 非空且无默认值的列无法安全补(存量行没值), 理论上不应出现
                    logger.warning(
                        f"[DB] 迁移跳过 {table.name}.{col.name}: 非空且无默认值"
                    )
                    continue
                ddl = _column_ddl(col)
                try:
                    cur.execute(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
                    logger.info(f"[DB] 迁移: {table.name} 新增列 {col.name}")
                except sqlite3.OperationalError as e:
                    # 并发/重复部署等极端情况下, 列可能已被其它进程加上
                    if "duplicate column" in str(e).lower():
                        logger.warning(
                            f"[DB] 迁移: {table.name}.{col.name} 已存在, 跳过"
                        )
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
