#!/bin/bash
# ===========================================================================
#  AIOps 运维平台 —— Windows Agent 修复部署脚本
#  修复主题: 被监控的 Windows 服务器上, 每隔 5~6 秒弹出一个 cmd 窗口并
#            瞬间消失, 且服务器上什么都输入不了(输入焦点被抢走)。
#
#  根因: Windows Agent 采集循环(默认 5 秒一轮)通过 subprocess 调用
#        "sc query" / powershell 来获取服务状态, 但子进程的控制台窗口
#        没有隐藏, 于是每轮都闪一下 cmd, 并抢走用户焦点。
#
#  本脚本作用: 把已修复(隐藏窗口)的 aiops-agent-windows.zip 安装到平台
#        服务端, 使平台下发给 Windows 服务器的 Agent 安装包即为修复版。
#
#  用法:
#     tar -xzf aiops-fix-agent-win-*.tar.gz
#     cd aiops-fix-agent-win-*
#     sudo bash deploy/deploy-fix-agent.sh
#
#  幂等: 可反复执行, 每次都会先备份原安装包。
# ===========================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 兼容脚本位于 deploy/ 子目录、从任意位置调用的情况：自动上溯到包含 backend/ 的包根目录
if [ ! -d "$SCRIPT_DIR/backend" ] && [ -d "$SCRIPT_DIR/../backend" ]; then
    SCRIPT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
DST="${AIOPS_DST:-/opt/aiops-deploy}"
TS="$(date +%F_%H%M%S)"

echo "==========================================================="
echo "  AIOps 平台 Windows Agent 修复部署"
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

SRC_PKG="$SCRIPT_DIR/backend/packages/aiops-agent-windows.zip"
[ -f "$SRC_PKG" ] || { echo "[错误] 包内未找到 $SRC_PKG, 请确认解压完整"; exit 1; }

mkdir -p "$DST/backend/packages"

# ---------------------------------------------------------------------------
echo ""
echo "[2/4] 备份并替换 Windows Agent 安装包..."
if [ -f "$DST/backend/packages/aiops-agent-windows.zip" ]; then
    cp -f "$DST/backend/packages/aiops-agent-windows.zip" "$DST/backend/packages/aiops-agent-windows_$TS.bak.zip"
    echo "  已备份原安装包 -> $DST/backend/packages/aiops-agent-windows_$TS.bak.zip"
fi
cp -f "$SRC_PKG" "$DST/backend/packages/aiops-agent-windows.zip"
echo "  已安装修复版 Agent 安装包 -> $DST/backend/packages/aiops-agent-windows.zip"

# ---------------------------------------------------------------------------
echo ""
echo "[3/4] 重启后端服务 (使平台下发修复版安装包)..."
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
echo "  安装包已更新!"
echo ""
echo "  【重要】还需在被监控的 Windows 服务器上重新安装 Agent:"
echo "    1. 到平台 Web 页面 -> 「Agent 管理」(或对应菜单) 重新下载"
echo "       Windows Agent 安装包 (现在下发的已是修复版)。"
echo "    2. 在被监控的 Windows 服务器上, 以管理员身份执行下载包内的"
echo "       install.bat 重新安装并启动; 或先 stop 旧服务再安装覆盖。"
echo "    3. 重新安装后, 采集循环(默认每 5 秒一轮)不再弹出 cmd 窗口,"
echo "       也不会再抢走服务器上的输入焦点。"
echo ""
echo "  仅平台服务端重启还不够: 闪窗发生在被监控的 Windows 服务器本地,"
echo "  必须让那台机器上运行的 Agent 也换成修复版。"
echo "==========================================================="
