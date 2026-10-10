#!/bin/bash
# ============================================================================
#  DGX Spark 纳管为监控设备 · 一键安装 AIOps Agent (Linux)
# ----------------------------------------------------------------------------
#  用途: 在你的 DGX Spark (Grace Blackwell GB10 / DGX OS, aarch64) 上安装本平台的
#        监控 Agent, 使其作为一台 Linux 服务器出现在平台「服务器」列表里,
#        被采集 CPU / 内存 / 磁盘 / 网络 / 进程, 并可作为 AI Agent 的只读诊断目标。
#
#  与通用 install.sh 的区别:
#    DGX Spark 是 ARM64 (NVIDIA Grace) 架构, 平台预制的便携 Python 安装包是 x86_64,
#    在本机无法运行。本脚本会自动:
#      1) 优先用系统 python3 + 系统 psutil (通过 apt 安装, 避开 pip externally-managed);
#      2) 若系统 python 不可用, 才退回便携包内置的 x86_64 python (会失败, 仅兜底)。
#    其余流程与平台官方 install.sh 完全一致, 上报协议不变。
#
#  用法 (在 DGX Spark 上执行, 需 root / sudo):
#    export AIOPS_SERVER="http://<平台IP>:8080"      # 平台访问地址(必填)
#    bash dgx-onboard.sh                             # 用默认共享令牌
#    bash dgx-onboard.sh --server http://10.1.2.3:8080 --token xxxxxx
#    bash dgx-onboard.sh --interval 10              # 采集间隔改为 10 秒
#
#  安装完成后: 到平台「服务器 → 新增」登记这台 DGX 的 IP (与下方回显一致),
#    并在「只读诊断凭据」里填入 DGX 的 SSH 账号, 这样 AI Agent 才能对它做只读排查。
# ============================================================================
set -euo pipefail

# ---------------- 参数 ----------------
SERVER=""
TOKEN="aiops-agent-shared-token"   # 必须与平台 settings.agent_token 一致 (默认即此值)
INTERVAL=5
INSTALL_DIR="/opt/aiops-agent"

while [ $# -gt 0 ]; do
  case "$1" in
    --server)  SERVER="$2"; shift 2 ;;
    --token)   TOKEN="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0" | grep -E '^\s*#' | sed 's/^ *# *//'; exit 0 ;;
    *) shift ;;
  esac
done

# 支持环境变量 AIOPS_SERVER 兜底
SERVER="${SERVER:-${AIOPS_SERVER:-}}"
if [[ -z "$SERVER" ]]; then
  echo "[错误] 必须指定平台地址: --server http://<平台IP>:8080  (或 export AIOPS_SERVER=...)" >&2
  exit 1
fi
case "$SERVER" in
  http://*|https://*) ;;
  *) SERVER="http://$SERVER" ;;
esac

echo "============================================================"
echo "  DGX Spark 纳管 · 安装 AIOps Agent"
echo "  平台地址 : $SERVER"
echo "  令牌     : ${TOKEN:0:6}****(已省略)"
echo "  采集间隔 : ${INTERVAL}s"
echo "  安装目录 : $INSTALL_DIR"
echo "============================================================"

if [ "$(id -u)" != "0" ]; then echo "[错误] 请使用 root 或 sudo 运行" >&2; exit 1; fi

# ---------------- 1) 架构检测 ----------------
ARCH="$(uname -m)"
if [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
  echo "[注意] 检测到 ARM64 (NVIDIA Grace), 将使用系统 python3 + 系统 psutil,"
  echo "        平台预制的 x86_64 便携 Python 在本机不可用, 安装时会自动跳过。"
fi

# ---------------- 2) 安装系统 python3 + psutil ----------------
echo "[1/6] 准备 Python 运行时 (系统 python3 + psutil)..."
PKG_MGR=""
command -v apt-get >/dev/null && PKG_MGR="apt"
command -v dnf  >/dev/null && PKG_MGR="dnf"
command -v yum  >/dev/null && PKG_MGR="yum"

if command -v python3 >/dev/null 2>&1; then
  echo "      系统 python3: $(python3 --version 2>&1)"
else
  echo "      未检测到 python3, 尝试安装..."
  case "$PKG_MGR" in
    apt) apt-get update -qq && apt-get install -y python3 python3-pip ;;
    dnf|yum) "$PKG_MGR" install -y python3 python3-pip ;;
    *) echo "[错误] 无法自动安装 python3, 请手动安装后重试" >&2; exit 1 ;;
  esac
fi

# 确保 psutil 可用 (apt 优先, 避免 Ubuntu pip externally-managed 报错)
if ! python3 -c "import psutil" 2>/dev/null; then
  echo "      psutil 缺失, 尝试安装..."
  case "$PKG_MGR" in
    apt) apt-get install -y python3-psutil 2>/dev/null || \
         pip3 install --break-system-packages psutil 2>/dev/null || \
         python3 -m pip install psutil 2>/dev/null || true ;;
    dnf|yum) "$PKG_MGR" install -y python3-psutil 2>/dev/null || python3 -m pip install psutil 2>/dev/null || true ;;
    *) python3 -m pip install psutil 2>/dev/null || true ;;
  esac
  if ! python3 -c "import psutil" 2>/dev/null; then
    echo "[警告] 仍未能安装 psutil, Agent 启动后会因缺依赖而退出, 请手动: pip3 install psutil"
  fi
fi
# requests / PyYAML 一般系统自带, 缺失则补
python3 -c "import requests, yaml" 2>/dev/null || \
  case "$PKG_MGR" in
    apt) apt-get install -y python3-requests python3-yaml 2>/dev/null || python3 -m pip install requests pyyaml 2>/dev/null || true ;;
    *) python3 -m pip install requests pyyaml 2>/dev/null || true ;;
  esac

# ---------------- 3) 下载并解包 Agent ----------------
echo "[2/6] 从平台下载 Agent 安装包..."
mkdir -p "$INSTALL_DIR"
TMP_PKG="/tmp/aiops-agent-linux.tar.gz"
if command -v curl >/dev/null 2>&1; then
  curl -sSfL "$SERVER/api/agents/package/linux" -o "$TMP_PKG"
elif command -v wget >/dev/null 2>&1; then
  wget -q "$SERVER/api/agents/package/linux" -O "$TMP_PKG"
else
  echo "[错误] 需要 curl 或 wget" >&2; exit 1
fi
tar -xzf "$TMP_PKG" -C "$INSTALL_DIR" --strip-components=1
rm -f "$TMP_PKG"
echo "      解包完成: $(ls "$INSTALL_DIR" | tr '\n' ' ')"

# 跳过不可用(如 x86_64)的便携 Python, 改用系统 python3
BUNDLED="$INSTALL_DIR/python/bin/python3.11"
if [[ -x "$BUNDLED" ]] && "$BUNDLED" --version >/dev/null 2>&1; then
  PY="$BUNDLED"
  echo "[3/6] 使用内置便携 Python: $PY"
else
  rm -rf "$INSTALL_DIR/python" 2>/dev/null || true
  PY="$(command -v python3)"
  echo "[3/6] 使用系统 python3: $PY"
fi

# ---------------- 4) 写配置 ----------------
echo "[4/6] 写入配置..."
cat > "$INSTALL_DIR/config.yaml" <<EOF
server:
  url: "$SERVER/api/servers/report"
  token: "$TOKEN"
agent:
  interval: $INTERVAL
  timeout: 10
EOF

# ---------------- 5) 注册 systemd 并启动 ----------------
echo "[5/6] 注册开机自启并启动..."
if command -v systemctl >/dev/null 2>&1 && [ -d /etc/systemd/system ]; then
  cat > /etc/systemd/system/aiops-agent.service <<EOF
[Unit]
Description=AIOps Monitoring Agent (DGX Spark)
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$PY $INSTALL_DIR/agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable aiops-agent
  systemctl restart aiops-agent
else
  echo "[警告] 未检测到 systemd, 使用 nohup 后台启动..."
  pkill -f "$INSTALL_DIR/agent.py" 2>/dev/null || true
  (cd "$INSTALL_DIR" && nohup "$PY" agent.py >/dev/null 2>&1 &)
fi

# ---------------- 通知平台本机 DGX 地址(供 Web「一键登记 DGX」向导自动带出) ----------------
echo "通知平台本机 DGX 地址..."
DGX_LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
DGX_NAME="$(hostname 2>/dev/null)"
NOTIFY_BODY="{\"dgx_ip\":\"${DGX_LOCAL_IP}\",\"dgx_name\":\"${DGX_NAME}\",\"dgx_user\":\"nvidia\",\"dgx_port\":22}"
if command -v curl >/dev/null 2>&1; then
  curl -sSfL -X POST "$SERVER/api/servers/dgx-onboard-notify" \
    -H "Content-Type: application/json" -H "x-agent-token: $TOKEN" \
    -d "$NOTIFY_BODY" >/dev/null 2>&1 \
    && echo "      已上报平台: $DGX_LOCAL_IP" || echo "      [警告] 上报失败(不影响 Agent 运行)"
elif command -v wget >/dev/null 2>&1; then
  wget -q --header="Content-Type: application/json" --header="x-agent-token: $TOKEN" \
    --post-data="$NOTIFY_BODY" "$SERVER/api/servers/dgx-onboard-notify" -O /dev/null 2>&1 \
    && echo "      已上报平台: $DGX_LOCAL_IP" || echo "      [警告] 上报失败(不影响 Agent 运行)"
fi

# ---------------- 6) 验证 ----------------
echo "[6/6] 验证 Agent 进程..."
sleep 3
if command -v pgrep >/dev/null 2>&1 && pgrep -f "$INSTALL_DIR/agent.py" >/dev/null 2>&1; then
  echo
  echo "============================================================"
  echo "  安装完成! Agent 已启动并设置开机自启。"
  echo "  本机管理地址: $(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "  查看状态: systemctl status aiops-agent"
  echo "  查看日志: journalctl -u aiops-agent -f"
  echo
  echo "  ★ 下一步 (在平台 Web 界面):"
  echo "    服务器 → 新增 → 填写本机 IP (与上面一致) + 名称(如 DGX-Spark-01)"
  echo "    → 『只读诊断凭据』填 DGX 的 SSH 账号, 这样 AI Agent 才能对它只读排查"
  echo "============================================================"
else
  echo
  echo "[警告] 未检测到 Agent 进程, 请手动排查:"
  echo "  cd $INSTALL_DIR && $PY agent.py --once"
  exit 1
fi
