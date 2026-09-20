"""Async SQLAlchemy database setup."""
import sqlite3

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# SQLite需要特殊参数防止长时间不用后连接失效(502根因)
_engine_kwargs = dict(echo=False, future=True)
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["pool_pre_ping"] = True      # 检测失效连接并自动重建
    _engine_kwargs["pool_recycle"] = 3600         # 每小时回收连接防止老化
    _engine_kwargs["connect_args"] = {
        "timeout": 30,       # busy_timeout: 等锁最多30秒而非立即报错
        "check_same_thread": False,
    }

engine = create_async_engine(settings.database_url, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# SQLite启用WAL模式: 允许并发读写, 长时间不用后不会锁死(502根因修复)
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")     # WAL模式, 读写不互斥
        cursor.execute("PRAGMA busy_timeout=30000")   # 等锁30秒
        cursor.execute("PRAGMA synchronous=NORMAL")    # WAL下NORMAL足够安全
        cursor.execute("PRAGMA cache_size=-64000")    # 64MB缓存
    finally:
        cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _migrate_sqlite():
    """SQLite不支持ALTER COLUMN, 这里对存量库补新增列 (幂等)。"""
    if not settings.database_url.startswith("sqlite"):
        return
    db_path = settings.database_url.split("///")[-1]
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for table, columns in {
            "switch_ports": [("physical", "INTEGER DEFAULT 1")],
            "storage_devices": [
                ("protocol", "TEXT DEFAULT 'none'"),
                ("snmp_community", "TEXT DEFAULT 'public'"),
                ("snmp_version", "TEXT DEFAULT '2c'"),
                ("username", "TEXT DEFAULT ''"),
                ("password", "TEXT DEFAULT ''"),
                ("details", "TEXT DEFAULT '{}'"),
            ],
        }.items():
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cur.fetchone():
                continue  # 新库由create_all建表
            cur.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            for col, ddl in columns:
                if col not in existing:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        conn.commit()
    finally:
        conn.close()


async def init_db():
    from app import models  # noqa: F401 - ensure models registered
    _migrate_sqlite()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
