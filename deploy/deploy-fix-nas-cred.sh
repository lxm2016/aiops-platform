#!/bin/bash
# ===========================================================================
#  AIOps 运维平台 —— 华为存储共享盘(NAS)监控 修复补丁(二)
#  针对"重装 Agent 后共享盘卡片消失 / SYSTEM 身份访问 UNC 共享失败"的修复。
#
#  本补丁相对上一版(aiops-fix-nas)的两点关键改动:
#    1. 安装脚本重装时【保留】已有 config.yaml, 不再覆盖你配好的 nas_shares
#       / nas_creds(之前重装会被清空, 导致共享盘不再采集、卡片消失)。
#    2. Agent 支持为共享主机配置访问凭据(nas_creds), 以 Windows 服务
#       (本地系统账户 SYSTEM) 身份也能带凭据访问 UNC 共享并查到空间。
#
#  用法:
#     tar -xzf aiops-fix-nas-cred-*.tar.gz
#     cd aiops-fix-nas-cred-*
#     sudo bash deploy/deploy-fix-nas-cred.sh
#
#  幂等: 可反复执行, 每次都会先备份。
# ===========================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -d "$SCRIPT_DIR/backend" ] && [ -d "$SCRIPT_DIR/../backend" ]; then
    SCRIPT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
DST="${AIOPS_DST:-/opt/aiops-deploy}"
TS="$(date +%F_%H%M%S)"

echo "==========================================================="
echo "  AIOps 平台 —— 共享盘监控修复补丁(凭据 + 配置保留)"
echo "  源目录  : $SCRIPT_DIR"
echo "  部署目录: $DST"
echo "==========================================================="

if [ "$(id -u)" != "0" ]; then
    echo "[警告] 建议用 root 执行 (需要重启后端服务)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[1/4] 检查部署目录..."
[ -d "$DST/backend" ] || { echo "[错误] 未找到 $DST/backend, 请先完成首次部署, 或用 AIOPS_DST= 指定路径"; exit 1; }

# ---------------------------------------------------------------------------
echo ""
echo "[2/4] 部署修复版 Agent 安装包(Windows / Linux)..."
mkdir -p "$DST/backend/packages"
for pkg in aiops-agent-windows.zip aiops-agent-linux.tar.gz; do
    SRC_PKG="$SCRIPT_DIR/backend/packages/$pkg"
    if [ -f "$SRC_PKG" ]; then
        if [ -f "$DST/backend/packages/$pkg" ]; then
            cp -f "$DST/backend/packages/$pkg" "$DST/backend/packages/${pkg%.*}_$TS.bak.${pkg##*.}"
            echo "  已备份原 $pkg"
        fi
        cp -f "$SRC_PKG" "$DST/backend/packages/$pkg"
        echo "  已部署 $pkg -> $DST/backend/packages/$pkg"
    else
        echo "  [提示] 包内未找到 $pkg, 跳过"
    fi
done

# ---------------------------------------------------------------------------
echo ""
echo "[3/4] 重启后端服务(使平台下发修复版 Agent 安装包)..."
if systemctl list-unit-files 2>/dev/null | grep -q '^aiops-backend.service'; then
    systemctl restart aiops-backend
    echo "  已重启 systemd 服务 aiops-backend"
else
    pkill -f "uvicorn app.main" 2>/dev/null || true
    sleep 2
    cd "$DST/backend"
    nohup "$DST/backend/venv/bin/python" -m uvicorn app.main:app \
        --host 0.0.0.0 --port 8080 \
        --workers 1 --timeout-keep-alive 75 --timeout-graceful-shutdown 30 \
        >> /var/log/aiops-backend.log 2>&1 &
    echo "  已用 nohup 启动后端"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[4/4] 验证..."
ok=0
for i in $(seq 1 15); do
    sleep 2
    code=$(curl -s -o /tmp/_aiops_health.json -m 8 -w '%{http_code}' http://127.0.0.1:8080/api/health 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then ok=1; break; fi
done

if [ "$ok" = "1" ]; then
    echo "  后端健康检查通过。"
else
    echo "  [错误] 后端健康检查未通过 (HTTP $code)"
    echo "  请查看日志: journalctl -u aiops-backend -n 100 --no-pager"
    exit 1
fi

echo ""
echo "==========================================================="
echo "  平台侧部署完成! 现在到【每台被监控的 Windows 服务器】做以下操作:"
echo ""
echo "  ① 重新下载并安装 Agent(从平台『服务器管理』页面下载修复版安装包,"
echo "     双击 install.bat 运行)。本次重装【不会覆盖】你已有的 config.yaml,"
echo "     所以之前配的 nas_shares 会保留; 若之前被清过, 请按②补上。"
echo ""
echo "  ② 编辑 Agent 安装目录下的 config.yaml, 加入共享盘与(可选)凭据:"
echo "       nas_shares:"
echo "         - \\\\192.168.1.5\\share"
echo "         - \\\\192.168.1.6\\share | 华为存储A"
echo "       nas_creds:                 # 仅当 Agent 以 SYSTEM 身份跑、且 UNC 访问失败时填"
echo "         192.168.1.5:"
echo "           user: \"storage\\admin\"   # 反斜杠写单个即可"
echo "           password: \"你的密码\""
echo ""
echo "  ③ 重启 Agent: 任务计划程序里的 AIOpsAgent 任务『运行』, 或"
echo "     在 Agent 目录执行:  taskkill /f /im pythonw.exe 然后双击 install.bat 末尾的启动。"
echo "     下次采集(约 5 秒后)即上报共享盘空间。"
echo ""
echo "  ④ 平台『服务器管理 -> 详情 -> 网络共享盘(华为存储 NAS)』卡片即可看到。"
echo "     若某共享显示『无法访问…』, 多半是凭据未配或地址/账号不对,"
echo "     检查 nas_creds 的主机 IP 与 nas_shares 里的 \\\\host 是否一致。"
echo ""
echo "  说明: 写 UNC(\\\\IP\\共享名) 比写盘符更稳 —— 服务身份下可能看不到"
echo "  用户映射的盘符, 但按 UNC 带凭据直查空间不受影响。"
echo "  浏览器请按 Ctrl+F5 强制刷新。"
echo "==========================================================="
