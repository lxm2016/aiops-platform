"""Alert engine (规则驱动版, 2026-09)

之前的版本把阈值写死在代码里 (CPU 80/95、内存 85/95 …), 改一个数字要改代码重启服务,
而且**只能入库、不能通知**。

现在:
  1. 阈值来自数据库 alert_rules 表, 界面可视化配置, 改完立即生效(30秒缓存);
  2. 命中后按规则绑定渠道推送: 钉钉 / 企业微信 / 院内短信平台 / 电话告警盒子;
  3. 支持两级阈值: 80% 为"提示"(warning)、90% 为"告警"(critical);
  4. 去抖: 连续命中 N 次才告警, 避免瞬时抖动刷屏;
  5. 静默: 同一告警在静默期内不重复发送;
  6. 恢复: 指标回落自动关闭告警, 可选推送恢复通知。
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, AlertRule
from app.services import notify_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 指标与类别的中文名
# ---------------------------------------------------------------------------
METRIC_LABELS = {
    "cpu_percent": "CPU使用率",
    "mem_percent": "内存使用率",
    "disk_percent": "磁盘使用率",
    "temperature": "温度",
    "humidity": "湿度",
    "used_percent": "容量使用率",
    "port_down": "DOWN端口数",
    "offline": "设备离线",
    # ---- 动环设备(Modbus)点位 ----
    "smoke": "烟雾报警",
    "water": "漏水报警",
    "door": "门禁状态",
    "ups_voltage": "UPS输入电压",
    "ups_battery": "UPS电池容量",
    "ups_load": "UPS负载率",
    "ups_temp": "UPS温度",
    "ups_runtime": "UPS剩余续航",
    "power": "市电状态",
}

# 指标归到哪个大类 —— 运维看告警第一眼想知道"是磁盘还是CPU出事了"
METRIC_GROUPS = {
    "cpu_percent": "CPU",
    "mem_percent": "内存",
    "disk_percent": "磁盘",
    "used_percent": "存储容量",
    "port_down": "网络端口",
    "temperature": "温度",
    "humidity": "湿度",
    "offline": "连通性",
    "smoke": "消防",
    "water": "漏水",
    "door": "门禁",
    "ups_voltage": "供电",
    "ups_battery": "供电",
    "ups_load": "供电",
    "ups_temp": "供电",
    "ups_runtime": "供电",
    "power": "供电",
}

CATEGORY_LABELS = {
    "server": "服务器",
    "network": "网络设备",
    "storage": "存储设备",
    "env": "温湿度",
    "vmware": "虚拟化",
}

# 数据库里没有对应规则时的内置默认阈值 (operator, warning, critical)
# 保证"开箱即用": 用户还没配置规则时, 80% 提示 / 90% 告警照样生效。
#
# 注意 operator 必须跟指标方向一致 —— 电池容量是"越低越糟", 用 gte 会把
# 电量充足(75%) 当成严重告警, 这类反向指标一律用 lte。
DEFAULT_THRESHOLDS = {
    ("server", "cpu_percent"): ("gte", 80.0, 90.0),
    ("server", "mem_percent"): ("gte", 80.0, 90.0),
    ("server", "disk_percent"): ("gte", 85.0, 95.0),
    ("network", "cpu_percent"): ("gte", 80.0, 90.0),
    ("network", "mem_percent"): ("gte", 85.0, 95.0),
    ("storage", "used_percent"): ("gte", 80.0, 90.0),
    ("vmware", "cpu_percent"): ("gte", 80.0, 90.0),
    ("vmware", "mem_percent"): ("gte", 80.0, 90.0),
    ("env", "temperature"): ("gte", 30.0, 35.0),
    ("env", "humidity"): ("gte", 75.0, 85.0),
    # 动环开关量: 引擎里统一按 0=正常 / 1=异常 传值, 所以阈值就是 1
    ("env", "smoke"): ("gte", 1.0, 1.0),
    ("env", "water"): ("gte", 1.0, 1.0),
    ("env", "door"): ("gte", 1.0, 1.0),
    ("env", "power"): ("gte", 1.0, 1.0),
    # 反向指标: 越低越糟
    ("env", "ups_battery"): ("lte", 40.0, 20.0),
    ("env", "ups_runtime"): ("lte", 15.0, 5.0),
    ("env", "ups_load"): ("gte", 80.0, 90.0),
    ("env", "ups_temp"): ("gte", 40.0, 50.0),
}

# 反向指标(值越小越危险) —— 通知文案要说"低于", 不能说"达到"。
# 用户自定义规则时如果 operator 填错, 这里也能兜住文案。
LOWER_IS_WORSE = {"ups_battery", "ups_runtime", "disk_free", "mem_available"}


# ---------------------------------------------------------------------------
# 规则缓存: agent 每分钟上报, 不能每次都查库
# ---------------------------------------------------------------------------
_RULES_CACHE: dict = {"ts": 0.0, "rules": []}
CACHE_TTL_SECONDS = 30


# 级别严重程度排序 (用于判定"升级")
_LEVEL_ORDER = {"info": 0, "warning": 1, "critical": 2}


def unit_of(metric: str) -> str:
    """指标单位: 百分比 / 温度 / 湿度 / 无。"""
    if metric.endswith("percent"):
        return "%"
    if metric == "temperature":
        return "°C"
    if metric == "humidity":
        return "%"
    return ""


def fmt_value(metric: str, value) -> str:
    """按指标格式化数值: 温度 32.5°C、CPU 92.5%、离线 离线。"""
    if value is None:
        return "-"
    if metric == "offline":
        return "离线"
    if metric == "port_down":      # 端口数是计数, 不该显示成 3.0
        return f"{int(value)} 个"
    unit = unit_of(metric)
    return f"{float(value):.1f}{unit}"


def group_of(metric: str) -> str:
    """指标大类: CPU / 内存 / 磁盘 / 连通性 ……"""
    return METRIC_GROUPS.get(metric, METRIC_LABELS.get(metric, metric))


# 动环设备在告警引擎里统一按 category="env" 上报(规则/阈值都挂在 env 上),
# 但通知里显然不该把烟感写成"温湿度"。所以按设备类型再细分一层显示名,
# 由采集侧通过 extra["cat_label"] 传进来。
ENV_CATEGORY_LABELS = {
    "temp_humidity": "温湿度",
    "smoke": "消防",
    "water": "漏水",
    "ups": "UPS供电",
    "power": "配电",
    "door": "门禁",
    "other": "动环",
}


def category_label(category: str, override: str = "") -> str:
    """告警类别显示名。override 优先(动环设备用它区分消防/漏水/供电)。"""
    return override or CATEGORY_LABELS.get(category, "") or ""


def is_lower_worse(metric: str, operator: str = "") -> bool:
    """这个指标是不是"越低越危险"(电池容量、剩余续航这类反向指标)。"""
    return operator == "lte" or metric in LOWER_IS_WORSE


def threshold_verb(metric: str, level: str, operator: str = "gte") -> str:
    """阈值方向文案 —— 电池容量 75% 不该说成"达到告警阈值"。"""
    if is_lower_worse(metric, operator):
        return "低于告警" if level == "critical" else "低于提示"
    return "达到告警" if level == "critical" else "超过提示"


_IP_CACHE: dict = {}          # {(category, source): (ts, ip)}  避免每条上报都查库
_IP_CACHE_TTL = 300.0


async def lookup_ip(db, category: str, source: str) -> str:
    """按设备名反查管理 IP。查不到返回空串(不影响告警本身)。"""
    key = (category, source)
    hit = _IP_CACHE.get(key)
    if hit and time.time() - hit[0] < _IP_CACHE_TTL:
        return hit[1]

    ip = ""
    try:
        if category == "server":
            from app.models import Server
            row = (await db.execute(
                select(Server.ip).where(Server.name == source))).scalar()
            ip = row or ""
            if not ip:      # 名字对不上时再按 IP 本身匹配(有些 agent 上报的就是 IP)
                row2 = (await db.execute(
                    select(Server.ip).where(Server.ip == source))).scalar()
                ip = row2 or ""
        elif category == "network":
            from app.models import NetworkDevice
            row = (await db.execute(
                select(NetworkDevice.ip).where(NetworkDevice.name == source))).scalar()
            ip = row or ""
        elif category == "storage":
            from app.models import StorageDevice
            row = (await db.execute(
                select(StorageDevice.ip).where(StorageDevice.name == source))).scalar()
            ip = row or ""
        elif category == "vmware":
            from app.models import VirtualMachine
            row = (await db.execute(
                select(VirtualMachine.ip).where(VirtualMachine.name == source))).scalar()
            ip = row or ""
        elif category == "env":
            from app.models import EnvSensor
            row = (await db.execute(
                select(EnvSensor.location).where(EnvSensor.name == source))).scalar()
            ip = row or ""      # 温湿度探头没有 IP, 用安装位置代替
    except Exception as e:
        logger.debug(f"[告警] 反查 {category}/{source} 地址失败: {e}")

    _IP_CACHE[key] = (time.time(), ip)
    return ip


def invalidate_ip_cache():
    """设备增删改后调用, 让地址缓存失效。"""
    _IP_CACHE.clear()


def _build_title(source: str, metric_label: str, metric: str, value, level: str,
                 ip: str = "", category: str = "", cat_label: str = "",
                 operator: str = "gte") -> str:
    """告警标题。例: 【严重告警】服务器 web01(172.16.0.10) CPU 达到告警阈值 95.0%"""
    cat = category_label(category, cat_label)
    obj = f"{source}({ip})" if ip else source
    tag = LEVEL_TAGS.get(level, "告警")
    if metric == "offline":
        return f"【{tag}】{cat} {obj} 设备离线失联"
    verb = threshold_verb(metric, level, operator)
    return f"【{tag}】{cat} {obj} {metric_label}{verb}阈值 {fmt_value(metric, value)}"


LEVEL_TAGS = {"critical": "严重告警", "warning": "告警提示", "info": "已恢复"}


def invalidate_rules_cache():
    """规则/渠道有变更时调用, 让缓存立即失效。"""
    _RULES_CACHE["ts"] = 0.0


async def _all_rules(db: AsyncSession) -> list:
    now = time.time()
    if _RULES_CACHE["rules"] and now - _RULES_CACHE["ts"] < CACHE_TTL_SECONDS:
        return _RULES_CACHE["rules"]

    rows = (await db.execute(select(AlertRule))).scalars().all()
    rules = [{
        "id": r.id, "name": r.name, "category": r.category, "metric": r.metric,
        "operator": r.operator or "gte",
        "warning_threshold": r.warning_threshold, "critical_threshold": r.critical_threshold,
        "duration_times": r.duration_times or 1, "silence_minutes": r.silence_minutes or 0,
        "notify_levels": (r.notify_levels or "warning,critical").split(","),
        "channel_ids": [int(x) for x in (r.channel_ids or "").split(",") if x.strip().isdigit()],
        "enabled": bool(r.enabled), "notify_on_recovery": bool(r.notify_on_recovery),
    } for r in rows]

    _RULES_CACHE["rules"] = rules
    _RULES_CACHE["ts"] = now
    return rules


async def _applicable_rules(db: AsyncSession, category: str, metric: str) -> list:
    """取适用于该 (类别, 指标) 的已启用规则。

    - 数据库里该组合一条规则都没有 -> 用内置默认阈值(保证开箱即用);
    - 有规则但全部禁用 -> 返回空(用户明确关掉了, 就不告警)。
    """
    rules = await _all_rules(db)
    matched = [r for r in rules if r["category"] == category and r["metric"] == metric]
    if not matched:
        defaults = DEFAULT_THRESHOLDS.get((category, metric))
        # 设备离线是最该第一时间知道的故障: 没配规则也要按"严重告警"处理
        if metric == "offline":
            defaults = ("gte", 1.0, 1.0)
        if defaults:
            op, warn, crit = defaults
            return [{
                "id": None, "name": f"默认规则 {METRIC_LABELS.get(metric, metric)}",
                "category": category, "metric": metric, "operator": op,
                "warning_threshold": warn, "critical_threshold": crit,
                "duration_times": 1, "silence_minutes": 30,
                "notify_levels": ["warning", "critical"], "channel_ids": [],
                "enabled": True, "notify_on_recovery": True,
            }]
        return []
    return [r for r in matched if r["enabled"]]


def _judge(rule: dict, value: float) -> Optional[str]:
    """判定等级: 返回 critical / warning / None(未触发)。"""
    op = rule.get("operator", "gte")
    warn = rule.get("warning_threshold")
    crit = rule.get("critical_threshold")
    try:
        if op == "gte":
            if crit is not None and value >= crit:
                return "critical"
            if warn is not None and value >= warn:
                return "warning"
        elif op == "lte":
            if crit is not None and value <= crit:
                return "critical"
            if warn is not None and value <= warn:
                return "warning"
        elif op == "eq":
            if crit is not None and abs(value - crit) < 1e-6:
                return "critical"
            if warn is not None and abs(value - warn) < 1e-6:
                return "warning"
    except (TypeError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------
# 核心: 单个指标评估
# ---------------------------------------------------------------------------
async def evaluate_metric(
    db: AsyncSession,
    category: str,
    source: str,
    metric: str,
    value: float,
    extra: Optional[dict] = None,
) -> Optional[Alert]:
    """按规则评估一个指标: 命中则创建/更新告警并通知, 回落则自动恢复。

    extra 可带 {"ip": "x.x.x.x"} 覆盖自动反查到的地址。
    """
    rules = await _applicable_rules(db, category, metric)
    if not rules:
        return None

    extra = dict(extra or {})
    if "ip" not in extra:
        extra["ip"] = await lookup_ip(db, category, source)

    result = None
    for rule in rules:
        level = _judge(rule, value)
        rule_key = f"{rule['id'] or 'default'}:{category}:{metric}"

        if level is None:
            await _try_recover(db, rule, category, source, metric, extra)
            continue

        alert = await _upsert_alert(db, rule, category, source, metric, value,
                                    level, rule_key, extra)
        result = alert or result
    return result


def _like_escape(s: str) -> str:
    """rule_key 里有下划线(ups_battery), 在 LIKE 里是单字符通配符, 必须转义。
    否则 default:env:ups_battery 会误匹配 default:env:upsXbattery。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _find_open_alert(db: AsyncSession, rule_key: str, source: str) -> Optional[Alert]:
    rows = (await db.execute(
        select(Alert).where(
            Alert.source == source,
            Alert.status != "resolved",
            Alert.detail.like(f"%{_like_escape(rule_key)}%", escape="\\"),
        ).order_by(Alert.id.desc()).limit(1)
    )).scalars().all()
    return rows[0] if rows else None


async def _upsert_alert(db, rule, category, source, metric, value, level,
                        rule_key, extra=None) -> Alert:
    """命中阈值: 已存在未恢复告警则累加命中次数, 否则新建。"""
    alert = await _find_open_alert(db, rule_key, source)
    metric_label = METRIC_LABELS.get(metric, metric)
    extra = extra or {}
    cat_label = extra.get("cat_label", "")
    op = rule.get("operator", "gte")
    now = datetime.utcnow()

    if alert:
        alert.level = level
        alert.value = value
        alert.hit_count = (alert.hit_count or 0) + 1
        alert.updated_at = now
        alert.title = _build_title(source, metric_label, metric, value, level,
                                   extra.get("ip", ""), category, cat_label, op)
        alert.detail = _build_detail(rule, category, source, metric, value, level,
                                     rule_key, extra)
        await db.flush()
    else:
        alert = Alert(
            level=level, category=category, source=source,
            title=_build_title(source, metric_label, metric, value, level,
                               extra.get("ip", ""), category, cat_label, op),
            detail=_build_detail(rule, category, source, metric, value, level,
                                 rule_key, extra),
            status="open", created_at=now, updated_at=now,
            rule_id=rule["id"], metric=metric, value=value, hit_count=1,
        )
        db.add(alert)
        await db.flush()

    # ---- 通知判定 ----
    need = (
        rule["channel_ids"]
        and level in rule["notify_levels"]
        and (alert.hit_count or 1) >= (rule["duration_times"] or 1)
    )
    if need:
        silence = rule["silence_minutes"] or 0
        # 级别升级(warning -> critical)必须立即通知, 不能被静默期吞掉:
        # 否则 80% 提示刚发过, 一路涨到 95% 反而不通知, 是最容易出事故的情况。
        escalated = _LEVEL_ORDER.get(level, 0) > _LEVEL_ORDER.get(alert.last_notify_level or "", 0)
        if alert.last_notify_at and silence > 0 and not escalated:
            if now - alert.last_notify_at < timedelta(minutes=silence):
                return alert
        threshold = rule["critical_threshold"] if level == "critical" else rule["warning_threshold"]
        _enqueue(db, {
            "alert_id": alert.id, "channel_ids": rule["channel_ids"], "level": level,
            "metric": metric, "metric_label": metric_label, "threshold": threshold,
            "rule_name": rule["name"], "category": category, "cat_label": cat_label,
            "operator": op,
            "source": alert.source, "value": alert.value, "recovered": False,
            "ip": extra.get("ip", ""), "hit_count": alert.hit_count or 1,
        })
        alert.last_notify_at = datetime.utcnow()
        alert.last_notify_level = level
        await db.flush()
    return alert


def _enqueue(db: AsyncSession, item: dict):
    """把待发送通知放进会话的待办队列, 等事务提交后再真正发送。

    ==== 为什么不在评估时直接发 ====
    发送通知需要读取通知渠道配置。如果在本会话的事务尚未提交时就另开一个
    连接去读, SQLite 的写锁会把这次读取卡住整整 busy_timeout(30秒)。
    实测表现: 告警入库正常, 但通知"整体超时10秒"被跳过, 上报接口变慢 32 秒。
    因此这里只入队, 由调用方在 commit 之后调用 flush_notifications() 发送。
    """
    if not isinstance(getattr(db, "info", None), dict):
        return
    queue = db.info.setdefault("pending_notify", [])
    if len(queue) >= 200:            # 防止极端情况下队列无限增长
        logger.warning("[告警] 待发送通知队列已满(200), 丢弃最早的一条")
        queue.pop(0)
    queue.append(item)


async def _try_recover(db, rule, category, source, metric, extra=None):
    """指标回落到阈值以内: 自动关闭已有告警, 可选推送恢复通知。"""
    rule_key = f"{rule['id'] or 'default'}:{category}:{metric}"
    alert = await _find_open_alert(db, rule_key, source)
    if not alert:
        return
    extra = extra or {}
    if "ip" not in extra:
        extra["ip"] = await lookup_ip(db, category, source)
    alert.status = "resolved"
    alert.resolved_at = datetime.utcnow()
    alert.updated_at = alert.resolved_at
    await db.flush()

    if rule.get("notify_on_recovery") and rule.get("channel_ids"):
        _enqueue(db, {
            "alert_id": alert.id, "channel_ids": rule["channel_ids"], "level": "info",
            "metric": metric, "metric_label": METRIC_LABELS.get(metric, metric),
            "threshold": rule["warning_threshold"], "rule_name": rule["name"],
            "source": alert.source, "value": alert.value, "recovered": True,
            "category": category, "cat_label": extra.get("cat_label", ""),
            "operator": rule.get("operator", "gte"),
            "ip": extra.get("ip", ""),
            "hit_count": alert.hit_count or 1,
        })
        logger.info(f"[告警] {source} {METRIC_LABELS.get(metric, metric)} 已恢复正常, 恢复通知已入队")
    else:
        logger.info(f"[告警] {source} {METRIC_LABELS.get(metric, metric)} 已恢复正常, 告警自动关闭")


def _build_detail(rule, category, source, metric, value, level, rule_key,
                  extra=None) -> str:
    """detail 里带 rule_key, 用于同一规则去重 (避免被用户看到的文案变化影响)。"""
    metric_label = METRIC_LABELS.get(metric, metric)
    extra = extra or {}
    ip = extra.get("ip", "")
    addr = f"(IP: {ip})" if ip else ""
    head = f"{category_label(category, extra.get('cat_label', ''))} {source}{addr}"
    if metric == "offline":
        return f"{head} 设备离线失联，已无法响应。[规则:{rule['name']}][key:{rule_key}]"
    threshold = rule["critical_threshold"] if level == "critical" else rule["warning_threshold"]
    verb = threshold_verb(metric, level, rule.get("operator", "gte"))
    return (
        f"{head} 的{metric_label}为 "
        f"{fmt_value(metric, value)}, 已{verb}阈值 {fmt_value(metric, threshold)}。"
        f"[规则:{rule['name']}][key:{rule_key}]"
    )


def build_message(item: dict) -> tuple:
    """根据队列项构造 (标题, 正文, 变量上下文)。

    运维接到告警最想知道三件事: **哪台机器(IP)**、**哪一类资源(CPU/磁盘/连通性)**、
    **现在多少、阈值多少**。所以正文按这个顺序组织, 一眼能读完。
    """
    recovered = item.get("recovered", False)
    level = item.get("level", "warning")
    metric = item.get("metric", "")
    metric_label = item.get("metric_label", "")
    source = item.get("source", "")
    value = item.get("value")
    value_text = fmt_value(metric, value)
    ip = item.get("ip", "")
    category = item.get("category", "")
    cat_label = item.get("cat_label") or CATEGORY_LABELS.get(category, category or "设备")
    op = item.get("operator", "gte")
    lower_worse = is_lower_worse(metric, op)
    group = group_of(metric)
    now = notify_service.now_cn()

    prefix = "已恢复" if recovered else LEVEL_TAGS.get(level, "告警")
    obj = f"{source}({ip})" if ip else source

    # 离线类的 title 单独写, "设备离线失联"比"设备离线 达到阈值"更像人话
    if metric == "offline":
        tail = "已恢复在线" if recovered else "设备离线失联"
        title = f"【{prefix}】{cat_label} {obj} {tail}"
    elif recovered:
        title = f"【{prefix}】{cat_label} {obj} {metric_label}已恢复正常"
    else:
        verb = threshold_verb(metric, level, op)
        title = f"【{prefix}】{cat_label} {obj} {metric_label}{verb}阈值 {fmt_value(metric, value)}"
    state = "已恢复正常" if recovered else ("严重告警" if level == "critical" else "告警提示")

    hits = item.get("hit_count") or 1
    threshold = item.get("threshold")
    thr_text = fmt_value(metric, threshold) if threshold is not None else "-"

    lines = [
        f"### {title}",
        "",
        f"> **告警类别**: {cat_label} / {group}",
        f"> **对象**: {source}",
    ]
    if ip:
        # 温湿度探头没有 IP, lookup_ip 返回的是安装位置, 标题就不能写"IP地址"
        addr_label = "安装位置" if category == "env" else "IP地址"
        lines.append(f"> **{addr_label}**: {ip}")
    lines += [
        f"> **指标**: {metric_label}",
    ]
    # 离线没有"阈值"和"数值"的概念, 单列一行状态即可, 别显示"离线/离线"
    if metric == "offline":
        lines.append(f"> **当前状态**: {'已恢复在线' if recovered else '设备失联，无响应'}")
    else:
        lines.append(f"> **当前值**: {value_text}　**阈值**: {thr_text}")
    lines.append(f"> **告警级别**: {state}")
    if not recovered and hits > 1:
        lines.append(f"> **连续命中**: {hits} 次")
    lines += [
        f"> **规则**: {item.get('rule_name', '')}",
        f"> **时间**: {now}",
    ]
    content = "\n".join(lines)

    # 电话/短信要短: 类别 + 名字 + IP + 指标 + 值 + 状态, 控制在 100 字内
    if metric == "offline":
        tail = "已恢复在线" if recovered else "设备离线失联"
        short = f"{cat_label}{source}" + (f" {ip}" if ip else "") + f" {tail}"
    else:
        # 电话/短信里"超限"会把电池容量 18% 说反, 反向指标统一说成"偏低"
        tail = "已恢复" if recovered else ("偏低" if lower_worse else "超限")
        short = (f"{cat_label}{source}" + (f" {ip}" if ip else "")
                 + f" {metric_label} {value_text} " + tail)

    ctx = {
        "title": title, "content": content, "level": level,
        "source": source, "ip": ip, "category": cat_label,
        "metric": metric_label, "group": group, "value": value_text,
        "threshold": thr_text, "state": state,
        "time": now, "short": short,
    }
    return title, content, ctx


async def _send_one(item: dict) -> dict:
    """发送单条通知。"""
    title, content, ctx = build_message(item)
    return await notify_service.dispatch(
        item.get("alert_id"), item.get("channel_ids", []),
        item.get("level", "warning"), title, content, ctx,
    )


async def flush_notifications(db: AsyncSession) -> int:
    """事务提交之后调用, 发送本会话累积的告警通知。

    ==== 调用时机很重要 ====
    必须在 commit 之后调用。若在事务内调用, 发送时另开连接读渠道配置会撞上
    SQLite 写锁, 白等 busy_timeout(30秒), 表现为"通知超时、接口变慢"。
    """
    info = getattr(db, "info", None)
    if not isinstance(info, dict):
        return 0
    items = info.pop("pending_notify", None)
    if not items:
        return 0

    sent = 0
    for item in items:
        try:
            res = await asyncio.wait_for(_send_one(item), timeout=12)
            sent += 1
            logger.info(f"[告警] 通知完成 {res.get('success')}/{res.get('total')} -> "
                        f"{item.get('source')}")
        except asyncio.TimeoutError:
            logger.warning("[告警] 单条通知发送超时(>12s), 已跳过")
        except Exception as e:
            logger.error(f"[告警] 通知发送异常: {e}", exc_info=True)
    return sent


# ---------------------------------------------------------------------------
# 兼容旧调用 (servers.py / devices.py 已在使用)
# ---------------------------------------------------------------------------
async def evaluate_server_metrics(db: AsyncSession, server_name: str, metrics: dict) -> list:
    """Agent 上报后评估 CPU/内存/磁盘。"""
    created = []
    for metric in ("cpu_percent", "mem_percent", "disk_percent"):
        value = metrics.get(metric)
        if value is None:
            continue
        alert = await evaluate_metric(db, "server", server_name, metric, float(value))
        if alert:
            created.append(alert)
    return created


async def evaluate_env_reading(
    db: AsyncSession, sensor_name: str, temperature: float, humidity: float
) -> list:
    """温湿度读数评估 (温度高、湿度过高/过低)。"""
    created = []
    if temperature is not None:
        a = await evaluate_metric(db, "env", sensor_name, "temperature", float(temperature))
        if a:
            created.append(a)
    if humidity is not None:
        a = await evaluate_metric(db, "env", sensor_name, "humidity", float(humidity))
        if a:
            created.append(a)
    return created


async def evaluate_device_offline(db: AsyncSession, category: str, source: str, offline: bool):
    """设备离线/上线告警。offline=True 表示离线。"""
    if offline:
        return await evaluate_metric(db, category, source, "offline", 1.0)
    await _mark_online_recovered(db, category, source)


async def _mark_online_recovered(db, category: str, source: str):
    """设备恢复在线: 关闭该对象所有 offline 类型的未恢复告警。"""
    rows = (await db.execute(
        select(Alert).where(
            Alert.category == category, Alert.source == source,
            Alert.metric == "offline", Alert.status != "resolved",
        )
    )).scalars().all()
    for a in rows:
        a.status = "resolved"
        a.resolved_at = datetime.utcnow()
    if rows:
        await db.flush()


# ---------------------------------------------------------------------------
# 默认规则初始化 (界面上一键生成)
# ---------------------------------------------------------------------------
DEFAULT_RULE_TEMPLATE = [
    # (名称, 类别, 指标, 比较符, 提示阈值, 告警阈值, 备注)
    ("CPU使用率告警", "server", "cpu_percent", "gte", 80.0, 90.0, "服务器CPU: 80%提示, 90%告警"),
    ("内存使用率告警", "server", "mem_percent", "gte", 80.0, 90.0, "服务器内存: 80%提示, 90%告警"),
    ("磁盘使用率告警", "server", "disk_percent", "gte", 85.0, 95.0, "磁盘: 85%提示, 95%告警"),
    ("交换机CPU告警", "network", "cpu_percent", "gte", 80.0, 90.0, "网络设备CPU: 80%提示, 90%告警"),
    ("交换机内存告警", "network", "mem_percent", "gte", 85.0, 95.0, "网络设备内存: 85%提示, 95%告警"),
    ("存储容量告警", "storage", "used_percent", "gte", 80.0, 90.0, "存储容量: 80%提示, 90%告警"),
    ("机房温度过高", "env", "temperature", "gte", 30.0, 35.0, "温度: 30°C提示, 35°C告警"),
    ("机房湿度过高", "env", "humidity", "gte", 70.0, 80.0, "湿度: 70%提示, 80%告警"),
    ("机房湿度过低", "env", "humidity", "lte", 30.0, 20.0, "湿度: 低于30%提示, 低于20%告警"),
    # 设备离线: 采不到数据就是故障, 直接按严重告警处理
    ("服务器离线告警", "server", "offline", "gte", 1.0, 1.0, "服务器失联: 立即严重告警"),
    ("网络设备离线告警", "network", "offline", "gte", 1.0, 1.0, "网络设备失联: 立即严重告警"),
    ("存储设备离线告警", "storage", "offline", "gte", 1.0, 1.0, "存储设备失联: 立即严重告警"),
]


async def init_default_rules(db: AsyncSession, channel_ids: Optional[list] = None) -> int:
    """生成一套默认规则 (已存在同类别+指标+比较符的则跳过)。"""
    existing = {(r.category, r.metric, r.operator)
                for r in (await db.execute(select(AlertRule))).scalars().all()}
    ch = ",".join(str(i) for i in (channel_ids or []))
    added = 0
    for name, category, metric, op, warn, crit, remark in DEFAULT_RULE_TEMPLATE:
        if (category, metric, op) in existing:
            continue
        db.add(AlertRule(
            name=name, category=category, metric=metric, operator=op,
            warning_threshold=float(warn), critical_threshold=float(crit),
            duration_times=1, silence_minutes=30,
            notify_levels="warning,critical", channel_ids=ch,
            enabled=True, notify_on_recovery=True, remark=remark,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        added += 1
    if added:
        await db.commit()
        invalidate_rules_cache()
        logger.info(f"[告警] 已生成 {added} 条默认告警规则")
    return added
