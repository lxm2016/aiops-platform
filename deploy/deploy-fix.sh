#!/bin/bash
# ===========================================================================
#  AIOps 运维平台 —— 修复部署脚本
#  修复主题: 长时间运行 / 无操作后页面数据全部消失、接口超时、必须重启服务
#
#  用法:
#     tar -xzf aiops-fix-*.tar.gz
#     cd aiops-fix-*
#     sudo bash deploy-fix.sh
#
#  幂等: 可以反复执行, 每次都会先备份原文件。
# ===========================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DST="${AIOPS_DST:-/opt/aiops-deploy}"
TS="$(date +%F_%H%M%S)"

echo "==========================================================="
echo "  AIOps 平台修复部署"
echo "  源目录  : $SCRIPT_DIR"
echo "  部署目录: $DST"
echo "==========================================================="

if [ "$(id -u)" != "0" ]; then
    echo "[警告] 建议用 root 执行 (需要写 /etc/systemd、重载 nginx)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[1/9] 检查部署目录..."
[ -d "$DST/backend" ] || { echo "[错误] 未找到 $DST/backend, 请先完成首次部署, 或用 AIOPS_DST= 指定路径"; exit 1; }
[ -x "$DST/backend/venv/bin/python" ] || { echo "[错误] 未找到 $DST/backend/venv, 请先完成首次后端安装"; exit 1; }

# ---------------------------------------------------------------------------
echo ""
echo "[2/9] 备份现有代码与数据库..."
mkdir -p "$DST/backup"
if [ -f "$DST/backend/aiops.db" ]; then
    cp -f "$DST/backend/aiops.db" "$DST/backup/aiops_$TS.db"
    echo "  数据库已备份 -> $DST/backup/aiops_$TS.db"
fi
if [ -d "$DST/backend/app" ]; then
    cp -rf "$DST/backend/app" "$DST/backup/app_$TS"
    echo "  后端代码已备份 -> $DST/backup/app_$TS"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[3/9] 更新后端代码..."
rm -rf "$DST/backend/app"
cp -r "$SCRIPT_DIR/backend/app" "$DST/backend/"
find "$DST/backend/app" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
echo "  后端代码更新完成"

# ---------------------------------------------------------------------------
# 只读服务器诊断依赖 (paramiko / pywinrm) —— 离线安装
# 内网服务器多半无外网, 包内已随附 cp311 manylinux 离线 wheel。
DIAG_WHEELS="$SCRIPT_DIR/backend/packages/diag-wheels"
if [ -d "$DIAG_WHEELS" ] && [ -x "$DST/backend/venv/bin/python" ]; then
    echo ""
    echo "  [3b] 安装只读诊断依赖 (paramiko / pywinrm, 离线)..."
    if "$DST/backend/venv/bin/python" -m pip install --no-index --find-links "$DIAG_WHEELS" paramiko pywinrm >/dev/null 2>&1; then
        echo "    诊断依赖安装完成 (智能诊断功能可用)"
    else
        echo "    [警告] 离线安装诊断依赖失败, 仅『智能诊断』功能受限 (其余功能不受影响)"
        echo "           可手动在 venv 中执行: $DST/backend/venv/bin/python -m pip install paramiko pywinrm"
    fi
else
    echo "  [提示] 包内未含 diag-wheels, 跳过诊断依赖安装 (智能诊断功能受限)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[4/9] 更新前端..."
FRONTEND_SRC=""
if [ -d "$SCRIPT_DIR/frontend-dist" ]; then
    FRONTEND_SRC="$SCRIPT_DIR/frontend-dist"
elif [ -d "$SCRIPT_DIR/frontend/dist" ]; then
    FRONTEND_SRC="$SCRIPT_DIR/frontend/dist"
fi
if [ -n "$FRONTEND_SRC" ]; then
    rm -rf "$DST/frontend-dist"
    cp -r "$FRONTEND_SRC" "$DST/frontend-dist"
    echo "  前端更新完成 (来源: $FRONTEND_SRC)"
else
    echo "  [提示] 包内未包含前端构建产物, 跳过前端更新"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[5/9] 安装脚本与 systemd 服务..."
mkdir -p "$DST/scripts"
cp -f "$SCRIPT_DIR/scripts/nginx-aiops.conf" "$DST/scripts/" 2>/dev/null || true
if [ -f "$SCRIPT_DIR/scripts/aiops-watchdog.sh" ]; then
    cp -f "$SCRIPT_DIR/scripts/aiops-watchdog.sh" "$DST/scripts/"
    chmod +x "$DST/scripts/aiops-watchdog.sh"
    # 去掉可能的 Windows 换行符, 否则脚本无法执行
    sed -i 's/\r$//' "$DST/scripts/aiops-watchdog.sh" 2>/dev/null || true
    echo "  看门狗脚本安装完成"
fi

if [ -f "$SCRIPT_DIR/scripts/aiops-backend.service" ] && [ -d /etc/systemd/system ]; then
    cp -f "$SCRIPT_DIR/scripts/aiops-backend.service" /etc/systemd/system/aiops-backend.service
    sed -i 's/\r$//' /etc/systemd/system/aiops-backend.service 2>/dev/null || true
    echo "  systemd 服务文件已更新 (单 worker + fd 上限提升 + 自动重启)"
fi

if [ -f "$SCRIPT_DIR/scripts/aiops-watchdog.service" ] && [ -d /etc/systemd/system ]; then
    cp -f "$SCRIPT_DIR/scripts/aiops-watchdog.service" /etc/systemd/system/
    cp -f "$SCRIPT_DIR/scripts/aiops-watchdog.timer" /etc/systemd/system/
    sed -i 's/\r$//' /etc/systemd/system/aiops-watchdog.service /etc/systemd/system/aiops-watchdog.timer 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable --now aiops-watchdog.timer >/dev/null 2>&1 && echo "  看门狗定时器已启用 (每 30 秒探活一次)"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[6/9] 更新 Nginx 配置..."
if [ -f "$SCRIPT_DIR/scripts/nginx-aiops.conf" ] && [ -d /etc/nginx/conf.d ]; then
    [ -f /etc/nginx/conf.d/nginx-aiops.conf ] && cp -f /etc/nginx/conf.d/nginx-aiops.conf "$DST/backup/nginx-aiops_$TS.conf"
    cp -f "$SCRIPT_DIR/scripts/nginx-aiops.conf" /etc/nginx/conf.d/nginx-aiops.conf
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null && echo "  Nginx 配置已更新并重载" || echo "  [提示] nginx 重载失败, 请手动执行 systemctl reload nginx"
    else
        echo "  [警告] Nginx 配置检查失败, 已回滚"
        nginx -t
        [ -f "$DST/backup/nginx-aiops_$TS.conf" ] && cp -f "$DST/backup/nginx-aiops_$TS.conf" /etc/nginx/conf.d/nginx-aiops.conf
    fi
else
    echo "  [提示] 未找到 /etc/nginx/conf.d, 跳过 Nginx 配置更新"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[7/9] 安装文档与工具..."
mkdir -p "$DST/docs" "$DST/tools"
if [ -f "$SCRIPT_DIR/docs/告警管理配置说明.md" ]; then
    cp -f "$SCRIPT_DIR/docs/告警管理配置说明.md" "$DST/docs/"
    echo "  配置说明 -> $DST/docs/告警管理配置说明.md"
fi
if [ -f "$SCRIPT_DIR/tools/probe_voice_box.py" ]; then
    cp -f "$SCRIPT_DIR/tools/probe_voice_box.py" "$DST/tools/"
    echo "  电话盒子接口探测器 -> $DST/tools/probe_voice_box.py"
    echo "    用法: $DST/backend/venv/bin/python $DST/tools/probe_voice_box.py --ip <盒子IP>"
fi
if [ -f "$SCRIPT_DIR/tools/probe_modbus.py" ]; then
    cp -f "$SCRIPT_DIR/tools/probe_modbus.py" "$DST/tools/"
    echo "  动环设备(Modbus)点位探测器 -> $DST/tools/probe_modbus.py"
    echo "    用法: python3 $DST/tools/probe_modbus.py --dev <IP>:<端口>:<从站地址>"
    echo "          透传/串口服务器请加 --rtu ; 不知道从站地址加 --scan-slave"
fi
if [ -f "$SCRIPT_DIR/tools/fix_env_points.py" ]; then
    cp -f "$SCRIPT_DIR/tools/fix_env_points.py" "$DST/tools/"
    echo "  温湿度点位校正工具 -> $DST/tools/fix_env_points.py"
    echo "    用法: $DST/backend/venv/bin/python $DST/tools/fix_env_points.py [--apply]"
    echo "          (修正历史设备 0=温度 的错误映射, 默认只预览)"
fi
if [ -f "$SCRIPT_DIR/tools/discover_env.py" ]; then
    cp -f "$SCRIPT_DIR/tools/discover_env.py" "$DST/tools/"
    echo "  动环设备批量发现工具 -> $DST/tools/discover_env.py"
    echo "    用法: python3 $DST/tools/discover_env.py --host <串口服务器IP> \\"
    echo "              --ports 5001-5008 --slaves 1-16 --rtu [--enroll]"
fi
if [ -f "$SCRIPT_DIR/tools/probe_snmp.py" ]; then
    cp -f "$SCRIPT_DIR/tools/probe_snmp.py" "$DST/tools/"
    echo "  交换机 SNMP CPU/内存 OID 诊断 -> $DST/tools/probe_snmp.py"
    echo "    用法: $DST/backend/venv/bin/python $DST/tools/probe_snmp.py --ip <交换机IP>"
fi
if [ -f "$SCRIPT_DIR/tools/sweep_device.py" ]; then
    cp -f "$SCRIPT_DIR/tools/sweep_device.py" "$DST/tools/"
    echo "  单设备寄存器全扫 -> $DST/tools/sweep_device.py"
    echo "    用法: python3 $DST/tools/sweep_device.py --host <IP> --port <端口> --slave <地址> --rtu"
fi

# ---------------------------------------------------------------------------
echo ""
echo "[8/9] 重启后端服务..."
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
echo "[9/9] 验证..."
ok=0
for i in $(seq 1 15); do
    sleep 2
    code=$(curl -s -o /tmp/_aiops_health.json -m 8 -w '%{http_code}' http://127.0.0.1:8080/api/health 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then ok=1; break; fi
done

if [ "$ok" = "1" ]; then
    echo "  后端健康检查通过:"
    cat /tmp/_aiops_health.json | head -c 800; echo ""
else
    echo "  [错误] 后端健康检查未通过 (HTTP $code)"
    echo "  请查看日志: journalctl -u aiops-backend -n 100 --no-pager"
    echo "       或:   tail -n 100 /var/log/aiops-backend.log"
    exit 1
fi

echo ""
echo "==========================================================="
echo "  部署完成!"
echo ""
echo "  本次内容:"
echo "    【稳定性修复(根因)】SNMP 采集每次新建 SnmpEngine 且 walk 生成器未关闭,"
echo "            实测每次操作泄漏 1 个 socket 句柄 + 1 个永不退出的"
echo "            asyncio 任务; 每 2 分钟一轮轮询, 数小时即耗尽文件"
echo "            描述符, 导致进程存活但全站接口超时。"
echo "            1. SNMP 层: 全局复用单 engine + aclosing 关闭生成器"
echo "               + 单次操作硬超时 + 并发限流 + 设备级总超时"
echo "            2. 调度器: 任务防重入/防堆积/单轮总超时/耗时统计"
echo "            3. 数据库: 连接池显式配置 + 异常回滚 + 复合索引"
echo "            4. 查询: 历史曲线在 SQL 层降采样, 不再全量进内存"
echo "            5. 配置: 后端单 worker + keep-alive 与 nginx 对齐"
echo "            6. 自愈: 深度健康检查 + 30 秒自动看门狗"
echo "            7. 前端: 断线自动重连 + 恢复后自动刷新数据"
echo ""
echo "    【新增: 告警管理】"
echo "            1. 可视化配置: 菜单「告警配置」, 规则/渠道全部界面操作"
echo "            2. 两级阈值: 资源 80% 提示 / 90% 严重告警(可改)"
echo "            3. 通知渠道: 钉钉 / 企业微信(微信) / 院内短信平台 /"
echo "               电话告警盒子 / 自定义 Webhook"
echo "            4. 短信与电话用通用 HTTP 网关对接, 不用改代码"
echo "            5. 连续次数去抖 + 静默期防打扰 + 指标回落自动恢复通知"
echo "            6. 级别升级(提示→严重)立即再发, 不被静默期吞掉"
echo "            7. 发送记录可查: 每次通知的成功/失败与平台返回原文"
echo ""
echo "  运维命令:"
echo "    服务状态  : systemctl status aiops-backend"
echo "    查看日志  : journalctl -u aiops-backend -f"
echo "    看门狗日志: tail -f /var/log/aiops-watchdog.log"
echo "    健康检查  : curl -s http://127.0.0.1:8080/api/health | python3 -m json.tool"
echo "    自检指标  : journalctl -u aiops-backend | grep 自检"
echo "    告警规则  : curl -s -H \"Authorization: Bearer <token>\" \\"
echo "                http://127.0.0.1:8080/api/alerts/rules"
echo ""
echo "  告警配置文档: $DST/docs/告警管理配置说明.md"
echo ""
echo "  浏览器请按 Ctrl+F5 强制刷新, 以加载新版前端。"
echo "==========================================================="
