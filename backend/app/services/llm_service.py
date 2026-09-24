"""LLM 集成：支持多种本地 / 在线大模型 (OpenAI 兼容协议)。

提供商 (provider) 一览见下方 PROVIDERS 注册表。所有 provider 都通过统一的
OpenAI 兼容 /v1 接口对接 (Ollama 也走其 OpenAI 兼容模式), 因此后端只需维护
一个 AsyncOpenAI 客户端, 不同 provider 的差异仅在:
  - 默认 base_url (本地地址 vs 在线云地址)
  - 是否需要 API Key (本地服务通常无鉴权)
  - 模型列表如何拉取 (OpenAI 兼容走 /models, Ollama 走 /api/tags)

LLM 配置支持运行时修改: 数据库 system_config 表优先, 未配置时回退 .env 默认值。
前端在 AI 助手页面的"模型配置"中修改, 保存后立即生效, 无需重启服务。
"""
from typing import List, Optional, Tuple

import httpx
from openai import AsyncOpenAI

from app.core.config import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# 提供商注册表
#   前端"模型配置"对话框按此渲染下拉选项, 选择后自动回填 base_url / api_key 提示。
#   不包含任何密钥, 可安全下发给浏览器。
#   models_mode: "openai" -> GET {base_url}/models ; "ollama" -> GET {root}/api/tags
# ---------------------------------------------------------------------------
PROVIDERS: dict = {
    "local_ollama": {
        "label": "本地 Ollama",
        "group": "本地部署",
        "kind": "ollama",
        "models_mode": "ollama",
        "default_base_url": "http://localhost:11434/v1",
        "needs_key": False,
        "key_placeholder": "本地服务无需密钥，填 EMPTY 或不填",
        "model_example": "qwen2.5:7b",
        "doc": "本机运行的 Ollama，默认地址 http://localhost:11434/v1（OpenAI 兼容模式）。"
               "模型列表通过 Ollama 原生 /api/tags 自动拉取。",
    },
    "local_openai": {
        "label": "本地 OpenAI 兼容 (vLLM / Xinference / LM Studio)",
        "group": "本地部署",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "http://localhost:8000/v1",
        "needs_key": False,
        "key_placeholder": "无鉴权填 EMPTY",
        "model_example": "qwen2.5-7b-instruct",
        "doc": "自托管的 OpenAI 兼容推理服务，如 vLLM、Xinference、LM Studio。"
               "模型列表通过 /models 自动拉取。",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "group": "在线大模型",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "https://api.openai.com/v1",
        "needs_key": True,
        "key_placeholder": "sk-...（必填）",
        "model_example": "gpt-4o-mini",
        "doc": "OpenAI 官方 API，需要 API Key。",
    },
    "deepseek": {
        "label": "DeepSeek",
        "group": "在线大模型",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "https://api.deepseek.com/v1",
        "needs_key": True,
        "key_placeholder": "sk-...（必填）",
        "model_example": "deepseek-chat",
        "doc": "DeepSeek 官方 API，base_url 为 https://api.deepseek.com/v1。",
    },
    "qwen": {
        "label": "通义千问 (阿里云百炼 DashScope)",
        "group": "在线大模型",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "needs_key": True,
        "key_placeholder": "sk-...（必填，DashScope API Key）",
        "model_example": "qwen-plus",
        "doc": "阿里云百炼平台，使用兼容模式地址。模型如 qwen-plus / qwen-max / qwen2.5-7b-instruct。",
    },
    "moonshot": {
        "label": "Moonshot (Kimi)",
        "group": "在线大模型",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "https://api.moonshot.cn/v1",
        "needs_key": True,
        "key_placeholder": "sk-...（必填）",
        "model_example": "moonshot-v1-8k",
        "doc": "月之暗面 Kimi 开放平台 API。",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "group": "在线大模型",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "needs_key": True,
        "key_placeholder": "sk-...（必填）",
        "model_example": "glm-4-flash",
        "doc": "智谱 AI 开放平台，base_url 为 https://open.bigmodel.cn/api/paas/v4。",
    },
    "custom": {
        "label": "自定义 / 其他 OpenAI 兼容",
        "group": "其他",
        "kind": "openai_compatible",
        "models_mode": "openai",
        "default_base_url": "http://localhost:8000/v1",
        "needs_key": False,
        "key_placeholder": "按需填写，无鉴权填 EMPTY",
        "model_example": "your-model",
        "doc": "任意兼容 OpenAI /v1 协议的推理服务，手动填写地址与模型。",
    },
}


def get_providers() -> List[dict]:
    """返回提供商列表 (含分组), 供前端渲染下拉。不含任何密钥。"""
    out: List[dict] = []
    for key, p in PROVIDERS.items():
        out.append({
            "key": key,
            "label": p["label"],
            "group": p["group"],
            "needs_key": p["needs_key"],
            "default_base_url": p["default_base_url"],
            "key_placeholder": p["key_placeholder"],
            "model_example": p["model_example"],
            "doc": p["doc"],
        })
    return out


async def list_models(provider: str, base_url: str, api_key: str) -> List[str]:
    """拉取指定 endpoint 上可用的模型列表。

    - OpenAI 兼容: GET {base_url}/models (带 Bearer 鉴权)
    - Ollama:      GET {root}/api/tags   (root = 去掉末尾 /v1)
    失败抛出带说明的异常, 由调用方包装成 {ok, detail}。
    """
    prov = PROVIDERS.get(provider, PROVIDERS["custom"])
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("服务地址 (base_url) 为空，无法拉取模型列表")

    if prov["models_mode"] == "ollama":
        root = base[:-3] if base.endswith("/v1") else base
        url = f"{root}/api/tags"
        async with httpx.AsyncClient(trust_env=False, timeout=20.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    else:
        url = f"{base}/models"
        headers = {}
        if api_key and api_key != "EMPTY":
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(trust_env=False, timeout=20.0) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


# 运行时生效的LLM配置 (由 reload_config 从数据库刷新)
_runtime = {
    "provider": settings.llm_provider,
    "base_url": settings.llm_base_url,
    "model": settings.llm_model,
    "api_key": settings.llm_api_key,
}

SYSTEM_PROMPT = """你是一个专业的AIOps运维助手，服务于数据中心机房管理员。
你熟悉以下环境：
- VMware虚拟化平台（vCenter/ESXi）
- Linux服务器：CentOS、openEuler、Rocky Linux、Ubuntu、龙蜥(Anolis)
- Windows服务器：2008/2012/2016/2019
- 网络设备：华三(H3C)、华为、Dell交换机
- 存储设备：华为存储、华三存储
- 机房温湿度监控

你可以帮助用户：
1. 分析告警和故障，给出排查建议
2. 解读监控指标（CPU/内存/磁盘/网络）
3. 提供运维操作建议和命令
4. 回答虚拟化、网络、存储相关问题

请用简洁专业的中文回答。"""


async def reload_config():
    """从数据库加载LLM配置, 覆盖.env默认值。启动时和保存配置后调用。"""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models import SystemConfig

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(SystemConfig))).scalars().all()
    stored = {r.key: r.value for r in rows}
    _runtime["provider"] = stored.get("llm_provider") or settings.llm_provider
    _runtime["base_url"] = stored.get("llm_base_url") or settings.llm_base_url
    _runtime["model"] = stored.get("llm_model") or settings.llm_model
    _runtime["api_key"] = stored.get("llm_api_key") or settings.llm_api_key


# 按 (地址, 密钥) 缓存客户端。
# 之前每次对话都 new 一个 AsyncOpenAI —— 它内部持有 httpx 连接池, 频繁创建会
# 泄漏连接(与 SNMP 引擎泄漏是同一类问题)。这里改为复用。
_CLIENTS: dict = {}


def _get_client(base_url: Optional[str] = None, api_key: Optional[str] = None) -> AsyncOpenAI:
    url = base_url or _runtime["base_url"]
    key = api_key or _runtime["api_key"]
    cache_key = (url, key)
    client = _CLIENTS.get(cache_key)
    if client is None:
        client = AsyncOpenAI(
            base_url=url,
            api_key=key,
            timeout=120.0,
            # trust_env=False: 内网大模型服务必须直连, 不能走服务器上的代理
            http_client=httpx.AsyncClient(trust_env=False, timeout=130.0),
        )
        _CLIENTS[cache_key] = client
    return client


async def aclose_clients():
    """优雅关闭时释放 LLM 客户端连接。"""
    for client in list(_CLIENTS.values()):
        try:
            await client.close()
        except Exception:
            pass
    _CLIENTS.clear()


async def chat_completion(
    message: str,
    history: Optional[List[dict]] = None,
    context: str = "",
) -> str:
    """Send a chat request to the internal Qwen model.

    Args:
        message: user message
        history: previous messages [{role, content}, ...]
        context: optional monitoring context injected into the prompt
    """
    client = _get_client()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({
            "role": "system",
            "content": f"以下是当前监控平台的实时数据，供你分析参考：\n{context}",
        })
    if history:
        messages.extend(history[-10:])  # keep last 10 turns
    messages.append({"role": "user", "content": message})

    try:
        resp = await client.chat.completions.create(
            model=_runtime["model"],
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
        )
        return resp.choices[0].message.content or "（模型未返回内容）"
    except Exception as e:
        prov = PROVIDERS.get(_runtime["provider"], {}).get("label", "大模型")
        return f"无法连接【{prov}】服务（{_runtime['base_url']}）：{e}\n请确认模型服务已启动且地址/模型名正确。"


async def test_connection(base_url: str, api_key: str, model: str) -> Tuple[bool, str]:
    """用指定配置实际调用一次模型, 验证连通性。"""
    try:
        # 测试用独立客户端, 用完立即关闭, 不进缓存
        async with httpx.AsyncClient(trust_env=False, timeout=35.0) as hc:
            client = AsyncOpenAI(
                base_url=base_url, api_key=api_key or "EMPTY",
                timeout=30.0, http_client=hc,
            )
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "你好"}],
                temperature=0.3,
                max_tokens=16,
            )
        return True, f"连接成功，模型 {model} 响应正常"
    except Exception as e:
        return False, f"连接失败: {e}"


async def analyze_alert(alert_info: str) -> str:
    """Ask the LLM to analyze an alert and give troubleshooting advice."""
    return await chat_completion(
        f"请分析以下告警信息，给出可能的原因和排查步骤：\n\n{alert_info}"
    )
