"""System settings API: LLM 多提供商运行时配置 (本地 Ollama / 在线大模型 等)。"""
import json
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models import SystemConfig
from app.schemas.schemas import LlmConfigIn, LlmModelsIn, LlmProfileIn
from app.services import llm_service

router = APIRouter(prefix="/api/settings", tags=["settings"])
settings = get_settings()

_PROFILES_KEY = "llm_profiles"          # JSON 列表: [{name,provider,base_url,model,api_key}]
_ACTIVE_KEY = "llm_active_profile"      # 当前生效配置的名称


async def _get_all(db: AsyncSession) -> dict:
    result = await db.execute(select(SystemConfig))
    return {row.key: row.value for row in result.scalars().all()}


async def _set(db: AsyncSession, key: str, value: str):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))


def _load_profiles(stored: dict) -> list:
    try:
        return json.loads(stored.get(_PROFILES_KEY) or "[]") or []
    except (json.JSONDecodeError, TypeError):
        return []


async def _write_active(db: AsyncSession, profile: dict):
    """把某套配置写进当前生效字段并热加载（让对话/诊断立即用新模型）。"""
    await _set(db, _ACTIVE_KEY, profile["name"])
    await _set(db, "llm_provider", profile["provider"])
    await _set(db, "llm_base_url", profile["base_url"])
    await _set(db, "llm_model", profile["model"])
    await _set(db, "llm_api_key", profile["api_key"])
    await llm_service.reload_config()


@router.get("/llm/providers")
async def list_llm_providers():
    """返回支持的 LLM 提供商注册表 (前端下拉用, 不含密钥)。"""
    return llm_service.get_providers()


@router.get("/llm")
async def get_llm_config(db: AsyncSession = Depends(get_db)):
    """返回当前生效的LLM配置 (数据库优先, 未配置时回退.env默认值)。"""
    stored = await _get_all(db)
    return {
        "provider": stored.get("llm_provider") or settings.llm_provider,
        "base_url": stored.get("llm_base_url") or settings.llm_base_url,
        "model": stored.get("llm_model") or settings.llm_model,
        "api_key": stored.get("llm_api_key") or settings.llm_api_key,
        "active": stored.get(_ACTIVE_KEY) or "",
    }


@router.get("/llm/profiles")
async def list_llm_profiles(db: AsyncSession = Depends(get_db)):
    """返回已保存的命名配置列表 + 当前生效配置名 + 当前生效字段。

    前端据此渲染"已保存配置"下拉(一键切换) 与初始表单值。
    """
    stored = await _get_all(db)
    profiles = _load_profiles(stored)
    return {
        "profiles": profiles,
        "active": stored.get(_ACTIVE_KEY) or "",
        "current": {
            "provider": stored.get("llm_provider") or settings.llm_provider,
            "base_url": stored.get("llm_base_url") or settings.llm_base_url,
            "model": stored.get("llm_model") or settings.llm_model,
            "api_key": stored.get("llm_api_key") or settings.llm_api_key,
        },
    }


@router.post("/llm/profiles")
async def save_llm_profile(data: LlmProfileIn, db: AsyncSession = Depends(get_db)):
    """保存(新建或按 name 覆盖)一套命名配置，并立即设为生效配置。

    前端流程: 填好提供商/地址/密钥/模型 → 测试连接通过 → 点"保存配置"(带名称) →
    这里落库并热加载, 之后即可从下拉一键切换回来。
    """
    name = (data.name or "").strip()
    if not name:
        return {"ok": False, "detail": "请填写配置名称"}
    if not data.base_url or not data.model:
        return {"ok": False, "detail": "服务地址和模型名称不能为空"}

    stored = await _get_all(db)
    profiles = _load_profiles(stored)
    profile = {
        "name": name,
        "provider": data.provider,
        "base_url": data.base_url,
        "model": data.model,
        "api_key": data.api_key or "EMPTY",
    }
    replaced = False
    for i, p in enumerate(profiles):
        if p.get("name") == name:
            profiles[i] = profile
            replaced = True
            break
    if not replaced:
        profiles.append(profile)
    await _set(db, _PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))
    await _write_active(db, profile)
    await db.commit()
    return {"ok": True, "detail": f"已保存配置「{name}」并生效"}


@router.post("/llm/profiles/{name}/activate")
async def activate_llm_profile(name: str, db: AsyncSession = Depends(get_db)):
    """一键切换到某套已保存配置, 立即生效。"""
    stored = await _get_all(db)
    profiles = _load_profiles(stored)
    target = next((p for p in profiles if p.get("name") == name), None)
    if not target:
        return {"ok": False, "detail": f"未找到配置「{name}」"}
    await _write_active(db, target)
    await db.commit()
    return {"ok": True, "detail": f"已切换到配置「{name}」"}


@router.delete("/llm/profiles/{name}")
async def delete_llm_profile(name: str, db: AsyncSession = Depends(get_db)):
    """删除一套命名配置(不能删当前生效的那套)。"""
    stored = await _get_all(db)
    profiles = _load_profiles(stored)
    if not any(p.get("name") == name for p in profiles):
        return {"ok": False, "detail": f"未找到配置「{name}」"}
    if (stored.get(_ACTIVE_KEY) or "") == name:
        return {"ok": False, "detail": "不能删除当前正在使用的配置，请先切换到其它配置再删除"}
    profiles = [p for p in profiles if p.get("name") != name]
    await _set(db, _PROFILES_KEY, json.dumps(profiles, ensure_ascii=False))
    await db.commit()
    return {"ok": True, "detail": f"已删除配置「{name}」"}


@router.put("/llm")
async def save_llm_config(data: LlmConfigIn, db: AsyncSession = Depends(get_db)):
    await _set(db, "llm_provider", data.provider)
    await _set(db, "llm_base_url", data.base_url)
    await _set(db, "llm_model", data.model)
    await _set(db, "llm_api_key", data.api_key)
    await db.commit()
    # 立即刷新内存中的运行时配置
    await llm_service.reload_config()
    return {"ok": True}


@router.post("/llm/models")
async def fetch_llm_models(data: LlmModelsIn):
    """拉取指定 endpoint 上可用的模型列表 (前端"获取模型列表"按钮用)。"""
    try:
        models = await llm_service.list_models(
            data.provider, data.base_url, data.api_key
        )
    except Exception as e:
        return {"ok": False, "detail": f"拉取模型列表失败: {e}", "models": []}
    return {"ok": True, "detail": f"共 {len(models)} 个可用模型", "models": models}


@router.post("/llm/test")
async def test_llm_config(data: LlmConfigIn):
    """用给定配置实际调用一次模型, 验证连通性。"""
    ok, detail = await llm_service.test_connection(
        data.base_url, data.api_key, data.model
    )
    return {"ok": ok, "detail": detail}
