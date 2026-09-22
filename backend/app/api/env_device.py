"""动环设备 (Modbus TCP) 接口: 温湿度 / 烟感 / 水浸 / UPS

和 `devices.py` 里老的 /env 不同: 老接口是被动接收推送、只有温湿度两个字段;
这里每台设备可以挂任意多个点位, 平台按间隔主动去轮询 Modbus。
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import EnvDevice, EnvPoint, EnvPointReading
from app.services import modbus_service
from app.services.alert_engine import evaluate_metric, flush_notifications

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/env", tags=["env-modbus"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PointIn(BaseModel):
    group: str = ""
    name: str
    key: str = ""
    fc: int = 3
    address: int = 0
    data_type: str = "u16"
    bit_index: int = 0
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    alarm_value: Optional[float] = None
    sort: int = 0
    enabled: bool = True


class DeviceIn(BaseModel):
    name: str
    category: str = "other"
    protocol: str = "modbus_tcp"
    ip: str
    port: int = 502
    slave_id: int = 1
    location: str = ""
    cluster: str = ""
    resource_group: str = ""
    poll_interval: int = 60
    enabled: bool = True
    remark: str = ""


class PointOut(PointIn):
    id: int
    device_id: int
    value: Optional[float] = None
    raw: Optional[int] = None
    ok: bool = True
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceOut(DeviceIn):
    id: int
    status: str = "unknown"
    last_seen: Optional[datetime] = None
    last_error: str = ""
    created_at: Optional[datetime] = None
    points: List[PointOut] = []

    class Config:
        from_attributes = True


CATEGORY_LABELS = {
    "temp_humidity": "温湿度",
    "smoke": "烟感",
    "water": "水浸",
    "ups": "UPS",
    "aircon": "精密空调",
    "power": "配电",
    "door": "门禁",
    "other": "其他",
}

# 新建设备时按类型预置的点位 —— 省得用户去猜寄存器
#
# 注意:
# * 温湿度: 现场实测(南西展区两台, raw 630/189、588/237)寄存器是 **0=湿度 1=温度**,
#   按老习惯"0=温度"配出来会显示 63℃/58℃, 所以模板按现场实测反过来配。
# * UPS: 科士达 YMK 系列协议(输入寄存器 FC04)。协议文档用 30001 编号,
#   实际 wire 地址 = 编号-1 (30001 -> 30000), 用探测器 --start 30000 可核对。
CATEGORY_TEMPLATES = {
    "temp_humidity": [
        # 现场实测: 寄存器 0=湿度, 1=温度, 倍率 0.1。
        # 温度必须是**有符号 s16**(零下环境 u16 会显示成 6553.x℃)。
        {"name": "湿度", "key": "humidity", "fc": 3, "address": 0,
         "scale": 0.1, "unit": "%", "data_type": "u16", "group": "温湿度"},
        {"name": "温度", "key": "temperature", "fc": 3, "address": 1,
         "scale": 0.1, "unit": "℃", "data_type": "s16", "group": "温湿度"},
    ],
    "smoke": [
        {"name": "烟雾状态", "key": "smoke", "fc": 2, "address": 0,
         "data_type": "bit", "alarm_value": 1, "unit": ""},
    ],
    "water": [
        {"name": "漏水状态", "key": "water", "fc": 1, "address": 0,
         "data_type": "bit", "alarm_value": 1, "unit": ""},
    ],
    # 精密空调 —— 依据现场 sweep 实测(192.168.204.71:5004 从站1, FC03):
    #   addr0=24.9  addr1=60.0  addr13=1  addr16=24.0  addr17=2.0  addr18=50.0  addr19=5.0
    # **重要**: 这台空调的温湿度顺序是 **0=温度、1=湿度**, 与南院区那批独立
    # 温湿度探头(0=湿度、1=温度)**正好相反**! 所以不能全平台套同一个顺序,
    # 判据是: 机房温度不可能超过 45℃, 谁大谁就是湿度。
    "aircon": [
        {"name": "回风温度", "key": "temperature", "fc": 3, "address": 0,
         "scale": 0.1, "unit": "℃", "data_type": "s16", "group": "环境"},
        {"name": "回风湿度", "key": "humidity", "fc": 3, "address": 1,
         "scale": 0.1, "unit": "%", "data_type": "u16", "group": "环境"},
        {"name": "运行状态", "fc": 3, "address": 13, "scale": 1, "unit": "",
         "data_type": "u16", "group": "状态"},
        {"name": "设定温度", "fc": 3, "address": 16, "scale": 0.1, "unit": "℃",
         "data_type": "s16", "group": "设定"},
        {"name": "温度回差", "fc": 3, "address": 17, "scale": 0.1, "unit": "℃",
         "data_type": "s16", "group": "设定"},
        {"name": "设定湿度", "fc": 3, "address": 18, "scale": 0.1, "unit": "%",
         "data_type": "u16", "group": "设定"},
        {"name": "湿度回差", "fc": 3, "address": 19, "scale": 0.1, "unit": "%",
         "data_type": "u16", "group": "设定"},
    ],
    "ups": [
        # ---- 整机交流输入 (FC04 @ 30001~30010 -> 地址 30000~30009) ----
        {"name": "输入A相电压", "key": "ups_voltage", "fc": 4, "address": 30000,
         "scale": 0.1, "unit": "V", "group": "交流输入"},
        {"name": "输入B相电压", "fc": 4, "address": 30001, "scale": 0.1,
         "unit": "V", "group": "交流输入"},
        {"name": "输入C相电压", "fc": 4, "address": 30002, "scale": 0.1,
         "unit": "V", "group": "交流输入"},
        {"name": "输入频率", "fc": 4, "address": 30003, "scale": 0.1,
         "unit": "Hz", "group": "交流输入"},
        {"name": "输入A相电流", "fc": 4, "address": 30004, "scale": 0.1,
         "unit": "A", "group": "交流输入"},
        {"name": "输入B相电流", "fc": 4, "address": 30005, "scale": 0.1,
         "unit": "A", "group": "交流输入"},
        {"name": "输入C相电流", "fc": 4, "address": 30006, "scale": 0.1,
         "unit": "A", "group": "交流输入"},
        {"name": "输入功率因数", "fc": 4, "address": 30007, "scale": 0.01,
         "unit": "", "group": "交流输入"},
        # ---- 整机交流输出 (30011~30023 -> 30010~30022) ----
        {"name": "输出A相电压", "fc": 4, "address": 30010, "scale": 0.1,
         "unit": "V", "group": "交流输出"},
        {"name": "输出B相电压", "fc": 4, "address": 30011, "scale": 0.1,
         "unit": "V", "group": "交流输出"},
        {"name": "输出C相电压", "fc": 4, "address": 30012, "scale": 0.1,
         "unit": "V", "group": "交流输出"},
        {"name": "输出频率", "fc": 4, "address": 30013, "scale": 0.1,
         "unit": "Hz", "group": "交流输出"},
        {"name": "输出A相电流", "fc": 4, "address": 30014, "scale": 0.1,
         "unit": "A", "group": "交流输出"},
        {"name": "输出B相电流", "fc": 4, "address": 30015, "scale": 0.1,
         "unit": "A", "group": "交流输出"},
        {"name": "输出C相电流", "fc": 4, "address": 30016, "scale": 0.1,
         "unit": "A", "group": "交流输出"},
        {"name": "输出A相有功功率", "fc": 4, "address": 30017, "scale": 0.1,
         "unit": "kW", "group": "交流输出"},
        {"name": "输出B相有功功率", "fc": 4, "address": 30018, "scale": 0.1,
         "unit": "kW", "group": "交流输出"},
        {"name": "输出C相有功功率", "fc": 4, "address": 30019, "scale": 0.1,
         "unit": "kW", "group": "交流输出"},
        {"name": "输出A相负载率", "key": "ups_load", "fc": 4, "address": 30020,
         "scale": 0.1, "unit": "%", "group": "交流输出"},
        # ---- 整机旁路 (30027~30030 -> 30026~30029) ----
        {"name": "旁路A相电压", "fc": 4, "address": 30026, "scale": 0.1,
         "unit": "V", "group": "旁路"},
        {"name": "旁路B相电压", "fc": 4, "address": 30027, "scale": 0.1,
         "unit": "V", "group": "旁路"},
        {"name": "旁路C相电压", "fc": 4, "address": 30028, "scale": 0.1,
         "unit": "V", "group": "旁路"},
        {"name": "旁路频率", "fc": 4, "address": 30029, "scale": 0.1,
         "unit": "Hz", "group": "旁路"},
        # ---- 整机电池数据 (30031~30041 -> 30030~30040) ----
        {"name": "正组电池电压", "fc": 4, "address": 30030, "scale": 0.1,
         "unit": "V", "group": "电池"},
        {"name": "负组电池电压", "fc": 4, "address": 30031, "scale": 0.1,
         "unit": "V", "group": "电池"},
        {"name": "正组电池放电电流", "fc": 4, "address": 30032, "scale": 0.1,
         "unit": "A", "group": "电池"},
        {"name": "正组电池充电电流", "fc": 4, "address": 30034, "scale": 0.1,
         "unit": "A", "group": "电池"},
        {"name": "电池容量率", "key": "ups_battery", "fc": 4, "address": 30036,
         "scale": 1, "unit": "%", "group": "电池"},
        {"name": "电池后备时间", "key": "ups_runtime", "fc": 4, "address": 30037,
         "scale": 0.1, "unit": "min", "group": "电池"},
        {"name": "电池温度", "fc": 4, "address": 30038, "scale": 0.1,
         "unit": "℃", "group": "电池"},
        {"name": "电池放电时间", "fc": 4, "address": 30040, "scale": 1,
         "unit": "min", "group": "电池"},
        # ---- 整机状态信息 (30040 环境温度 -> 30039) ----
        {"name": "环境温度", "key": "ups_temp", "fc": 4, "address": 30039,
         "scale": 0.1, "unit": "℃", "group": "状态"},
    ],
}

METRIC_LABELS_FOR_UI = {
    "temperature": "温度", "humidity": "湿度", "smoke": "烟雾报警",
    "water": "漏水报警", "door": "门禁状态", "power": "市电状态",
    "ups_voltage": "UPS输入电压", "ups_battery": "UPS电池容量",
    "ups_load": "UPS负载率", "ups_temp": "UPS温度", "ups_runtime": "UPS剩余续航",
}


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------
@router.get("/meta")
async def env_meta():
    """界面下拉用: 设备类型 / 预置点位模板 / 可告警的点位标识。"""
    return {
        "categories": [{"value": k, "label": v} for k, v in CATEGORY_LABELS.items()],
        "templates": CATEGORY_TEMPLATES,
        "point_keys": [{"value": k, "label": v} for k, v in METRIC_LABELS_FOR_UI.items()],
        "protocols": [
            {"value": "modbus_tcp", "label": "Modbus TCP（网关模式）"},
            {"value": "modbus_rtu", "label": "RTU over TCP（透传/串口服务器）"},
        ],
        "function_codes": [
            {"value": 1, "label": "01 线圈 (Coil, 可读写开关量)"},
            {"value": 2, "label": "02 离散输入 (只读开关量)"},
            {"value": 3, "label": "03 保持寄存器 (4xxxx)"},
            {"value": 4, "label": "04 输入寄存器 (3xxxx)"},
        ],
        "data_types": [
            {"value": "u16", "label": "u16 无符号16位"},
            {"value": "s16", "label": "s16 有符号16位"},
            {"value": "u32", "label": "u32 无符号32位"},
            {"value": "s32", "label": "s32 有符号32位"},
            {"value": "bit", "label": "bit 取某一位"},
        ],
    }


# ---------------------------------------------------------------------------
# 设备 CRUD
# ---------------------------------------------------------------------------
async def _load_device(db: AsyncSession, device_id: int) -> Optional[EnvDevice]:
    """提交后重新把设备连同点位读回来。

    异步会话下单靠 db.refresh() 拿不到已提交的关联集合(提交后属性失效,
    再访问会触发惰性 IO 抛 MissingGreenlet), 所以统一走显式重查。
    """
    return (await db.execute(
        select(EnvDevice).where(EnvDevice.id == device_id)
    )).scalars().first()


@router.get("/devices", response_model=List[DeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(EnvDevice).order_by(EnvDevice.cluster, EnvDevice.id)
    )).scalars().all()
    return rows


@router.post("/devices", response_model=DeviceOut)
async def create_device(data: DeviceIn, db: AsyncSession = Depends(get_db)):
    d = EnvDevice(**data.model_dump())
    db.add(d)
    await db.flush()
    # 按设备类型预置点位, 用户改改地址就能用
    for i, t in enumerate(CATEGORY_TEMPLATES.get(d.category, [])):
        db.add(EnvPoint(device_id=d.id, sort=i, **t))
    new_id = d.id
    await db.commit()
    return await _load_device(db, new_id)


@router.post("/devices/batch")
async def batch_create_devices(items: List[DeviceIn], db: AsyncSession = Depends(get_db)):
    """批量录入设备。

    现场往往是一张表上抄下来的一批设备(IP/端口/从站地址),
    一台台点界面太慢, 用这个接口一次灌进去。已存在同名+同地址的会跳过。
    """
    created, skipped = [], []
    for item in items:
        exists = (await db.execute(
            select(EnvDevice).where(
                EnvDevice.name == item.name,
                EnvDevice.ip == item.ip,
                EnvDevice.port == item.port,
            )
        )).scalars().first()
        if exists:
            skipped.append(item.name)
            continue
        d = EnvDevice(**item.model_dump())
        db.add(d)
        await db.flush()
        for i, t in enumerate(CATEGORY_TEMPLATES.get(d.category, [])):
            db.add(EnvPoint(device_id=d.id, sort=i, **t))
        created.append(d.name)
    await db.commit()
    return {"created": created, "skipped": skipped,
            "hint": "预置点位是常见默认值, 跑一次轮询后用「试读」核对地址和系数"}


@router.put("/devices/{device_id}", response_model=DeviceOut)
async def update_device(device_id: int, data: DeviceIn,
                        db: AsyncSession = Depends(get_db)):
    d = await db.get(EnvDevice, device_id)
    if not d:
        raise HTTPException(404, "设备不存在")
    for k, v in data.model_dump().items():
        setattr(d, k, v)
    await db.commit()
    return await _load_device(db, device_id)


@router.delete("/devices/{device_id}")
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    d = await db.get(EnvDevice, device_id)
    if not d:
        raise HTTPException(404, "设备不存在")
    ids = [p.id for p in (await db.execute(
        select(EnvPoint).where(EnvPoint.device_id == device_id))).scalars().all()]
    if ids:
        await db.execute(delete(EnvPointReading).where(EnvPointReading.point_id.in_(ids)))
    await db.delete(d)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 点位 CRUD
# ---------------------------------------------------------------------------
@router.get("/devices/{device_id}/points", response_model=List[PointOut])
async def list_points(device_id: int, db: AsyncSession = Depends(get_db)):
    return (await db.execute(
        select(EnvPoint).where(EnvPoint.device_id == device_id)
        .order_by(EnvPoint.sort, EnvPoint.id)
    )).scalars().all()


@router.post("/devices/{device_id}/points", response_model=PointOut)
async def add_point(device_id: int, data: PointIn, db: AsyncSession = Depends(get_db)):
    if not await db.get(EnvDevice, device_id):
        raise HTTPException(404, "设备不存在")
    p = EnvPoint(device_id=device_id, **data.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@router.put("/points/{point_id}", response_model=PointOut)
async def update_point(point_id: int, data: PointIn, db: AsyncSession = Depends(get_db)):
    p = await db.get(EnvPoint, point_id)
    if not p:
        raise HTTPException(404, "点位不存在")
    for k, v in data.model_dump().items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/points/{point_id}")
async def delete_point(point_id: int, db: AsyncSession = Depends(get_db)):
    p = await db.get(EnvPoint, point_id)
    if not p:
        raise HTTPException(404, "点位不存在")
    await db.execute(delete(EnvPointReading).where(EnvPointReading.point_id == point_id))
    await db.delete(p)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 轮询
# ---------------------------------------------------------------------------
@router.post("/devices/{device_id}/poll")
async def poll_one(device_id: int, db: AsyncSession = Depends(get_db)):
    """手动触发一次轮询。同时把点位值送进告警引擎(阈值/通知复用告警模块)。"""
    d = await db.get(EnvDevice, device_id)
    if not d:
        raise HTTPException(404, "设备不存在")

    res = await modbus_service.poll_device(db, d)

    # 收集点位对象, 喂给告警引擎
    points = (await db.execute(
        select(EnvPoint).where(EnvPoint.device_id == device_id)
    )).scalars().all()
    by_name = {p.name: p for p in points}
    for item in res.get("values", []):
        item["point_obj"] = by_name.get(item["point"])
    stat = await modbus_service.evaluate_device_points(db, d, res.get("values", []))
    await db.commit()
    await flush_notifications(db)

    res.pop("values", None)
    res.update(stat)
    return res


@router.post("/poll-all")
async def poll_all(db: AsyncSession = Depends(get_db)):
    """把所有启用的动环设备轮询一遍(界面上"立即刷新"用)。"""
    devices = (await db.execute(
        select(EnvDevice).where(EnvDevice.enabled == True)  # noqa: E712
    )).scalars().all()
    out = []
    for d in devices:
        res = await modbus_service.poll_device(db, d)
        points = (await db.execute(
            select(EnvPoint).where(EnvPoint.device_id == d.id)
        )).scalars().all()
        by_name = {p.name: p for p in points}
        for item in res.get("values", []):
            item["point_obj"] = by_name.get(item["point"])
        stat = await modbus_service.evaluate_device_points(db, d, res.get("values", []))
        res.pop("values", None)
        res.update(stat)
        out.append(res)
    await db.commit()
    await flush_notifications(db)
    return {"devices": out}


@router.post("/devices/{device_id}/read-point")
async def read_one_point(device_id: int, body: dict,
                         db: AsyncSession = Depends(get_db)):
    """配点位时"试读一下", 不落库。用于确认地址/系数填对没有。"""
    d = await db.get(EnvDevice, device_id)
    if not d:
        raise HTTPException(404, "设备不存在")
    data = body or {}
    tmp = EnvPoint(
        device_id=device_id,
        name=data.get("name", "test"),
        key="",
        fc=int(data.get("fc", 3)),
        address=int(data.get("address", 0)),
        data_type=data.get("data_type", "u16"),
        bit_index=int(data.get("bit_index", 0)),
        scale=float(data.get("scale", 1) or 1),
        offset=float(data.get("offset", 0) or 0),
    )

    def _do():
        with modbus_service.make_client(d) as c:
            return modbus_service.read_point(c, tmp)

    try:
        import asyncio
        value, raw = await asyncio.wait_for(asyncio.to_thread(_do), timeout=10)
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
    return {"success": True, "value": value, "raw": raw}


# ---------------------------------------------------------------------------
# 历史
# ---------------------------------------------------------------------------
@router.get("/points/{point_id}/history")
async def point_history(point_id: int, hours: int = 24,
                        db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=max(1, min(hours, 24 * 30)))
    rows = (await db.execute(
        select(EnvPointReading).where(
            EnvPointReading.point_id == point_id,
            EnvPointReading.collected_at >= since,
        ).order_by(EnvPointReading.collected_at)
    )).scalars().all()
    return [{"value": r.value, "collected_at": r.collected_at} for r in rows]


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """仪表盘用: 各类设备数量 + 异常点位数。"""
    devs = (await db.execute(select(EnvDevice))).scalars().all()
    pts = (await db.execute(select(EnvPoint))).scalars().all()
    by_cat: dict = {}
    for d in devs:
        by_cat[d.category] = by_cat.get(d.category, 0) + 1
    alarm = [p for p in pts if p.alarm_value is not None and p.value is not None
             and abs(p.value - p.alarm_value) < 1e-6]
    offline = [d for d in devs if d.status == "offline"]
    return {
        "devices": len(devs),
        "devices_online": len([d for d in devs if d.status == "online"]),
        "devices_offline": len(offline),
        "points": len(pts),
        "points_alarm": len(alarm),
        "by_category": [{"category": k, "label": CATEGORY_LABELS.get(k, k), "count": v}
                        for k, v in by_cat.items()],
        "alarm_points": [{"device": next((d.name for d in devs if d.id == p.device_id), ""),
                          "point": p.name, "value": p.value} for p in alarm],
    }
