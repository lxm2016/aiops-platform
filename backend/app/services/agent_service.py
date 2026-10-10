"""可执行运维 Agent: 基于大模型 function-calling 的工具调用循环。

=======================================================================
设计原则（与 diagnostic_service 一致，安全优先）
=======================================================================
1. 本 Agent 只暴露【只读】工具: 查询监控清单、查告警、查实时指标、做只读诊断、查进程。
2. 所有工具都走硬编码的 DB 查询 / 平台已有的只读诊断逻辑, 绝不执行任何写操作、
   重启、杀进程、改配置、下发命令——从机制上杜绝"AI 乱执行"。
3. 模型只能通过工具读取数据; 若用户要求"去重启/杀进程/关端口", Agent 由 system
   prompt 约束, 明确拒绝自动执行, 并给出人工处置建议。
4. 工具循环有最大轮次上限 (max_iter), 防止模型陷入无限工具调用。
=======================================================================
"""
import json
import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Server, ServerMetric, NetworkDevice, StorageDevice, VmwareHost,
    VirtualMachine, EnvSensor, Alert,
)
from app.services import diagnostic_service
from app.services import llm_service

logger = logging.getLogger(__name__)


AGENT_SYSTEM_PROMPT = """你是一个可执行的数据中心运维 Agent（AIOps 助手）。你能通过一组【只读】工具实时查询平台监控数据：
- 服务器 / 网络设备 / 存储 / VMware / 动环设备 的清单与状态
- VMware 虚拟机清单(电源状态/资源占用)、存储设备的存储池/磁盘/卷利用率明细
- 全局概览(服务器/虚拟机/存储/告警 数量)
- 未处理告警
- 单台服务器的实时指标、只读诊断（CPU/内存/磁盘/进程）、进程列表

工作准则（务必遵守）：
1. 涉及"当前状况 / 哪台在告警 / 资源是否紧张 / 某设备指标"等问题时, 必须先调用对应工具获取真实数据, 再回答, 禁止凭空编造数字。
2. 你只有【只读】权限: 只能查询, 不能修改。若用户要求"重启 / 杀进程 / 关端口 / 删除 / 改配置"等写操作, 你必须明确拒绝自动执行, 并给出【人工处置步骤】建议, 由运维人员手动确认后操作。
3. 工具返回的数据通常已足够, 请直接基于数据给结论; 数据不足时再补调用工具, 不要反复调用同一个工具。
4. 用简洁、专业、分点的中文回答; 引用具体设备名 / IP / 数值。
"""


# ---------------------------------------------------------------------------
# 工具定义 (OpenAI function-calling schema)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_servers",
            "description": "列出被监控的服务器清单, 可按状态/是否启用过滤。返回 id、名称、IP、系统类型、在线状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["online", "offline", "unknown"],
                               "description": "按在线状态过滤, 留空表示全部"},
                    "enabled": {"type": "boolean",
                                "description": "true=仅看启用监测的, false=仅看已暂停监测的, 留空=全部"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_network_devices",
            "description": "列出网络设备(交换机/路由器)清单与状态、CPU/内存占用、端口 up 数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["online", "offline", "unknown"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_storage_devices",
            "description": "列出存储设备清单: 厂商/型号、已用百分比、总容量、状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["online", "offline", "unknown"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_vmware_hosts",
            "description": "列出 VMware vCenter/ESXi 清单及状态、名下虚拟机数量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["online", "offline", "unknown"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_env_sensors",
            "description": "列出机房动环传感器(温湿度)实时读数。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_alerts",
            "description": "列出平台告警。默认返回未处理(open)告警。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "ack", "resolved"],
                               "description": "告警处理状态, 默认 open"},
                    "level": {"type": "string", "enum": ["info", "warning", "critical"],
                              "description": "按级别过滤, 留空=全部"},
                    "limit": {"type": "integer", "description": "最多返回条数, 默认 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_server_detail",
            "description": "查看单台服务器的详细信息(系统、CPU核数、内存、最后上报时间、是否启用监测)。",
            "parameters": {
                "type": "object",
                "properties": {"server_id": {"type": "integer", "description": "服务器 id"}},
                "required": ["server_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_server_metrics",
            "description": "查看单台服务器最新一次采集的实时指标(CPU/内存/磁盘/网络/负载/进程数)。",
            "parameters": {
                "type": "object",
                "properties": {"server_id": {"type": "integer", "description": "服务器 id"}},
                "required": ["server_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_server",
            "description": "对单台服务器执行【只读】诊断(通过 SSH/WinRM 采集 CPU/内存/磁盘/进程 原始数据), 用于排查故障。不改变系统。",
            "parameters": {
                "type": "object",
                "properties": {"server_id": {"type": "integer", "description": "服务器 id"}},
                "required": ["server_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "查看单台服务器资源占用最高的 TOP 进程(名称/PID/CPU/内存), 用于定位吃资源的进程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_id": {"type": "integer", "description": "服务器 id"},
                    "top": {"type": "integer", "description": "返回前 N 个, 默认 15"},
                },
                "required": ["server_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_vms",
            "description": "列出 VMware 虚拟机清单(可按宿主机/电源状态过滤): 名称、电源状态、客户机系统、IP、CPU/内存占用、所属宿主机。用于回答“某 vCenter 下有哪些虚机/有多少台开机”等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host_id": {"type": "integer", "description": "只看某台 vCenter/ESXi 下的虚拟机"},
                    "power_state": {"type": "string", "enum": ["poweredOn", "poweredOff", "suspended"],
                                    "description": "按电源状态过滤, 留空=全部"},
                    "limit": {"type": "integer", "description": "最多返回条数, 默认 50"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_storage_details",
            "description": "查看存储设备(如华为/新华三/Sangfor)的存储池/磁盘/卷明细与利用率(used_percent)。用于回答“某存储阵列容量还剩多少/哪个池快满/磁盘健康”等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "storage_id": {"type": "integer", "description": "指定存储设备 id; 留空=返回全部"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": "获取全局概览: 服务器总数/在线/离线、虚拟机数、存储设备数、未处理告警数。用于回答“整体情况/一共有多少设备/有多少未处理告警”类问题。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ---------------------------------------------------------------------------
# 单个工具实现 (全部只读)
# ---------------------------------------------------------------------------
async def _tool_list_servers(db: AsyncSession, status=None, enabled=None):
    stmt = select(Server)
    if status:
        stmt = stmt.where(Server.status == status)
    if enabled is not None:
        stmt = stmt.where(Server.enabled == enabled)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "servers": [
            {"id": s.id, "name": s.name, "ip": s.ip, "os_type": s.os_type,
             "status": s.status, "enabled": s.enabled, "last_seen": s.last_seen}
            for s in rows
        ],
    }


async def _tool_list_network(db: AsyncSession, status=None):
    stmt = select(NetworkDevice)
    if status:
        stmt = stmt.where(NetworkDevice.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "devices": [
            {"id": d.id, "name": d.name, "ip": d.ip, "vendor": d.vendor,
             "status": d.status, "cpu_percent": d.cpu_percent, "mem_percent": d.mem_percent,
             "port_up": d.port_up, "port_total": d.port_total}
            for d in rows
        ],
    }


async def _tool_list_storage(db: AsyncSession, status=None):
    stmt = select(StorageDevice)
    if status:
        stmt = stmt.where(StorageDevice.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "devices": [
            {"id": d.id, "name": d.name, "ip": d.ip, "vendor": d.vendor, "model": d.model,
             "used_percent": d.used_percent, "capacity_tb": d.capacity_tb, "status": d.status}
            for d in rows
        ],
    }


async def _tool_list_vmware(db: AsyncSession, status=None):
    stmt = select(VmwareHost)
    if status:
        stmt = stmt.where(VmwareHost.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for h in rows:
        vm_count = (await db.execute(
            select(func.count(VirtualMachine.id)).where(VirtualMachine.host_id == h.id)
        )).scalar() or 0
        out.append({"id": h.id, "name": h.name, "host": h.host, "status": h.status, "vm_count": vm_count})
    return {"count": len(out), "hosts": out}


async def _tool_list_env(db: AsyncSession):
    rows = (await db.execute(select(EnvSensor))).scalars().all()
    return {
        "count": len(rows),
        "sensors": [
            {"id": s.id, "name": s.name, "location": s.location,
             "temperature": s.temperature, "humidity": s.humidity, "status": s.status}
            for s in rows
        ],
    }


async def _tool_list_alerts(db: AsyncSession, status="open", level=None, limit=10):
    stmt = select(Alert).where(Alert.status == (status or "open"))
    if level:
        stmt = stmt.where(Alert.level == level)
    stmt = stmt.order_by(Alert.created_at.desc()).limit(int(limit or 10))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "alerts": [
            {"id": a.id, "level": a.level, "category": a.category, "source": a.source,
             "title": a.title, "status": a.status, "created_at": a.created_at}
            for a in rows
        ],
    }


async def _tool_server_detail(db: AsyncSession, server_id):
    s = await db.get(Server, server_id)
    if not s:
        return {"error": f"服务器 id={server_id} 不存在"}
    return {"id": s.id, "name": s.name, "ip": s.ip, "os_type": s.os_type,
            "os_distro": s.os_distro, "os_version": s.os_version,
            "cpu_cores": s.cpu_cores, "cpu_model": s.cpu_model, "mem_total_gb": s.mem_total_gb,
            "status": s.status, "enabled": s.enabled, "agent_installed": s.agent_installed,
            "last_seen": s.last_seen, "tags": s.tags}


async def _tool_server_metrics(db: AsyncSession, server_id):
    s = await db.get(Server, server_id)
    if not s:
        return {"error": f"服务器 id={server_id} 不存在"}
    m = (await db.execute(
        select(ServerMetric).where(ServerMetric.server_id == server_id)
        .order_by(ServerMetric.collected_at.desc()).limit(1)
    )).scalars().first()
    if not m:
        return {"id": server_id, "name": s.name, "status": s.status,
                "metrics": None, "note": "暂无采集数据(可能刚接入或已暂停监测)"}
    return {"id": server_id, "name": s.name, "status": s.status, "collected_at": m.collected_at,
            "cpu_percent": m.cpu_percent, "mem_percent": m.mem_percent,
            "disk_percent": m.disk_percent, "disk_used_gb": m.disk_used_gb,
            "disk_total_gb": m.disk_total_gb, "net_rx_mbps": m.net_rx_mbps,
            "net_tx_mbps": m.net_tx_mbps, "load_avg": m.load_avg,
            "process_count": m.process_count}


async def _tool_diagnose(db: AsyncSession, server_id):
    s = await db.get(Server, server_id)
    if not s:
        return {"error": f"服务器 id={server_id} 不存在"}
    diag = await diagnostic_service.diagnose_server(s)
    if not diag.get("ok"):
        return {"ok": False, "error": diag.get("error")}
    sections = diag.get("sections", {})
    summary = {}
    for key, label in diagnostic_service.SECTION_LABELS.items():
        txt = (sections.get(key) or "").strip()
        if txt:
            summary[label] = txt[:1500]
    return {"ok": True, "os_type": diag.get("os_type"), "host": diag.get("host"),
            "sections": summary}


async def _tool_processes(db: AsyncSession, server_id, top=15):
    s = await db.get(Server, server_id)
    if not s:
        return {"error": f"服务器 id={server_id} 不存在"}
    res = await diagnostic_service.process_list(s)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    procs = (res.get("processes") or [])[: max(1, int(top or 15))]
    return {"ok": True, "count": res.get("count"), "os_type": res.get("os_type"),
            "processes": procs}


async def _tool_list_vms(db: AsyncSession, host_id=None, power_state=None, limit=50):
    stmt = select(VirtualMachine)
    if host_id:
        stmt = stmt.where(VirtualMachine.host_id == host_id)
    if power_state:
        stmt = stmt.where(VirtualMachine.power_state == power_state)
    stmt = stmt.order_by(VirtualMachine.host_id, VirtualMachine.name).limit(int(limit or 50))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "vms": [
            {"id": v.id, "name": v.name, "host_id": v.host_id, "power_state": v.power_state,
             "guest_os": v.guest_os, "ip": v.ip, "cpu_cores": v.cpu_cores, "mem_mb": v.mem_mb,
             "cpu_percent": v.cpu_percent, "mem_percent": v.mem_percent, "uptime": v.uptime,
             "last_seen": v.last_seen}
            for v in rows
        ],
    }


def _pct(used, total):
    try:
        if used is None or total in (None, 0):
            return None
        return round(used / total * 100, 1)
    except Exception:
        return None


def _agg_detail(items):
    out = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        used = it.get("used")
        total = it.get("total")
        used_percent = it.get("used_percent")
        if used_percent is None:
            used_percent = _pct(used, total)
        out.append({
            "name": it.get("name") or it.get("id") or it.get("pool_name") or "?",
            "used_percent": used_percent,
            "total": total,
            "used": used,
            "status": it.get("status"),
        })
    return out


async def _tool_storage_details(db: AsyncSession, storage_id=None):
    stmt = select(StorageDevice)
    if storage_id:
        stmt = stmt.where(StorageDevice.id == storage_id)
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for d in rows:
        details = d.details or {}
        out.append({
            "id": d.id, "name": d.name, "ip": d.ip, "vendor": d.vendor, "model": d.model,
            "status": d.status, "capacity_tb": d.capacity_tb, "used_percent": d.used_percent,
            "pools": _agg_detail(details.get("pools")),
            "disks": _agg_detail(details.get("disks")),
            "volumes": _agg_detail(details.get("volumes")),
        })
    return {"count": len(out), "storage": out}


async def _tool_summary(db: AsyncSession):
    total = (await db.execute(select(func.count(Server.id)))).scalar() or 0
    online = (await db.execute(
        select(func.count(Server.id)).where(Server.status == "online", Server.enabled == True)
    )).scalar() or 0
    offline = (await db.execute(
        select(func.count(Server.id)).where(Server.status != "online", Server.enabled == True)
    )).scalar() or 0
    vms = (await db.execute(select(func.count(VirtualMachine.id)))).scalar() or 0
    storages = (await db.execute(select(func.count(StorageDevice.id)))).scalar() or 0
    open_alerts = (await db.execute(
        select(func.count(Alert.id)).where(Alert.status == "open")
    )).scalar() or 0
    return {
        "servers": {"total": total, "online": online, "offline": offline},
        "vms": vms, "storages": storages, "open_alerts": open_alerts,
    }


async def dispatch_tool(name: str, args: dict, db: AsyncSession) -> dict:
    """执行单个工具, 返回可 JSON 序列化的结果字典。"""
    args = args or {}
    if name == "list_servers":
        return await _tool_list_servers(db, args.get("status"), args.get("enabled"))
    if name == "list_network_devices":
        return await _tool_list_network(db, args.get("status"))
    if name == "list_storage_devices":
        return await _tool_list_storage(db, args.get("status"))
    if name == "list_vmware_hosts":
        return await _tool_list_vmware(db, args.get("status"))
    if name == "list_env_sensors":
        return await _tool_list_env(db)
    if name == "list_alerts":
        return await _tool_list_alerts(db, args.get("status", "open"),
                                        args.get("level"), args.get("limit", 10))
    if name == "get_server_detail":
        return await _tool_server_detail(db, args.get("server_id"))
    if name == "get_server_metrics":
        return await _tool_server_metrics(db, args.get("server_id"))
    if name == "diagnose_server":
        return await _tool_diagnose(db, args.get("server_id"))
    if name == "list_processes":
        return await _tool_processes(db, args.get("server_id"), args.get("top", 15))
    if name == "list_vms":
        return await _tool_list_vms(db, args.get("host_id"), args.get("power_state"), args.get("limit", 50))
    if name == "get_storage_details":
        return await _tool_storage_details(db, args.get("storage_id"))
    if name == "get_summary":
        return await _tool_summary(db)
    return {"error": f"未知工具: {name}"}


# ---------------------------------------------------------------------------
# 工具调用循环 (Agent 主流程)
# ---------------------------------------------------------------------------
async def run_agent(
    message: str,
    history: list = None,
    db: AsyncSession = None,
    max_iter: int = 6,
) -> tuple:
    """运行 Agent, 返回 (最终回答文本, 工具调用轨迹列表)。

    - history: [{role, content}, ...] 历史对话(用于多轮连贯)。
    - 工具轨迹 tool_trace: 每条 {name, arguments, ok, result, error}, 供前端展示。
    """
    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-8:])
    messages.append({"role": "user", "content": message})

    tool_trace = []
    final = ""

    for _ in range(max_iter):
        resp = await llm_service.chat_with_tools(messages, TOOLS)
        msg = resp.choices[0].message

        # 没有工具调用 → 这就是最终回答
        if not getattr(msg, "tool_calls", None):
            final = msg.content or ""
            break

        # 把 assistant(带 tool_calls) 原样加入对话, 让模型下一轮看到自己的决策
        messages.append(msg)

        for tc in msg.tool_calls:
            try:
                fn_args = json.loads(tc.function.arguments or "{}")
            except Exception:
                fn_args = {}
            entry = {"name": tc.function.name, "arguments": fn_args,
                     "ok": True, "result": None, "error": None}
            try:
                result = await dispatch_tool(tc.function.name, fn_args, db)
                entry["result"] = result
            except Exception as e:  # 工具内部异常不应中断整个 Agent
                logger.warning("[agent] 工具 %s 执行异常: %s", tc.function.name, e)
                entry["ok"] = False
                entry["error"] = str(e)
                result = {"error": str(e)}
            tool_trace.append(entry)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    if not final:
        final = "（Agent 已达到最大工具调用轮次仍未给出结论，请简化问题或稍后重试。）"
    return final, tool_trace
