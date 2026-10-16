# -*- coding: utf-8 -*-
"""Windows 平台采集器。

支持系统: Windows Server 2008 / 2008R2 / 2012 / 2012R2 / 2016 / 2019。
依赖: 仅使用 psutil + 系统自带 sc.exe / 注册表, 无需额外组件。
注意: 建议以管理员权限运行, 否则监听端口-进程映射可能无法获取。
兼容性说明: Windows 2008/2008R2 请安装 Python 3.8(3.8 是官方支持该系统的最后版本)。
"""
import ctypes
import locale
import re
import subprocess
import sys

from .base import BaseCollector, GB

# Windows 上隐藏子进程(sc / wmic / powershell)的控制台窗口。
# 否则每次采集循环都会闪出这些控制台窗口(每 5 秒一次), 还会抢走用户
# 在服务器上的输入焦点(表现为"什么都输入不了")。
# 非 Windows 平台不设置这些值(避免属性不存在报错)。
_RUN_KWARGS = {}
if sys.platform == "win32":
    try:
        _si = subprocess.STARTUPINFO()
        _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _si.wShowWindow = 0  # SW_HIDE
        _RUN_KWARGS["startupinfo"] = _si
        # CREATE_NO_WINDOW 从 Python 3.7 起在 Windows 上可用, 兜底为 0
        _RUN_KWARGS["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        except Exception:
            # 极端情况下(如旧 Python)拿不到 STARTUPINFO, 仅用 CREATE_NO_WINDOW
            _RUN_KWARGS["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# WNetAddConnection2W 连接 SMB 共享时用的结构(服务身份下访问共享需带凭据)
class NETRESOURCEW(ctypes.Structure):
    _fields_ = [
        ("dwScope", ctypes.c_ulong),
        ("dwType", ctypes.c_ulong),
        ("dwDisplayType", ctypes.c_ulong),
        ("dwUsage", ctypes.c_ulong),
        ("lpLocalName", ctypes.c_wchar_p),
        ("lpRemoteName", ctypes.c_wchar_p),
        ("lpComment", ctypes.c_wchar_p),
        ("lpProvider", ctypes.c_wchar_p),
    ]


# Windows 内部版本号 -> 产品名映射
_WIN_NAMES = {
    (6, 0): "Windows Server 2008",
    (6, 1): "Windows Server 2008 R2",
    (6, 2): "Windows Server 2012",
    (6, 3): "Windows Server 2012 R2",
    (10, 0): "Windows Server 2016/2019",
}

# 按 build 号细分 Windows 10 内核的服务器版本
_WIN10_BUILDS = [
    (20348, "Windows Server 2022"),
    (17763, "Windows Server 2019"),
    (14393, "Windows Server 2016"),
]


def _run_cmd(cmd, timeout=30):
    """执行命令并按系统编码解码输出, 失败返回空字符串。

    在 Windows 上通过 _RUN_KWARGS 隐藏子进程的控制台窗口,
    避免采集循环每 5 秒闪窗并抢走用户焦点。
    """
    try:
        raw = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=timeout, **_RUN_KWARGS,
        )
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return raw.decode(encoding, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


class WindowsCollector(BaseCollector):
    """Windows 采集器实现。"""

    os_type = "windows"

    def os_distro(self):
        """发行版名称: Microsoft Windows Server。"""
        return "Microsoft Windows Server"

    def os_version(self):
        """系统版本, 如 '2019 (10.0.17763)'。"""
        w = sys.getwindowsversion()
        version_str = "{}.{}.{}".format(w.major, w.minor, w.build)
        name = _WIN_NAMES.get((w.major, w.minor), "Windows")
        # Windows Server 2016/2019/2022 内核均为 10.0, 按 build 号细分
        if (w.major, w.minor) == (10, 0):
            for build, product in _WIN10_BUILDS:
                if w.build >= build:
                    name = product
                    break
        return "{} ({})".format(name, version_str)

    def cpu_model(self):
        """CPU 型号: 优先读注册表, 失败时回退到 wmic 命令。"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            try:
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
            finally:
                winreg.CloseKey(key)
        except OSError:
            pass
        out = _run_cmd(["wmic", "cpu", "get", "Name", "/value"])
        m = re.search(r"Name=(.+)", out)
        return m.group(1).strip() if m else "unknown"

    def load_avg(self):
        """Windows 无 Unix 负载概念, 固定返回 0。"""
        return 0.0

    def collect_services(self, limit=500):
        """服务状态列表。

        优先解析 sc.exe 输出(兼容中英文系统、无需 PowerShell),
        失败时回退到 PowerShell Get-Service。
        """
        services = self._services_from_sc(limit)
        if not services:
            services = self._services_from_powershell(limit)
        return services

    def _services_from_sc(self, limit):
        """通过 'sc query' 解析服务状态, 兼容中英文系统输出。"""
        out = _run_cmd(["sc", "query", "type=", "service", "state=", "all"], timeout=60)
        if not out:
            return []
        services = []
        current = None
        for line in out.splitlines():
            # 服务名行: "SERVICE_NAME: xxx" 或中文系统 "服务名称: xxx"
            m = re.match(r"^(?:SERVICE_NAME|服务名称)\s*:\s*(\S+)", line)
            if m:
                current = m.group(1)
                continue
            # 状态行: "STATE : 4 RUNNING" 或中文系统 "状态 : 4 RUNNING"
            m = re.search(r"(?:STATE|状态)\s*:\s*\d+\s+(\S+)", line)
            if m and current:
                raw_state = m.group(1).upper()
                state_map = {
                    "RUNNING": "running",
                    "STOPPED": "stopped",
                    "START_PENDING": "starting",
                    "STOP_PENDING": "stopping",
                    "PAUSED": "paused",
                }
                services.append({
                    "name": current,
                    "status": state_map.get(raw_state, raw_state.lower()),
                })
                current = None
                if len(services) >= limit:
                    break
        return services

    def _services_from_powershell(self, limit):
        """回退方案: 通过 PowerShell 获取服务状态(兼容 PowerShell 2.0)。"""
        out = _run_cmd(
            ["powershell", "-NoProfile", "-Command",
             "Get-Service | ForEach-Object { $_.Name + '|' + $_.Status }"],
            timeout=60,
        )
        services = []
        for line in out.splitlines():
            if "|" not in line:
                continue
            name, status = line.strip().split("|", 1)
            services.append({
                "name": name,
                "status": "running" if status.strip() == "Running" else "stopped",
            })
            if len(services) >= limit:
                break
        return services

    # ------------------------------------------------------------------
    # 网络共享盘(SMB)空间采集
    #
    # 华为存储等以 SMB 共享方式挂到 Windows 时, 可能是映射盘(Z:)也可能直接
    # 用 UNC(\\\\host\\share)访问。psutil.disk_partitions(all=False) 在 Windows
    # 上只采集本地盘, 网络盘根本不进 disks 列表, 所以这里单独采集。
    #
    # 做法: 用 GetDiskFreeSpaceExW 直接按 UNC 路径或盘符查询空间, 不依赖盘符在
    # (服务身份下)是否可见, 比枚举映射盘更稳。要监控哪些共享由 agent 配置
    # 的 nas_shares 列表指定(见 agent.py / config.yaml)。
    #
    # 凭据(SYSTEM 关键): Agent 常以 Windows 服务(本地系统账户)运行, 直接查 UNC
    # 共享往往因无凭据而失败。可在 nas_creds 里为共享所属主机配上
    # user/password, Agent 会用 WNetAddConnection2W 先带凭据建连再查空间。
    # ------------------------------------------------------------------
    def _ensure_nas_credentialed(self, remote_unc, creds):
        """用 WNetAddConnection2W 以指定凭据建立到 UNC 共享的连接。

        返回 True 表示连接可用(已连或本就可用), False 表示建连失败。
        仅在 Windows 且配置了凭据时生效; 非 Windows 或无需凭据时直接返回 True。
        """
        if sys.platform != "win32" or not creds:
            return True
        try:
            mpr = ctypes.windll.mpr
        except Exception:
            return True
        user, password = creds
        nr = NETRESOURCEW()
        nr.dwType = 1  # RESOURCETYPE_DISK
        nr.lpLocalName = None
        nr.lpRemoteName = ctypes.c_wchar_p(remote_unc)
        nr.lpProvider = None
        # CONNECT_TEMPORARY(0x4): 进程退出自动断开, 不留僵尸连接
        rc = mpr.WNetAddConnection2W(
            ctypes.byref(nr),
            ctypes.c_wchar_p(password) if password else None,
            ctypes.c_wchar_p(user) if user else None,
            0x4,
        )
        # 0=成功; 1219/1208/85/1223 视为"已连/已有连接/用户取消", 当作可用
        if rc in (0, 1219, 1208, 85, 1223):
            return True
        return False

    def _query_unc_space(self, path, creds=None):
        """用 GetDiskFreeSpaceExW 查询任意路径(UNC 或盘符)的磁盘空间。

        返回 {total, free, used}(字节) 或 None(失败, 如路径不可达/无权限)。
        若提供凭据且为 UNC 路径, 会先尝试带凭据建连(服务身份下访问共享常需)。
        """
        if sys.platform != "win32":
            return None
        try:
            k32 = ctypes.windll.kernel32
        except Exception:
            return None
        # 服务身份 + UNC 共享: 先带凭据建连, 否则大概率访问被拒
        if creds and (path or "").startswith("\\\\"):
            self._ensure_nas_credentialed(path, creds)
        free = ctypes.c_ulonglong(0)
        total = ctypes.c_ulonglong(0)
        avail = ctypes.c_ulonglong(0)
        # UNC 共享根 / 盘符根: 同时尝试"原样"与"补尾部反斜杠"两种写法,
        # 不同系统对根路径的容错不同, 任一成功即可。
        base = (path or "").rstrip("\\/")
        cands = []
        if path:
            cands.append(path)
        if base:
            cands.append(base + "\\")
        for p in cands:
            ok = k32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(p),
                ctypes.byref(avail),
                ctypes.byref(total),
                ctypes.byref(free),
            )
            if ok:
                tb = total.value
                fb = free.value
                return {"total": tb, "free": fb, "used": tb - fb}
        return None

    def collect_nas_disks(self):
        """采集配置的网络共享盘空间使用量, 返回列表。

        每项: {path, label, ok, total_gb, used_gb, free_gb, percent} 或
        失败项 {path, label, ok:false, error}。
        """
        shares = getattr(self, "nas_shares", None) or []
        creds_map = getattr(self, "nas_creds", None) or {}
        out = []
        for entry in shares:
            # 兼容 dict 写法({path, label})与字符串写法("路径" / "路径 | 标签")
            if isinstance(entry, dict):
                raw_path = str(entry.get("path", "")).strip()
                label = str(entry.get("label", "")).strip()
            else:
                s = str(entry).strip()
                if " | " in s:
                    raw_path, label = s.split(" | ", 1)
                    raw_path, label = raw_path.strip(), label.strip()
                else:
                    raw_path, label = s, ""
            if not raw_path:
                continue
            # 从 UNC 解析主机(\\host\share -> host), 查对应凭据
            creds = None
            if raw_path.startswith("\\\\"):
                host = raw_path[2:].split("\\", 1)[0]
                if host and host in creds_map:
                    pair = creds_map[host]
                    if isinstance(pair, dict):
                        creds = (str(pair.get("user", "")), str(pair.get("password", "")))
                    elif isinstance(pair, str) and ":" in pair:
                        u, _, p = pair.partition(":")
                        creds = (u, p)
            rec = {"path": raw_path, "label": label, "ok": False}
            info = self._query_unc_space(raw_path, creds)
            if info:
                rec["ok"] = True
                rec["total_gb"] = round(info["total"] / GB, 2)
                rec["used_gb"] = round(info["used"] / GB, 2)
                rec["free_gb"] = round(info["free"] / GB, 2)
                rec["percent"] = round(info["used"] * 100.0 / info["total"], 1) if info["total"] else 0.0
            else:
                rec["error"] = "无法访问(服务身份/凭据不正确, 或路径不可达)"
            out.append(rec)
        return out

