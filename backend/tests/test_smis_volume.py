"""SMI-S 卷容量回归测试 —— 用桩 pywbem, 不连真设备。

为什么要有这条测试: 华为 SNMP 的 LUN 表拿不到"已用容量"(Dorado 5600 V6 实测只有
11 列, 无该字段), 卷级容量告警只能靠 SMI-S 的 ConsumableBlocks 补齐。
这段换算是新写的, 必须有测试钉住, 否则算错了界面上就是一笔糊涂账。

桩数据按 SMI-S 真实语义构造:
  NumberOfBlocks  = 总块数
  ConsumableBlocks= 剩余可用块数   -> 已用 = (总 - 剩余) * BlockSize
  BlockSize       = 每块字节数(华为常见 512)
"""
import sys
import types
import unittest
from pathlib import Path

# ---------------------------------------------------------------- 桩 pywbem
BLOCK = 512
# (名称, 总块数, 剩余块数) —— 对应 30TB/5TB/4TB/80GB 四个真实 LUN
VOLS = [
    ("H3C_VM_1", 64424509440, 22011707392),      # 30TB, 已用约 19.75TB -> 65.8%
    ("DMZ_ESXI_5TB", 10737418240, 8589934592),   # 5TB,  已用 1TB       -> 20.0%
    ("VPLEX_Flash1_DB01_4TB", 8589934592, 2147483648),  # 4TB, 已用 3TB   -> 75.0%
    ("VPLEX_Metalun1", 167772160, 167772160),    # 80GB, 剩余=总        -> 0.0%
]
POOLS = [("Flash_Pool_01", 28519332 * 1024 * 1024, 12059412 * 1024 * 1024, True)]
DISKS = [("CTE0.0", 3840000000000)]


class _FakeInst(dict):
    pass


def _mk(**kw):
    return _FakeInst(kw)


class _FakeConn:
    def __init__(self, *a, **kw):
        pass

    def EnumerateClassNames(self):
        return ["CIM_StorageVolume"]

    def EnumerateInstances(self, cls):
        if cls == "CIM_StorageVolume":
            return [_mk(ElementName=n, NumberOfBlocks=t, ConsumableBlocks=c,
                        BlockSize=BLOCK, OperationalStatus=[2]) for n, t, c in VOLS]
        if cls == "CIM_StoragePool":
            return [_mk(ElementName=n, Primordial=int(p),
                        TotalManagedSpace=t, RemainingManagedSpace=r)
                    for n, t, r, p in POOLS]
        if cls == "CIM_PhysicalDisk":
            return [_mk(ElementName=n, Capacity=c, OperationalStatus=[2])
                    for n, c in DISKS]
        if cls == "CIM_ComputerSystem":
            return [_mk(ElementName="CTE0", OperationalStatus=[2])]
        return []


_fake = types.ModuleType("pywbem")
_fake.WBEMConnection = _FakeConn
_fake.Error = Exception
sys.modules["pywbem"] = _fake

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.storage_service import _collect_storage_wbem_sync  # noqa: E402


class TestSmisVolume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _collect_storage_wbem_sync("1.2.3.4", "u", "p")
        cls.vols = {v["name"]: v for v in cls.r["details"]["volumes"]}

    def test_reachable(self):
        self.assertTrue(self.r["reachable"])

    def test_source_marked(self):
        self.assertEqual(self.r.get("source"), "smi-s")

    def _calc(self, idx):
        n, total, left = VOLS[idx]
        v = self.vols[n]
        self.assertIsNotNone(v["size_tb"])
        self.assertAlmostEqual(v["alloc_tb"], v["size_tb"], places=3)
        self.assertIsNotNone(v["used_tb"])
        self.assertIsNotNone(v["used_percent"])
        exp_pct = (total - left) / total * 100
        self.assertAlmostEqual(v["used_percent"], round(exp_pct, 1), places=1)
        return v

    def test_vol_used_percent(self):
        # 四个 LUN 逐一对拍: 已用 = (总块 - 剩余块) * 块大小
        for i in range(len(VOLS)):
            self._calc(i)

    def test_zero_used_when_consumable_equals_total(self):
        # 剩余块 == 总块 -> 已用 0。这里必须是 0.0 而不是 None:
        # None 代表"取不到", 0 代表"算出来了是 0", 两者不能混
        v = self.vols["VPLEX_Metalun1"]
        self.assertEqual(v["used_percent"], 0.0)
        self.assertEqual(v["used_tb"], 0.0)

    def test_used_none_when_no_consumable_blocks(self):
        # 反向验证: 没有 ConsumableBlocks 字段时必须留 None(前端显示 '-')
        import types as _t
        sys.modules["pywbem"].WBEMConnection = type(
            "C", (_FakeConn,), {
                "EnumerateInstances": lambda self, cls: (
                    [_mk(ElementName="X", NumberOfBlocks=1000, BlockSize=512)]
                    if cls == "CIM_StorageVolume" else _FakeConn().EnumerateInstances(cls))
            })
        try:
            r = _collect_storage_wbem_sync("1.2.3.4", "u", "p")
            v = r["details"]["volumes"][0]
            self.assertIsNone(v["used_tb"])
            self.assertIsNone(v["used_percent"])
        finally:
            sys.modules["pywbem"].WBEMConnection = _FakeConn

    def test_high_usage_value(self):
        # 30TB 那个: 已用 19.75TB, 使用率 65.8%
        v = self.vols["H3C_VM_1"]
        self.assertAlmostEqual(v["size_tb"], 30.0, places=1)
        self.assertAlmostEqual(v["used_tb"], 19.75, places=1)
        self.assertAlmostEqual(v["used_percent"], 65.8, places=1)

    def test_pool_and_disk(self):
        p = self.r["details"]["pools"][0]
        self.assertAlmostEqual(p["size_tb"], 27.20, places=1)
        self.assertAlmostEqual(p["used_percent"], 57.7, places=1)
        d = self.r["details"]["disks"][0]
        self.assertAlmostEqual(d["size_tb"], 3.492, places=2)

    def test_device_capacity(self):
        self.assertAlmostEqual(self.r["capacity_tb"], 27.20, places=1)
        self.assertAlmostEqual(self.r["used_percent"], 57.7, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
