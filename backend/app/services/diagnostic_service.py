"""只读服务器诊断服务：Linux(SSH) / Windows(WinRM)。

=======================================================================
设计原则（关键，务必遵守）
=======================================================================
1. 本服务【只执行只读命令】，绝不执行任何会修改系统的操作（不装包、不杀进程、
   不重启、不改配置、不写文件）。AI 只负责"分析现象 + 给出人工处置建议"，
   实际修复由运维人员手动确认执行。
2. 所有待执行命令都是代码里【硬编码的白名单】，不接受任何外部拼接的命令字符串，
   从机制上杜绝误执行 / 命令注入。
3. 依赖 paramiko(Linux SSH) 与 pywinrm(Windows WinRM)。缺失时给出明确安装提示，
   不影响平台其它功能启动。
=======================================================================
"""
import asyncio
import logging
import re
import time
from typing import Dict, List, Optional

from sqlalchemy import select

from app.models import Server
from app.services import llm_service

logger = logging.getLogger(__name__)


# ---------- 只读命令白名单（硬编码，不接受外部拼接） ----------
LINUX_COMMANDS: Dict[str, List[str]] = {
    "cpu": [
        "echo '== uptime =='; uptime",
        "echo '== top(前12行) =='; top -bn1 | head -n 12",
        "echo '== CPU核数 =='; nproc",
        "echo '== mpstat(若装了sysstat) =='; mpstat 1 1 2>/dev/null || echo 'mpstat 未安装(sysstat)'",
    ],
    "memory": [
        "echo '== free =='; free -h",
        "echo '== meminfo(前6行) =='; head -n 6 /proc/meminfo",
    ],
    "disk": [
        "echo '== df =='; df -h",
        "echo '== lsblk =='; lsblk -f 2>/dev/null | head -n 20 || echo 'lsblk 不可用'",
        "echo '== 大目录占用(若有权限) =='; du -sh /var/log /tmp /home 2>/dev/null",
    ],
    "process": [
        "echo '== CPU占用Top8 =='; ps aux --sort=-%cpu | head -n 8",
        "echo '== 内存占用Top8 =='; ps aux --sort=-%mem | head -n 8",
        "echo '== iostat(若装了sysstat) =='; iostat -x 1 1 2>/dev/null || echo 'iostat 未安装(sysstat)'",
    ],
}

WINDOWS_POWERSHELL: Dict[str, List[str]] = {
    "cpu": [
        "Get-Counter '\\Processor(_Total)\\% Processor Time' | Select -ExpandProperty CounterSamples | Select -ExpandProperty CookedValue",
        "Get-CimInstance Win32_Processor | Select Name,LoadPercentage,NumberOfCores | Format-List",
    ],
    "memory": [
        "Get-CimInstance Win32_OperatingSystem | ForEach-Object { '总内存MB=' + [math]::Round($_.TotalVisibleMemorySize/1KB,0) + ' 可用MB=' + [math]::Round($_.FreePhysicalMemory/1KB,0) }",
        "Get-Counter '\\Memory\\Available MBytes' | Select -ExpandProperty CounterSamples | Select -ExpandProperty CookedValue",
    ],
    "disk": [
        "Get-PSDrive -PSProvider FileSystem | Select Name,@{n='UsedGB';e={[math]::Round($_.Used/1GB,2)}},@{n='FreeGB';e={[math]::Round($_.Free/1GB,2)}} | Format-Table -AutoSize",
    ],
    "process": [
        "Get-Process | Sort-Object CPU -Descending | Select -First 10 Name,CPU,Id,WorkingSet | Format-Table -AutoSize",
        "Get-Service | Where-Object { $_.Status -eq 'Running' } | Measure-Object | Select -ExpandProperty Count | ForEach-Object { '运行中服务数=' + $_ }",
    ],
}

SECTION_LABELS = {"cpu": "CPU", "memory": "内存", "disk": "磁盘", "process": "进程/IO"}


def _classify_conn_error(msg: str) -> str:
    """把底层报错翻译成运维能直接行动的提示。"""
    low = (msg or "").lower()
    if "401" in low or "credentials" in low or "auth" in low or "login" in low or "access denied" in low:
        return "认证被拒绝：用户名/密码不对，或目标 WinRM 未开启对应认证方式（见提示）"
    if "timed out" in low or "timeout" in low or "unreachable" in low or "refused" in low or "no route" in low:
        return "网络不通或端口未放行：检查 IP/端口、目标防火墙是否放行 22/5985"
    if "winrm" in low and ("encrypted" in low or "basic" in low):
        return "目标 WinRM 认证方式未开启（需 Basic/AllowUnencrypted，或改用 NTLM）"
    return ""


def _connect_linux(server: Server):
    """建立 SSH 连接(同步)。成功返回 client；失败抛 RuntimeError(带人话提示)。

    调用方负责 client.close()。被 _run_linux / _ssh_probe / _process_list_linux 复用。
    """
    try:
        import paramiko
    except ImportError:
        raise RuntimeError(
            "后端 venv 未安装 paramiko，无法 SSH 诊断。请用后端 venv 的 pip 安装"
            "（勿用系统 pip，Debian/Ubuntu 会报 externally-managed）："
            "backend/venv/bin/pip install paramiko pywinrm —— 离线 wheel 随包附于 "
            "backend/packages/diag-wheels(-py312)"
        )
    port = server.diag_port or 22
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=server.ip, port=port, username=server.diag_user,
            password=server.diag_password or None, timeout=15,
            look_for_keys=False, allow_agent=False,
        )
        return client
    except Exception as e:
        hint = _classify_conn_error(str(e))
        raise RuntimeError(
            f"SSH 连接失败 ({server.ip}:{port}): {e}" + (f" —— {hint}" if hint else "")
        )


def _connect_windows(server: Server):
    """建立 WinRM 会话(同步, NTLM 优先)。成功返回 (session, transport)；

    失败抛 RuntimeError(带人话提示)。被 _run_windows / _winrm_probe / _process_list_windows 复用。
    """
    try:
        import winrm
    except ImportError:
        raise RuntimeError(
            "后端 venv 未安装 pywinrm，无法 WinRM 诊断。请用后端 venv 的 pip 安装"
            "（勿用系统 pip，Debian/Ubuntu 会报 externally-managed）："
            "backend/venv/bin/pip install paramiko pywinrm —— 离线 wheel 随包附于 "
            "backend/packages/diag-wheels(-py312)"
        )
    port = server.diag_port or 5985
    endpoint = f"http://{server.ip}:{port}/wsman"
    last_err: Optional[str] = None
    session = None
    used_transport: Optional[str] = None
    for transport in ("ntlm", "plaintext"):
        try:
            s = winrm.Session(
                endpoint, auth=(server.diag_user, server.diag_password or ""),
                transport=transport, server_cert_validation="ignore",
            )
            probe = s.run_ps("Write-Output ok")
            if probe.status_code == 0:
                session, used_transport = s, transport
                break
            last_err = (probe.std_err.decode("utf-8", "replace") or f"exit={probe.status_code}").strip()
        except Exception as e:      # ntlm 缺 requests-ntlm 等情况 → 换下一种方式
            last_err = str(e)
            session = None
    if session is None:
        hint = _classify_conn_error(last_err or "")
        extra = ""
        if "认证" in (hint or ""):
            extra = ("。目标机排查建议: ① 确认账号密码; ② 目标机执行 winrm quickconfig; "
                     "③ 若仍不行, 在目标机开启 Basic: winrm set winrm/config/service/auth @{Basic=\"true\"} "
                     "与 winrm set winrm/config/service @{AllowUnencrypted=\"true\"} (HTTP/5985 时)")
        raise RuntimeError(
            f"WinRM 连接失败 ({server.ip}:{port}): {last_err}"
            + (f" —— {hint}" if hint else "") + extra
        )
    return session, used_transport


def _run_linux(server: Server) -> Dict:
    """通过 SSH 在 Linux 上执行只读命令（同步，放到线程里跑）。"""
    try:
        client = _connect_linux(server)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    sections: Dict[str, str] = {}
    try:
        for sec, cmds in LINUX_COMMANDS.items():
            blocks = []
            for cmd in cmds:
                try:
                    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
                    out = stdout.read().decode("utf-8", "replace")
                    err = stderr.read().decode("utf-8", "replace")
                    blocks.append(f"$ {cmd}\n{(out + err).strip()}")
                except Exception as e:
                    blocks.append(f"$ {cmd}\n[命令执行失败: {e}]")
            sections[sec] = "\n\n".join(blocks)
    finally:
        client.close()

    return {"ok": True, "os_type": "linux", "host": f"{server.ip}:{server.diag_port or 22}", "sections": sections}


def _run_windows(server: Server) -> Dict:
    """通过 WinRM 在 Windows 上执行只读 PowerShell（同步，放到线程里跑）。

    认证策略：先试 NTLM（本地 administrator 开箱即用，不要求目标开 Basic/明文），
    失败再退回 plaintext(HTTP Basic，需目标开 Basic 且 AllowUnencrypted)。
    pywinrm 建会话不会真正连网，所以用一条最小探测命令来确认认证方式可用。
    """
    try:
        session, used_transport = _connect_windows(server)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    port = server.diag_port or 5985
    sections: Dict[str, str] = {}
    for sec, scripts in WINDOWS_POWERSHELL.items():
        blocks = []
        for script in scripts:
            try:
                r = session.run_ps(script)
                out = r.std_out.decode("utf-8", "replace")
                err = r.std_err.decode("utf-8", "replace")
                if r.status_code not in (0, None):
                    err = (err + f"\n[exit={r.status_code}]").strip()
                blocks.append(f"PS> {script}\n{(out + err).strip()}")
            except Exception as e:
                blocks.append(f"PS> {script}\n[执行失败: {e}]")
        sections[sec] = "\n\n".join(blocks)

    return {"ok": True, "os_type": "windows", "host": f"{server.ip}:{port}",
            "transport": used_transport, "sections": sections}


# ---------- 测试连接（一条最小命令, 快速验证 IP/端口/账号密码） ----------
def _shim(ip, port, user, password, os_type):
    """把散落的 ip/port/user/password 包成最小对象, 复用 _connect_* 连接逻辑。"""

    class _S:
        pass

    s = _S()
    s.ip = ip
    s.diag_port = port
    s.diag_user = user
    s.diag_password = password
    s.os_type = os_type
    return s


def _ssh_probe(ip: str, port: int, user: str, password: str) -> Dict:
    try:
        import paramiko
    except ImportError:
        return {"ok": False, "detail": "后端 venv 未安装 paramiko，请用 venv 的 pip 安装"}
    try:
        client = _connect_linux(_shim(ip, port, user, password, "linux"))
    except RuntimeError as e:
        return {"ok": False, "detail": str(e)}
    client.close()
    return {"ok": True, "detail": "SSH 认证并执行成功"}


def _winrm_probe(ip: str, port: int, user: str, password: str) -> Dict:
    try:
        import winrm
    except ImportError:
        return {"ok": False, "detail": "后端 venv 未安装 pywinrm，请用 venv 的 pip 安装"}
    try:
        session, transport = _connect_windows(_shim(ip, port, user, password, "windows"))
    except RuntimeError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": f"WinRM 认证并执行成功 (transport={transport})"}


async def test_connection(
    server: Server,
    diag_user: Optional[str] = None,
    diag_password: Optional[str] = None,
    diag_port: Optional[int] = None,
) -> Dict:
    """快速测试只读诊断凭据能否连上（只跑一条最小命令，不做任何修改）。

    diag_* 传参可覆盖已存值 —— 前端"测试连接"按钮可用表单里刚输入、尚未保存的密码。
    """
    user = diag_user if diag_user not in (None, "") else server.diag_user
    password = diag_password if diag_password not in (None, "") else server.diag_password
    port = diag_port or server.diag_port
    if not user:
        return {"ok": False, "detail": "未配置登录用户，请先在『编辑服务器 → 只读诊断凭据』填写"}
    if not password:
        return {"ok": False, "detail": "登录密码为空（表单留空且库里也没有保存过）。请输入密码后重试"}
    started = time.time()
    if server.os_type == "windows":
        res = await asyncio.to_thread(_winrm_probe, server.ip, port or 5985, user, password)
    else:
        res = await asyncio.to_thread(_ssh_probe, server.ip, port or 22, user, password)
    res["latency_ms"] = int((time.time() - started) * 1000)
    return res


async def diagnose_server(server: Server) -> Dict:
    """对单台服务器执行只读诊断，返回各分类原始输出。"""
    if not server.diag_user:
        return {
            "ok": False,
            "error": "未配置诊断凭据（用户名/密码）。请先在服务器编辑中填写 SSH/WinRM 账号。",
        }
    if not server.diag_password:
        return {
            "ok": False,
            "error": "登录密码为空（SSH/WinRM 密码认证必须有密码）。"
                     "请在『编辑服务器 → 只读诊断凭据』输入密码保存，可先用『测试连接』验证。",
        }
    if server.os_type == "windows":
        return await asyncio.to_thread(_run_windows, server)
    # 默认 Linux（含其它 Unix-like）
    return await asyncio.to_thread(_run_linux, server)


# ---------- 进程列表（点击"进程数"卡片下钻, 只读） ----------
# 与 diagnose 的 process 段不同: 这里要把实时进程解析成结构化表格(名称/PID/CPU/内存),
# 而不是大段原始文本。命令同样是硬编码白名单, 不接受任何外部拼接。
LINUX_PROC_LIST_CMD = (
    "ps -eo pid=,comm=,%cpu=,%mem=,rss=,user= --sort=-%cpu | head -n 20"
)
WINDOWS_PROC_LIST_CMD = (
    "Get-Process | Sort-Object CPU -Descending | "
    "Select-Object -First 20 Id,ProcessName,CPU,WorkingSet | "
    "ConvertTo-Csv -NoTypeInformation"
)


def _is_num(s) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _process_list_linux(server: Server) -> Dict:
    try:
        client = _connect_linux(server)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    port = server.diag_port or 22
    try:
        stdin, stdout, stderr = client.exec_command(LINUX_PROC_LIST_CMD, timeout=30)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if err.strip():
            return {"ok": False, "error": f"执行失败: {err.strip()}"}
        procs = []
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) < 6:
                continue
            pid, name, cpu, mem, rss = parts[0], parts[1], parts[2], parts[3], parts[4]
            procs.append({
                "pid": int(pid) if pid.isdigit() else pid,
                "name": name,
                "cpu": float(cpu) if _is_num(cpu) else None,
                "mem": float(mem) if _is_num(mem) else None,
                "rss_mb": round(int(rss) / 1024, 1) if rss.isdigit() else None,
                "user": " ".join(parts[5:]),
            })
        return {"ok": True, "os_type": "linux", "host": f"{server.ip}:{port}",
                "count": len(procs), "processes": procs}
    except Exception as e:
        return {"ok": False, "error": f"执行失败: {e}"}
    finally:
        client.close()


def _process_list_windows(server: Server) -> Dict:
    try:
        session, used_transport = _connect_windows(server)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    port = server.diag_port or 5985
    try:
        r = session.run_ps(WINDOWS_PROC_LIST_CMD)
        out = r.std_out.decode("utf-8", "replace")
        err = r.std_err.decode("utf-8", "replace")
        if r.status_code not in (0, None):
            return {"ok": False, "error": (err or f"exit={r.status_code}").strip()}
        import csv
        import io
        procs = []
        for row in csv.DictReader(io.StringIO(out)):
            pid = (row.get("Id") or "").strip()
            ws = (row.get("WorkingSet") or "").strip()
            cpu = (row.get("CPU") or "").strip()
            procs.append({
                "pid": int(pid) if pid.isdigit() else pid,
                "name": (row.get("ProcessName") or "").strip(),
                # Windows Get-Process 的 CPU 是"累计处理器时间(秒)", 不是实时占比, 单独标注
                "cpu_seconds": float(cpu) if _is_num(cpu) else None,
                "mem": None,
                "rss_mb": round(int(ws) / 1048576, 1) if ws.isdigit() else None,
                "user": "",
            })
        return {"ok": True, "os_type": "windows", "host": f"{server.ip}:{port}",
                "transport": used_transport, "count": len(procs), "processes": procs}
    except Exception as e:
        return {"ok": False, "error": f"执行失败: {e}"}


async def process_list(server: Server) -> Dict:
    """点击"进程数"卡片下钻: 只读列出资源占用最高的 TOP 进程(名称/PID/CPU/内存)。

    与 diagnose 不同, 这里不做 AI 分析, 只是把实时进程快照以结构化方式返回给前端表格。
    同样全程只读, 不执行任何修改操作。
    """
    if not server.diag_user:
        return {"ok": False, "error": "未配置诊断凭据（用户名/密码）。请先在服务器编辑中填写 SSH/WinRM 账号。"}
    if not server.diag_password:
        return {"ok": False, "error": "登录密码为空（SSH/WinRM 密码认证必须有密码）。请先在『编辑服务器 → 只读诊断凭据』输入密码保存，可先用『测试连接』验证。"}
    if server.os_type == "windows":
        return await asyncio.to_thread(_process_list_windows, server)
    return await asyncio.to_thread(_process_list_linux, server)


def _format_sections(sections: Dict[str, str]) -> str:
    """把各分类原始输出拼成一段文本，供注入给大模型分析。"""
    parts = []
    for key, label in SECTION_LABELS.items():
        content = (sections or {}).get(key, "")
        if content:
            parts.append(f"【{label}】\n{content}")
    return "\n\n".join(parts)


async def diagnose_and_analyze(server: Server) -> Dict:
    """只读诊断 + 调用大模型分析原因与人工处置建议。"""
    diag = await diagnose_server(server)
    if not diag.get("ok"):
        return {**diag, "analysis": None, "raw_text": None}

    raw_text = _format_sections(diag.get("sections", {}))
    context = (
        "你是一名资深数据中心运维专家。下面是通过【只读方式】连接到一台服务器"
        "实时采集的诊断数据（系统未做任何修改）。请：\n"
        "1. 指出 CPU / 内存 / 磁盘 / 进程 中是否存在异常，并给出最可能的根因；\n"
        "2. 给出【人工处置建议】（明确说明：本系统仅执行了只读检查，未自动做任何修复，"
        "重启 / 杀进程 / 清理磁盘 等操作需运维人员手动确认后执行）；\n"
        "3. 用简洁的中文分点回答。"
    )
    try:
        analysis = await llm_service.chat_completion(
            f"请分析以下服务器({server.name}, {server.ip})的只读诊断数据：\n\n{raw_text}",
            history=[],
            context=context,
        )
    except Exception as e:
        analysis = f"AI 分析失败：{e}"
    # 剥掉推理型模型(DeepSeek-R1 等)泄漏的 <think>…</think>，只给用户看结论
    analysis = re.sub(r"<think>.*?</think>", "", analysis or "", flags=re.S).strip()
    if not analysis:
        analysis = "（模型未返回有效分析内容，请检查模型配置）"
    return {**diag, "analysis": analysis, "raw_text": raw_text}
