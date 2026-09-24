---
name: server-diagnosis
description: 连到服务器（Linux SSH / Windows WinRM）做【只读】诊断，由 AI 分析 CPU/内存/磁盘/进程异常原因并给出人工处置建议，绝不自动修复。适用于"服务器 CPU/内存/磁盘告警了，帮我看看为什么""连上去排查一下"等运维场景。本平台已在 backend/app/services/diagnostic_service.py 落地。
agent_created: true
---

# 服务器只读诊断 + AI 分析（人工处置）

## 何时使用

出现以下任一情形即套用本流程：

- 用户说"某台服务器 CPU/内存/磁盘高了，帮我排查一下""连上去看看为什么告警"
- 平台产生服务器类告警，需要先采集现场数据再判断根因
- 用户想在对话里直接针对某台服务器问"这台为什么慢"

**核心铁律（必须遵守）**：本流程**只执行只读命令**，绝不执行任何会修改系统的操作——
不装包、不杀进程、不重启、不改配置、不写文件。AI 只负责"分析现象 + 给人工处置建议"，
实际修复（重启/清理/扩容）由运维人员手动确认执行。所有待执行命令必须是**硬编码白名单**，
不得接受外部拼接的命令字符串，从机制上杜绝误执行 / 命令注入。

## 协议与凭据

| 系统 | 协议 | 默认端口 | 认证 |
|------|------|----------|------|
| Linux / Unix | SSH | 22 | 用户名 + 密码（paramiko） |
| Windows | WinRM | 5985 | 用户名 + 密码（pywinrm, ntlm） |

凭据存于 `servers` 表的 `diag_user` / `diag_password` / `diag_port`（在"服务器管理"编辑页填写）。
未配置凭据时直接返回明确提示，不尝试连接。

## 只读命令白名单

**Linux（SSH）**——按分类采集，每块都是只读：
- CPU：`uptime`、`top -bn1 | head -n 12`、`nproc`、`mpstat 1 1`（未装则提示）
- 内存：`free -h`、`head -n 6 /proc/meminfo`
- 磁盘：`df -h`、`lsblk -f`、`du -sh /var/log /tmp /home`
- 进程/IO：`ps aux --sort=-%cpu | head`、`ps aux --sort=-%mem | head`、`iostat -x 1 1`

**Windows（WinRM, PowerShell）**：
- CPU：`Get-Counter '\Processor(_Total)\% Processor Time'`、`Get-CimInstance Win32_Processor`
- 内存：`Get-CimInstance Win32_OperatingSystem`（算可用 MB）、`Get-Counter '\Memory\Available MBytes'`
- 磁盘：`Get-PSDrive -PSProvider FileSystem`（Used/Free GB）
- 进程：`Get-Process | Sort CPU -First 10`、`Get-Service` 运行数

## AI 分析提示词要点

把上面四类原始输出拼成一段文本，配合如下 context 调用大模型：
"你是资深数据中心运维专家。以下是通过【只读】方式实时采集的诊断数据（未做任何修改）。请：
1. 指出 CPU/内存/磁盘/进程中是否异常并给最可能根因；
2. 给出人工处置建议（明确：本系统仅做只读检查，未自动修复，重启/杀进程/清理需人工确认）；
3. 用简洁中文分点回答。"

## 本平台落地位置（AIOps Platform）

- 后端服务：`backend/app/services/diagnostic_service.py`
  - `diagnose_server(server)`：按 os_type 走 SSH/WinRM，返回 `{ok, os_type, host, sections}`
  - `diagnose_and_analyze(server)`：诊断 + 调 `llm_service.chat_completion` 出分析
- API：`POST /api/servers/{id}/diagnose`（返回 sections + analysis）；
  `POST /api/chat` 支持 `server_id` 字段，会在回答前把该服务器只读诊断结果注入上下文
- 前端：服务器详情页"智能诊断（只读）"抽屉；AI 助手页"关联服务器"下拉（可选，对话时带入实时数据）
- 凭据编辑：服务器管理列表的"编辑"按钮 → diag_user/password/port

## 扩展与排错

- 新增只读命令：只在 `LINUX_COMMANDS` / `WINDOWS_POWERSHELL` 白名单里加，**不要**做成接受用户输入命令。
- 依赖缺失：后端未装 `paramiko` / `pywinrm` 时，对应协议返回"请执行 pip install ..."提示，不影响平台其它功能。
- WinRM 连不上：多数情况是目标未启用 WinRM 或用了 basic 而非 ntlm；可让用户在目标执行
  `winrm quickconfig` 并确认服务账户（本地管理员建议 transport=ntlm）。
- 在线大模型做分析时：后端 `llm_service` 用 `trust_env=False` 直连，若平台服务器需经代理出公网，
  把 `llm_service.py` 里 3 处 `trust_env=False` 改为 `True`。
