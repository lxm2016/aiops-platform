#!/bin/bash
# ===========================================================================
#  AIOps 运维平台 —— 华为存储共享盘(NAS)监控 修复部署脚本
#  新增监测点: Windows 服务器上华为存储映射的 SMB 共享盘空间使用率。
#
#  改动涉及两端:
#    1. 后端/前端: 接收并展示 nas_disks(无需改表结构, 存于 metrics.raw)。
#    2. Windows Agent: 新增按 UNC/盘符查询共享盘空间的采集(内置修复版安装包)。
#
#  用法:
#     tar -xzf aiops-fix-nas-*.tar.gz
#     cd aiops-fix-nas-*
#     sudo bash deploy/deploy-fix-nas.sh
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
echo "  AIOps 平台 —— 华为存储共享盘(NAS)监控 部署"
echo "  源目录  : $SCRIPT_DIR"
echo "  部署目录: $DST"
echo "==========================================================="

if [ "$(id -u)" != "0" ]; then
    echo "[警告] 建议用 root 执行 (需要重启后端服务)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[1/5] 检查部署目录..."
[ -d "$DST/backend" ] || { echo "[错误] 未找到 $DST/backend, 请先完成首次部署, 或用 AIOPS_DST= 指定路径"; exit 1; }

# ---------------------------------------------------------------------------
echo ""
echo "[2/5] 部署前端(含共享盘卡片)..."
FRONTEND_SRC=""
if [ -d "$SCRIPT_DIR/frontend-dist" ]; then
    FRONTEND_SRC="$SCRIPT_DIR/frontend-dist"
elif [ -d "$SCRIPT_DIR/frontend/dist" ]; then
    FRONTEND_SRC="$SCRIPT_DIR/frontend/dist"
fi
if [ -n "$FRONTEND_SRC" ]; then
    rm -rf "$DST/frontend-dist"
    cp -r "$FRONTEND_SRC" "$DST/frontend-dist"
    echo "  前端已更新 -> $DST/frontend-dist"
else
    echo "  [提示] 包内未包含前端构建产物, 跳过前端更新(请手动构建并放置)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[3/5] 部署修复版 Agent 安装包(Windows / Linux)..."
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
echo "[4/5] 重启后端服务(使平台下发修复版 Agent 安装包)..."
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
echo "[5/5] 验证..."
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
echo "  部署完成!"
echo ""
echo "  【在每台被监控 Windows 服务器上配置并重启 Agent】"
echo "    1. 编辑 Agent 安装目录下的 config.yaml, 在 nas_shares 下加入你的"
echo "       华为存储共享, 例如:"
echo "         nas_shares:"
echo "           - \\\\192.168.1.5\\share"
echo "           - \\\\192.168.1.6\\share | 华为存储A"
echo "           - Z:            # 已映射的盘符也行(推荐写 UNC 更稳)"
echo "    2. 重启 Agent 服务(schtasks / 服务管理器), 或重新运行 install.bat"
echo "       覆盖安装(覆盖后会带出 nas_shares 占位段, 记得再补上你的共享)。"
echo "    3. 下一次采集(默认 5 秒后)Agent 即上报共享盘空间, 平台"
echo "       「服务器管理 -> 详情 -> 网络共享盘(华为存储 NAS)」卡片可见。"
echo ""
echo "  说明: 写 UNC(\\\\IP\\共享名) 比写盘符更稳 —— Agent 若以 Windows 服务身份"
echo "  运行, 可能看不到交互用户映射的盘符, 但按 UNC 直接查空间不受影响。"
echo "  浏览器请按 Ctrl+F5 强制刷新以加载新版前端。"
echo "==========================================================="
