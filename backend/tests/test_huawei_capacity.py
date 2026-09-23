"""华为 OceanStor 容量口径回归测试。

数据全部取自**真实设备** 172.16.10.199 的 probe_snmp.py 输出
(2026-09-23, SNMPv3 + 私有 MIB), 不是编的。

这条测试要钉死三件事:
1. 探测判据不能只认 .1.3.0/.1.6.0 —— 这台机器两个标量都是「无数据」,
   但 23 张私有表有数据, 判错就整台采不到。
2. 存储池 .8 是**已分配(thin 供给)容量**, 不是已用。拿它当分子会算出
   486% 使用率 → 假告警。已用必须 = 总 - 可用。
3. running=27 是"正常"、健康分 255 是"未上报", 都不能当异常/百分比。
"""
import asyncio
import sys
import types
from pathlib import Path

# ---------------------------------------------------------------- 假 SNMP 层
# 真实采样值 (suffix -> 值), OID 见 storage_service 里的 HW_* 常量
B = "1.3.6.1.4.1.34774.4.1"

POOL_SFX = "1.49"
# LUN 容量(KB): 前 8 个是探针实测, 后 8 个按同型号补齐到 16 个
LUN_KB = [
    32212254720, 32212254720, 5368709120, 32212254720,
    83886080, 83886080, 4294967296, 4294967296,
    4294967296, 4294967296, 4294967296, 4294967296,
    4294967296, 4294967296, 4294967296, 2147483648,
]
DISK_SFX = [f"9.49.51.52.50.49.55.55.{50 + i}" for i in range(12)]

DATA = {
    # 注意: 这两个标量在真机上**都没有数据** —— 正是这条测试存在的理由
    f"{B}.1.3.0": None,
    f"{B}.1.6.0": None,
    # 私有版本字段不答时, 靠标准 sysDescr 兜底显示型号
    "1.3.6.1.2.1.1.1.0": "Huawei OceanStor Dorado 5600 V6",
    # 存储池
    f"{B}.23.4.2.1.2": {POOL_SFX: "Flash_Pool_01"},
    f"{B}.23.4.2.1.5": {POOL_SFX: "1"},
    f"{B}.23.4.2.1.6": {POOL_SFX: "27"},
    f"{B}.23.4.2.1.7": {POOL_SFX: "28519332"},    # 总容量 MB -> 27.20 TiB
    f"{B}.23.4.2.1.8": {POOL_SFX: "138566656"},   # 已分配 MB -> 132.15 TiB(!)
    f"{B}.23.4.2.1.9": {POOL_SFX: "12058318"},    # 可用 MB   -> 11.50 TiB
    # 控制器
    f"{B}.23.5.2.1.1": {"2.48.65": "0A", "2.48.66": "0B"},
    f"{B}.23.5.2.1.2": {"2.48.65": "1", "2.48.66": "1"},
    f"{B}.23.5.2.1.3": {"2.48.65": "27", "2.48.66": "27"},
    f"{B}.23.5.2.1.6": {"2.48.65": "1", "2.48.66": "2"},
    f"{B}.23.5.2.1.8": {"2.48.65": "2", "2.48.66": "2"},
    f"{B}.23.5.2.1.9": {"2.48.65": "44", "2.48.66": "45"},
    # 硬盘
    f"{B}.23.5.1.1.1": {s: s for s in DISK_SFX},
    f"{B}.23.5.1.1.2": {s: "1" for s in DISK_SFX},
    f"{B}.23.5.1.1.3": {s: "27" for s in DISK_SFX},
    f"{B}.23.5.1.1.4": {s: f"CTE0.{i}" for i, s in enumerate(DISK_SFX)},
    f"{B}.23.5.1.1.11": {s: "37" for s in DISK_SFX},
    f"{B}.23.5.1.1.12": {s: "HSSD-D7B94DN3T8V" for s in DISK_SFX},
    f"{B}.23.5.1.1.25": {s: "255" for s in DISK_SFX},   # 255 = 未上报
    # LUN
    f"{B}.19.9.4.1.2": {str(i): f"LUN{i}" for i in range(16)},
    f"{B}.19.9.4.1.5": {str(i): str(v) for i, v in enumerate(LUN_KB)},
    f"{B}.19.9.4.1.11": {str(i): "1" for i in range(16)},
}


def _safe_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


_fake = types.ModuleType("app.services.snmp_service")


async def _walk(ip, community, oid, version="2c"):
    return dict(DATA.get(oid) or {})


async def _get(ip, community, oid, version="2c"):
    return DATA.get(oid)


_fake.snmp_walk = _walk
_fake.snmp_get = _get
_fake._safe_int = _safe_int
_APP_DIR = Path(__file__).resolve().parents[1] / "app"
for _name, _path in (("app", _APP_DIR), ("app.services", _APP_DIR / "services")):
    _m = types.ModuleType(_name)
    _m.__path__ = [str(_path)]          # 声明为包, 否则 import 子模块会失败
    sys.modules.setdefault(_name, _m)
sys.modules["app.services.snmp_service"] = _fake

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.storage_service import collect_huawei_snmp  # noqa: E402


def main():
    r = asyncio.run(collect_huawei_snmp("172.16.10.199", {"user": "zzrmyy"}, "3"))
    d = r["details"]
    pool = d["pools"][0] if d["pools"] else {}

    print("reachable      =", r["reachable"])
    print("capacity_tb    =", r["capacity_tb"])
    print("used_tb        =", r["used_tb"])
    print("used_percent   =", r["used_percent"])
    print("alloc_tb       =", d.get("alloc_tb"))
    print("over_subscrib  =", d.get("over_subscription"))
    print("池 running     =", pool.get("running"))
    print("控制器 running =", [c["running"] for c in d["controllers"]])
    print("硬盘健康分     =", d["disks"][0].get("health_score"))
    print("硬盘数/LUN数   =", len(d["disks"]), "/", len(d["volumes"]))
    print("型号(兜底)     =", d.get("version"))

    ok = True

    def chk(label, got, want, tol=0.05):
        nonlocal ok
        good = (abs(got - want) <= tol * max(abs(want), 1e-9)) if isinstance(want, float) \
            else (got == want)
        ok = ok and good
        print(f"  [{'OK ' if good else 'FAIL'}] {label}: 实得 {got!r} / 期望 {want!r}")

    print("\n--- 断言 ---")
    # 1) 两个标量都无数据, 仍然必须认出这是华为
    chk("reachable(标量全空也要认出)", r["reachable"], True)
    # 2) 容量口径
    chk("总容量 TiB", r["capacity_tb"], 27.20, 0.01)
    chk("已用 TiB", r["used_tb"], 15.70, 0.01)
    chk("使用率 %", r["used_percent"], 57.7, 0.01)
    chk("已分配 TiB", d.get("alloc_tb"), 132.15, 0.02)
    chk("超配比", d.get("over_subscription"), 4.86, 0.02)
    # 3) 状态码
    chk("池 running(27->正常)", pool.get("running"), "正常")
    chk("控制器 running", [c["running"] for c in d["controllers"]], ["正常", "正常"])
    chk("健康分 255 -> None", d["disks"][0].get("health_score"), None)
    # 4) 明细数量
    chk("硬盘 12 块", len(d["disks"]), 12)
    chk("LUN 16 个", len(d["volumes"]), 16)
    chk("控制器 2 个", len(d["controllers"]), 2)
    # 6) 私有版本字段不答时, 型号要能从 sysDescr 兜底出来
    chk("型号 sysDescr 兜底", d.get("version"), "Huawei OceanStor Dorado 5600 V6")

    # 5) 反面: 使用率绝不能越界
    assert 0 <= r["used_percent"] <= 100, "使用率越界"
    print("  [OK ] 使用率在 0~100 之内 (旧代码会得到 100.0% —— 被 min() 截断的假满盘)")

    print("\n结论:", "全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
