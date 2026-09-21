"""SQLAlchemy ORM models for the AIOps platform."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, JSON
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Server(Base):
    """Physical or virtual server (Linux / Windows)."""
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    ip = Column(String(64), unique=True, nullable=False, index=True)
    os_type = Column(String(32), default="linux")  # linux / windows
    os_distro = Column(String(64), default="")  # centos/ubuntu/openeuler/rocky/anolis/2019...
    os_version = Column(String(64), default="")
    cpu_cores = Column(Integer, default=0)
    cpu_model = Column(String(128), default="")
    mem_total_gb = Column(Float, default=0.0)
    status = Column(String(16), default="unknown")  # online / offline / unknown
    agent_installed = Column(Boolean, default=False)
    last_seen = Column(DateTime, nullable=True)
    tags = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    metrics = relationship("ServerMetric", back_populates="server", cascade="all, delete-orphan")


class ServerMetric(Base):
    """Time-series metrics reported by the agent."""
    __tablename__ = "server_metrics"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("servers.id"), index=True)
    cpu_percent = Column(Float, default=0.0)
    mem_percent = Column(Float, default=0.0)
    mem_used_gb = Column(Float, default=0.0)
    disk_percent = Column(Float, default=0.0)
    disk_used_gb = Column(Float, default=0.0)
    disk_total_gb = Column(Float, default=0.0)
    net_rx_mbps = Column(Float, default=0.0)
    net_tx_mbps = Column(Float, default=0.0)
    load_avg = Column(Float, default=0.0)
    process_count = Column(Integer, default=0)
    raw = Column(JSON, nullable=True)  # full payload incl. per-disk, ports, services
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)

    server = relationship("Server", back_populates="metrics")


class VmwareHost(Base):
    """VMware vCenter / ESXi connection endpoint."""
    __tablename__ = "vmware_hosts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    host = Column(String(128), nullable=False)
    username = Column(String(128), default="")
    password = Column(String(256), default="")
    port = Column(Integer, default=443)
    status = Column(String(16), default="unknown")
    last_sync = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    vms = relationship("VirtualMachine", back_populates="host", cascade="all, delete-orphan")


class VirtualMachine(Base):
    """A VM discovered from VMware."""
    __tablename__ = "virtual_machines"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("vmware_hosts.id"), index=True)
    name = Column(String(128), nullable=False)
    power_state = Column(String(32), default="unknown")  # poweredOn / poweredOff / suspended
    guest_os = Column(String(128), default="")
    ip = Column(String(64), default="")
    cpu_cores = Column(Integer, default=0)
    mem_mb = Column(Integer, default=0)
    cpu_percent = Column(Float, default=0.0)
    mem_percent = Column(Float, default=0.0)
    uptime = Column(String(64), default="")
    last_seen = Column(DateTime, nullable=True)

    host = relationship("VmwareHost", back_populates="vms")


class NetworkDevice(Base):
    """Switch / router / firewall (H3C, Huawei, Dell, ...)."""
    __tablename__ = "network_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    ip = Column(String(64), unique=True, nullable=False, index=True)
    vendor = Column(String(64), default="")  # h3c / huawei / dell / cisco
    device_type = Column(String(64), default="switch")
    model = Column(String(128), default="")
    snmp_community = Column(String(128), default="public")
    snmp_version = Column(String(8), default="2c")
    status = Column(String(16), default="unknown")
    cpu_percent = Column(Float, default=0.0)
    mem_percent = Column(Float, default=0.0)
    port_total = Column(Integer, default=0)
    port_up = Column(Integer, default=0)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Rack(Base):
    """机房机柜 (按列分组, U数可自定义)。"""
    __tablename__ = "racks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False)          # 机柜编号, 如 A01
    row_name = Column(String(64), default="A")         # 列名, 如 A列
    u_height = Column(Integer, default=42)             # 总U数
    remark = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class RackDevice(Base):
    """机柜内设备 (支持前后侧, 同U位前后侧可各放一台设备)。"""
    __tablename__ = "rack_devices"

    id = Column(Integer, primary_key=True, index=True)
    rack_id = Column(Integer, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    device_type = Column(String(32), default="server")  # server/switch/storage/security/other
    u_start = Column(Integer, nullable=False)           # 起始U位 (从1开始, 1=最底部)
    u_size = Column(Integer, default=1)                 # 占用U数
    side = Column(String(8), default="front")           # front/back
    remark = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SwitchPort(Base):
    """交换机端口明细 (每次轮询刷新状态, 备注由用户编辑并保留)。"""
    __tablename__ = "switch_ports"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, nullable=False, index=True)
    port_index = Column(Integer, nullable=False)
    name = Column(String(128), default="")
    status = Column(String(16), default="unknown")
    speed_mbps = Column(Integer, default=0)
    physical = Column(Integer, default=1)  # 1=物理口(计入UP/总), 0=VLAN/虚拟口
    alias = Column(String(256), default="")      # 交换机侧 ifAlias
    remark = Column(String(256), default="")     # 用户填写的端口用途备注
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StorageDevice(Base):
    """Storage array (Huawei / H3C / Sangfor / EMC / ...)."""
    __tablename__ = "storage_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    ip = Column(String(64), unique=True, nullable=False, index=True)
    vendor = Column(String(64), default="")
    model = Column(String(128), default="")
    protocol = Column(String(16), default="none")  # none/snmp/smi-s
    snmp_community = Column(String(64), default="public")
    snmp_version = Column(String(4), default="2c")
    username = Column(String(64), default="")      # SMI-S (WBEM) 账号
    password = Column(String(128), default="")
    capacity_tb = Column(Float, default=0.0)
    used_tb = Column(Float, default=0.0)
    used_percent = Column(Float, default=0.0)
    details = Column(JSON, default=dict)           # 控制器/磁盘/存储池/卷等明细
    status = Column(String(16), default="unknown")
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EnvSensor(Base):
    """Temperature / humidity sensor endpoint."""
    __tablename__ = "env_sensors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    location = Column(String(128), default="")
    source_url = Column(String(256), default="")  # HTTP endpoint or SNMP
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    status = Column(String(16), default="unknown")
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EnvReading(Base):
    """Time-series temperature/humidity readings."""
    __tablename__ = "env_readings"

    id = Column(Integer, primary_key=True, index=True)
    sensor_id = Column(Integer, ForeignKey("env_sensors.id"), index=True)
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# 动环设备 (Modbus TCP): 温湿度 / 烟感 / 水浸 / UPS 等
#
# 为什么单独建表而不是往 env_sensors 里塞:
#   原来的 env_sensors 只有 温度/湿度 两个固定字段, 且只能被动接收 HTTP 推送。
#   而实际机房里一个"设备"往往带**好几种**点位(一台 UPS 有电压/电流/容量/电池),
#   而且是平台**主动去轮询** Modbus, 不是等上报。
#   所以拆成 设备 -> 点位 -> 读数 三层, 点位类型可自由扩展。
# ---------------------------------------------------------------------------
class EnvDevice(Base):
    """动环设备 (一台 Modbus 从站, 通常挂在串口服务器的某个端口上)。"""
    __tablename__ = "env_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)          # 设备名称, 如 南部院区_温湿度_1
    category = Column(String(32), default="other")      # temp_humidity/smoke/water/ups/other
    protocol = Column(String(16), default="modbus_tcp")
    ip = Column(String(64), nullable=False, index=True)  # 串口服务器/网关 IP
    port = Column(Integer, default=502)                  # 网关上的 TCP 端口
    slave_id = Column(Integer, default=1)                # Modbus 从站地址
    location = Column(String(256), default="")           # 安装位置(括号里那句描述)
    cluster = Column(String(64), default="")             # 采集集群/院区, 如 南部院区
    resource_group = Column(String(64), default="")      # 资源组, 如 机房环境设备
    poll_interval = Column(Integer, default=60)          # 轮询间隔(秒), 0=不自动轮询
    enabled = Column(Boolean, default=True)
    status = Column(String(16), default="unknown")       # online/offline/unknown
    last_seen = Column(DateTime, nullable=True)
    last_error = Column(String(256), default="")
    remark = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    # lazy="selectin": 列表接口要连点位一起返回, 异步会话里惰性加载会抛
    # MissingGreenlet, 所以改成随主查询一次性预加载。
    points = relationship("EnvPoint", back_populates="device",
                          cascade="all, delete-orphan", lazy="selectin",
                          order_by="EnvPoint.sort, EnvPoint.id")


class EnvPoint(Base):
    """设备上的一个点位 (一个寄存器读出来的一个值)。"""
    __tablename__ = "env_points"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("env_devices.id"), index=True)
    name = Column(String(128), nullable=False)      # 显示名, 如 温度
    key = Column(String(32), default="")            # 告警用标识: temperature/smoke/...
    fc = Column(Integer, default=3)                 # 功能码 1/2/3/4
    address = Column(Integer, default=0)            # 寄存器地址 (0 基)
    data_type = Column(String(16), default="u16")   # u16/s16/u32/s32/bit
    bit_index = Column(Integer, default=0)          # data_type=bit 时取第几位
    scale = Column(Float, default=1.0)              # 系数, 如 0.1
    offset = Column(Float, default=0.0)             # 偏移
    unit = Column(String(16), default="")           # ℃ / % / V / A
    group = Column(String(32), default="")          # 点位分组: 交流输入/电池/... (UPS 多点位分类显示)
    # 开关量的"报警值": 取值等于这个数就算异常(烟感/水浸常用)
    alarm_value = Column(Float, nullable=True)
    value = Column(Float, nullable=True)            # 最近一次读到的值
    raw = Column(Integer, nullable=True)            # 最近一次原始寄存器值(排查用)
    ok = Column(Boolean, default=True)              # 最近一次读取是否成功
    updated_at = Column(DateTime, nullable=True)
    sort = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)

    device = relationship("EnvDevice", back_populates="points")


class EnvPointReading(Base):
    """点位时序数据 (画曲线用)。"""
    __tablename__ = "env_point_readings"

    id = Column(Integer, primary_key=True, index=True)
    point_id = Column(Integer, ForeignKey("env_points.id"), index=True)
    value = Column(Float, nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)


class Alert(Base):
    """Alert raised by the alert engine."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(16), default="info")  # info / warning / critical
    category = Column(String(64), default="")  # server / vmware / network / storage / env
    source = Column(String(128), default="")  # device name / ip
    title = Column(String(256), nullable=False)
    detail = Column(Text, default="")
    status = Column(String(16), default="open")  # open / ack / resolved
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)

    # ---- 以下为"可配置告警规则"改造后新增 (2026-09) ----
    rule_id = Column(Integer, nullable=True, index=True)        # 命中的规则ID
    metric = Column(String(64), default="")                     # cpu_percent / mem_percent / ...
    value = Column(Float, nullable=True)                        # 触发时的实际值
    hit_count = Column(Integer, default=1)                      # 连续命中次数(用于 duration 判定)
    last_notify_at = Column(DateTime, nullable=True)            # 上次发送通知时间(静默期用)
    last_notify_level = Column(String(16), nullable=True)       # 上次通知的级别(级别升级时不受静默期限制)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlertRule(Base):
    """可配置的告警规则 (界面可视化维护)。

    典型配置: "服务器 CPU 使用率 >= 80% 提示, >= 90% 告警"。
    """
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)                  # 规则名称
    category = Column(String(32), default="server")             # server/network/storage/env/vmware
    metric = Column(String(64), default="cpu_percent")          # 监控指标
    operator = Column(String(8), default="gte")                 # gte(>=) / lte(<=) / eq
    warning_threshold = Column(Float, default=80.0)             # 提示阈值 (如 80%)
    critical_threshold = Column(Float, default=90.0)            # 告警阈值 (如 90%)
    duration_times = Column(Integer, default=1)                 # 连续命中N次才告警(防抖动)
    silence_minutes = Column(Integer, default=30)               # 静默期: 同一告警X分钟内不重发
    notify_levels = Column(String(32), default="warning,critical")  # 哪些级别需要通知
    channel_ids = Column(String(128), default="")               # 通知渠道ID, 逗号分隔 "1,2"
    enabled = Column(Boolean, default=True)
    notify_on_recovery = Column(Boolean, default=True)          # 恢复时是否通知
    remark = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NotifyChannel(Base):
    """通知渠道配置。

    支持类型:
      dingtalk  钉钉群机器人 (支持加签与@某人)
      wecom     企业微信群机器人 (可推送到微信)
      sms       短信平台 —— 通用 HTTP 网关, 对接院内短信告警平台
      voice     电话告警 —— 通用 HTTP 网关, 对接电话告警盒子
      webhook   通用自定义 Webhook (Server酱/PushPlus/自建网关等)
    """
    __tablename__ = "notify_channels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False)                  # 渠道名称, 如"运维钉钉群"
    type = Column(String(32), default="dingtalk")               # dingtalk/wecom/sms/voice/webhook
    enabled = Column(Boolean, default=True)

    # ---- 钉钉 / 企业微信 / 通用 Webhook ----
    webhook_url = Column(String(512), default="")               # 机器人地址
    secret = Column(String(256), default="")                    # 钉钉加签密钥(可空)
    at_mobiles = Column(String(256), default="")                # 钉钉@的手机号, 逗号分隔
    at_all = Column(Boolean, default=False)                     # 是否@所有人

    # ---- 短信平台 / 电话盒子 / 自定义网关 ----
    targets = Column(String(512), default="")                   # 被叫号码(电话盒子/短信), 多个逗号分隔
    http_method = Column(String(8), default="POST")             # GET / POST
    http_url = Column(String(512), default="")                  # 平台接口地址
    http_headers = Column(Text, default="")                     # 请求头, JSON 格式
    http_body = Column(Text, default="")                        # 请求体模板, 支持变量替换
    # 变量: {title} {content} {short} {level} {source} {metric} {value} {time}
    #       号码别名: {targets} {phone} {mobile} {tel} {called} {callee} {to} {number}
    #       内容别名: {text} {msg} {tts} {play} {message} {speak}
    success_keyword = Column(String(128), default="")           # 响应中包含该字符串视为成功(空=只看HTTP 200)
    timeout_seconds = Column(Integer, default=10)

    remark = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlertNotifyLog(Base):
    """告警通知发送记录 (便于排查"为什么没收到")。"""
    __tablename__ = "alert_notify_logs"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, nullable=True, index=True)
    channel_id = Column(Integer, nullable=True)
    channel_name = Column(String(128), default="")
    channel_type = Column(String(32), default="")
    level = Column(String(16), default="")
    target = Column(String(256), default="")                    # 摘要: 告警标题
    success = Column(Boolean, default=False)
    response = Column(Text, default="")                         # 平台返回内容(截断)
    error = Column(Text, default="")
    sent_at = Column(DateTime, default=datetime.utcnow, index=True)


class ChatMessage(Base):
    """AI assistant conversation history."""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), index=True)
    role = Column(String(16), default="user")  # user / assistant / system
    content = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SystemConfig(Base):
    """Key-value system settings (LLM config etc.), overrides .env defaults."""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    """Platform user."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    role = Column(String(32), default="admin")
    created_at = Column(DateTime, default=datetime.utcnow)
