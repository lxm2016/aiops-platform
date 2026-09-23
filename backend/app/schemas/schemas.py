"""Pydantic schemas for API request/response."""
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel


# ---------- Auth / Settings ----------
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class LlmConfigIn(BaseModel):
    base_url: str
    model: str
    api_key: str = "EMPTY"


class PortRemarkIn(BaseModel):
    remark: str = ""


# ---------- Server ----------
class ServerCreate(BaseModel):
    name: str
    ip: str
    os_type: str = "linux"
    os_distro: str = ""
    os_version: str = ""
    tags: str = ""


class ServerOut(BaseModel):
    id: int
    name: str
    ip: str
    os_type: str
    os_distro: str
    os_version: str
    cpu_cores: int
    cpu_model: str
    mem_total_gb: float
    status: str
    agent_installed: bool
    last_seen: Optional[datetime]
    tags: str
    created_at: datetime

    class Config:
        from_attributes = True


class MetricReport(BaseModel):
    """Payload the agent POSTs to the platform."""
    ip: str
    hostname: str = ""
    os_type: str = "linux"
    os_distro: str = ""
    os_version: str = ""
    cpu_model: str = ""
    cpu_cores: int = 0
    mem_total_gb: float = 0.0
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    mem_used_gb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    net_rx_mbps: float = 0.0
    net_tx_mbps: float = 0.0
    load_avg: float = 0.0
    process_count: int = 0
    disks: List[Any] = []
    ports: List[Any] = []
    services: List[Any] = []


class ServerMetricOut(BaseModel):
    cpu_percent: float
    mem_percent: float
    mem_used_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    net_rx_mbps: float
    net_tx_mbps: float
    load_avg: float
    process_count: int
    collected_at: datetime
    disks: Optional[List[Any]] = None  # 各分区使用率历史 (从raw提取)

    class Config:
        from_attributes = True


# ---------- VMware ----------
class VmwareHostCreate(BaseModel):
    name: str
    host: str
    username: str = ""
    password: str = ""
    port: int = 443


class VmwareHostOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    status: str
    last_sync: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class VirtualMachineOut(BaseModel):
    id: int
    host_id: int
    name: str
    power_state: str
    guest_os: str
    ip: str
    cpu_cores: int
    mem_mb: int
    cpu_percent: float
    mem_percent: float
    uptime: str
    last_seen: Optional[datetime]

    class Config:
        from_attributes = True


# ---------- Network ----------
class NetworkDeviceCreate(BaseModel):
    name: str
    ip: str
    vendor: str = ""
    device_type: str = "switch"
    model: str = ""
    snmp_community: str = "public"
    snmp_version: str = "2c"


class NetworkDeviceOut(BaseModel):
    id: int
    name: str
    ip: str
    vendor: str
    device_type: str
    model: str
    snmp_community: str
    snmp_version: str
    status: str
    cpu_percent: float
    mem_percent: float
    port_total: int
    port_up: int
    last_seen: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Storage ----------
class StorageDeviceCreate(BaseModel):
    name: str
    ip: str
    vendor: str = ""
    model: str = ""
    protocol: str = "none"  # none/snmp/smi-s
    snmp_community: str = "public"
    snmp_version: str = "2c"
    # --- SNMPv3 (USM) ---
    # 华为 OceanStor 的「SNMPv1&SNMPv2c协议开关」默认关闭, 只留 USM 用户;
    # 这类设备必须用 v3, 上下文名称填 DeviceManager 上「上下文名称」那一栏的值。
    snmp_v3_user: str = ""
    snmp_v3_auth_proto: str = "sha"
    snmp_v3_auth_pass: str = ""
    snmp_v3_priv_proto: str = "aes"
    snmp_v3_priv_pass: str = ""
    snmp_context: str = ""
    username: str = ""
    password: str = ""
    capacity_tb: float = 0.0


class StorageDeviceOut(BaseModel):
    id: int
    name: str
    ip: str
    vendor: str
    model: str
    protocol: str
    snmp_community: str
    snmp_version: str
    snmp_v3_user: str = ""
    snmp_v3_auth_proto: str = "sha"
    snmp_v3_auth_pass: str = ""
    snmp_v3_priv_proto: str = "aes"
    snmp_v3_priv_pass: str = ""
    snmp_context: str = ""
    username: str
    password: str
    capacity_tb: float
    used_tb: float
    used_percent: float
    details: Optional[Any] = None
    status: str
    last_seen: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Env sensor ----------
class EnvSensorCreate(BaseModel):
    name: str
    location: str = ""
    source_url: str = ""


class EnvSensorOut(BaseModel):
    id: int
    name: str
    location: str
    source_url: str
    temperature: Optional[float]
    humidity: Optional[float]
    status: str
    last_seen: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class EnvReadingOut(BaseModel):
    temperature: Optional[float]
    humidity: Optional[float]
    collected_at: datetime

    class Config:
        from_attributes = True


# ---------- Alert ----------
class AlertOut(BaseModel):
    id: int
    level: str
    category: str
    source: str
    title: str
    detail: str
    status: str
    created_at: datetime
    resolved_at: Optional[datetime]
    # 可配置告警规则改造后新增: 便于界面直接展示"当前值/命中次数/命中规则"
    rule_id: Optional[int] = None
    metric: Optional[str] = None
    value: Optional[float] = None
    hit_count: Optional[int] = None

    class Config:
        from_attributes = True


# ---------- Alert rules (可配置告警规则) ----------
class AlertRuleIn(BaseModel):
    name: str
    category: str = "server"          # server/network/storage/env/vmware
    metric: str = "cpu_percent"
    operator: str = "gte"             # gte(>=) / lte(<=)
    warning_threshold: float = 80.0   # 提示阈值, 如 80
    critical_threshold: float = 90.0  # 告警阈值, 如 90
    duration_times: int = 1           # 连续命中N次才告警
    silence_minutes: int = 30         # 静默期(分钟)
    notify_levels: List[str] = ["warning", "critical"]
    channel_ids: List[int] = []
    enabled: bool = True
    notify_on_recovery: bool = True
    remark: str = ""


class AlertRuleOut(BaseModel):
    id: int
    name: str
    category: str
    metric: str
    operator: str
    warning_threshold: float
    critical_threshold: float
    duration_times: int
    silence_minutes: int
    notify_levels: str
    channel_ids: str
    enabled: bool
    notify_on_recovery: bool
    remark: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    # 派生字段(方便前端直接绑定多选框)
    channel_ids_list: List[int] = []
    notify_levels_list: List[str] = []

    class Config:
        from_attributes = True


# ---------- Notify channels (通知渠道) ----------
class NotifyChannelIn(BaseModel):
    name: str
    type: str = "dingtalk"            # dingtalk/wecom/sms/voice/webhook
    enabled: bool = True
    webhook_url: str = ""
    secret: str = ""                  # 钉钉加签密钥
    at_mobiles: str = ""
    at_all: bool = False
    targets: str = ""                 # 被叫号码(电话盒子/短信平台), 多个逗号分隔
    http_method: str = "POST"
    http_url: str = ""
    http_headers: str = ""
    http_body: str = ""
    success_keyword: str = ""
    timeout_seconds: int = 10
    remark: str = ""


class NotifyChannelOut(BaseModel):
    id: int
    name: str
    type: str
    enabled: bool
    webhook_url: str
    secret: str
    at_mobiles: str
    at_all: bool
    targets: str
    http_method: str
    http_url: str
    http_headers: str
    http_body: str
    success_keyword: str
    timeout_seconds: int
    remark: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NotifyLogOut(BaseModel):
    id: int
    alert_id: Optional[int] = None
    channel_id: Optional[int] = None
    channel_name: str
    channel_type: str
    level: str
    target: str
    success: bool
    response: str
    error: str
    sent_at: datetime

    class Config:
        from_attributes = True


class NotifyTestIn(BaseModel):
    title: str = "AIOps 平台测试告警"
    content: str = "这是一条测试消息, 用于验证通知渠道是否配置正确。"


# ---------- Chat ----------
class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    session_id: str


# ---------- Rack ----------
class RackCreate(BaseModel):
    name: str
    row_name: str = "A"
    u_height: int = 42
    remark: str = ""


class RackOut(BaseModel):
    id: int
    name: str
    row_name: str
    u_height: int
    remark: str
    created_at: datetime

    class Config:
        from_attributes = True


class RackDeviceCreate(BaseModel):
    name: str
    device_type: str = "server"  # server/switch/storage/security/other
    u_start: int
    u_size: int = 1
    side: str = "front"  # front/back
    remark: str = ""


class RackDeviceOut(BaseModel):
    id: int
    rack_id: int
    name: str
    device_type: str
    u_start: int
    u_size: int
    side: str
    remark: str
    created_at: datetime
    # 以下为与监控数据联动的派生字段(非数据库列), 由接口实时计算填充:
    #   online / offline / warning / critical / unknown
    status: Optional[str] = None
    ip: Optional[str] = None            # 关联到的服务器IP
    alert_level: Optional[str] = None   # 关联到的未恢复告警等级
    alert_count: Optional[int] = 0      # 未恢复告警条数

    class Config:
        from_attributes = True


class RackDeviceImportItem(BaseModel):
    rack_name: str
    name: str
    device_type: str = "server"
    u_start: int
    u_size: int = 1
    side: str = "front"
    remark: str = ""


class RackDeviceImportIn(BaseModel):
    items: List[RackDeviceImportItem]


# ---------- Auth ----------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str
