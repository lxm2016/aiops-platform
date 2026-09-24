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


def _run_linux(server: Server) -> Dict:
    """通过 SSH 在 Linux 上执行只读命令（同步，放到线程里跑）。"""
    try:
        import paramiko
    except ImportError:
        return {"ok": False, "error": "后端 venv 未安装 paramiko，无法 SSH 诊断。请用后端 venv 的 pip 安装（勿用系统 pip，Debian/Ubuntu 会报 externally-managed）：{venv}/bin/pip install paramiko pywinrm —— 离线 wheel 随包附于 backend/packages/diag-wheels(-py312)"}

    port = server.diag_port or 22
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=server.ip,
            port=port,
            username=server.diag_user,
            password=server.diag_password or None,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
    except Exception as e:
        hint = _classify_conn_error(str(e))
        return {"ok": False, "error": f"SSH 连接失败 ({server.ip}:{port}): {e}"
                                       + (f" —— {hint}" if hint else "")}

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

    return {"ok": True, "os_type": "linux", "host": f"{server.ip}:{port}", "sections": sections}


def _run_windows(server: Server) -> Dict:
    """通过 WinRM 在 Windows 上执行只读 PowerShell（同步，放到线程里跑）。

    认证策略：先试 NTLM（本地 administrator 开箱即用，不要求目标开 Basic/明文），
    失败再退回 plaintext(HTTP Basic，需目标开 Basic 且 AllowUnencrypted)。
    pywinrm 建会话不会真正连网，所以用一条最小探测命令来确认认证方式可用。
    """
    try:
        import winrm
    except ImportError:
        return {"ok": False, "error": "后端 venv 未安装 pywinrm，无法 WinRM 诊断。请用后端 venv 的 pip 安装（勿用系统 pip，Debian/Ubuntu 会报 externally-managed）：{venv}/bin/pip install paramiko pywinrm —— 离线 wheel 随包附于 backend/packages/diag-wheels(-py312)"}

    port = server.diag_port or 5985
    endpoint = f"http://{server.ip}:{port}/wsman"
    last_err: Optional[str] = None
    session = None
    used_transport: Optional[str] = None

    for transport in ("ntlm", "plaintext"):
        try:
            s = winrm.Session(
                endpoint,
                auth=(server.diag_user, server.diag_password or ""),
                transport=transport,
                server_cert_validation="ignore",
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
        return {"ok": False,
                "error": f"WinRM 连接失败 ({server.ip}:{port}): {last_err}"
                         + (f" —— {hint}" if hint else "") + extra}

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
def _ssh_probe(ip: str, port: int, user: str, password: str) -> Dict:
    try:
        import paramiko
    except ImportError:
        return {"ok": False, "detail": "后端 venv 未安装 paramiko，请用 venv 的 pip 安装"}
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=ip, port=port, username=user, password=password,
                       timeout=10, look_for_keys=False, allow_agent=False)
    except Exception as e:
        hint = _classify_conn_error(str(e))
        return {"ok": False, "detail": f"SSH 连接失败 ({ip}:{port}): {e}" + (f" —— {hint}" if hint else "")}
    try:
        _, stdout, _ = client.exec_command("echo ok", timeout=10)
        out = stdout.read().decode("utf-8", "replace").strip()
        if "ok" in out:
            return {"ok": True, "detail": "SSH 认证并执行成功"}
        return {"ok": False, "detail": f"SSH 已连上但执行异常: {out[:200]}"}
    finally:
        client.close()


def _winrm_probe(ip: str, port: int, user: str, password: str) -> Dict:
    try:
        import winrm
    except ImportError:
        return {"ok": False, "detail": "后端 venv 未安装 pywinrm，请用 venv 的 pip 安装"}
    endpoint = f"http://{ip}:{port}/wsman"
    last_err: Optional[str] = None
    for transport in ("ntlm", "plaintext"):
        try:
            s = winrm.Session(endpoint, auth=(user, password), transport=transport,
                              server_cert_validation="ignore")
            r = s.run_ps("Write-Output ok")
            if r.status_code == 0:
                return {"ok": True, "detail": f"WinRM 认证并执行成功 (transport={transport})"}
            last_err = (r.std_err.decode("utf-8", "replace") or f"exit={r.status_code}").strip()
        except Exception as e:
            last_err = str(e)
    hint = _classify_conn_error(last_err or "")
    return {"ok": False, "detail": f"WinRM 连接失败 ({ip}:{port}): {last_err}"
                                   + (f" —— {hint}" if hint else "")}


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
