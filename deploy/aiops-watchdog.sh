#!/bin/bash
# ===========================================================================
#  AIOps 后端自动看门狗 (watchdog)
#
#  作用: 每 30 秒探测一次后端健康检查接口。如果后端出现"进程还在、接口全挂"
#        这种卡死状态 (以前只能靠人工重启服务才能恢复), 本脚本会自动重启它,
#        用户不需要做任何操作。
#
#  判定规则:
#    - 连续 FAIL_THRESHOLD 次探测失败(连接超时 / 返回非 200) -> 自动重启
#    - 只重启一次后重新计数, 避免连环重启
#
#  为什么还需要它: 定时任务崩溃、SNMP 库异常、磁盘写满、第三方库死锁 等
#  情况无法在代码里 100% 排除, 看门狗是最后一道保险。
# ===========================================================================
set -u

HEALTH_URL="${AIOPS_HEALTH_URL:-http://127.0.0.1:8080/api/health}"
SERVICE_NAME="${AIOPS_SERVICE:-aiops-backend}"
FAIL_THRESHOLD="${AIOPS_FAIL_THRESHOLD:-3}"
BACKEND_DIR="${AIOPS_BACKEND_DIR:-/opt/aiops-deploy/backend}"
STATE_FILE="/tmp/aiops-watchdog.fails"
LOG_FILE="/var/log/aiops-watchdog.log"
LOG_MAX_BYTES=$((5 * 1024 * 1024))

log() {
    echo "$(date '+%F %T') $*" >> "$LOG_FILE" 2>/dev/null || true
}

# 日志轮转: 超过 5MB 截断, 避免自己把磁盘写满
if [ -f "$LOG_FILE" ]; then
    size=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
        tail -c $((LOG_MAX_BYTES / 2)) "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
fi

fails=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
case "$fails" in ''|*[!0-9]*) fails=0 ;; esac

# ---------------------------------------------------------------------------
# 探测: 使用固定超时, 避免脚本本身被卡住
#   http_code == 000 表示连不上/超时
# ---------------------------------------------------------------------------
http_code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000)

if [ "$http_code" = "200" ]; then
    if [ "$fails" -ne 0 ]; then
        log "后端已恢复健康 (HTTP 200), 失败计数清零"
    fi
    echo 0 > "$STATE_FILE"
    exit 0
fi

fails=$((fails + 1))
echo "$fails" > "$STATE_FILE"
log "健康检查失败 (HTTP ${http_code}), 连续第 ${fails}/${FAIL_THRESHOLD} 次"

if [ "$fails" -lt "$FAIL_THRESHOLD" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# 触发重启
# ---------------------------------------------------------------------------
log "==== 连续 ${fails} 次探活失败, 判定后端卡死, 开始自动重启 ===="

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
    systemctl restart "$SERVICE_NAME" >> "$LOG_FILE" 2>&1
    log "已执行 systemctl restart ${SERVICE_NAME}"
else
    # 非 systemd 部署: 杀掉旧进程并用 nohup 拉起
    log "未检测到 systemd 服务, 使用 pkill + nohup 方式重启"
    pkill -f "uvicorn app.main" 2>/dev/null || true
    sleep 3
    if [ -x "$BACKEND_DIR/venv/bin/python" ]; then
        cd "$BACKEND_DIR" || exit 1
        nohup "$BACKEND_DIR/venv/bin/python" -m uvicorn app.main:app \
            --host 0.0.0.0 --port 8080 \
            --workers 1 --timeout-keep-alive 75 --timeout-graceful-shutdown 30 \
            >> /var/log/aiops-backend.log 2>&1 &
        log "已用 nohup 重新拉起后端"
    else
        log "错误: 找不到 $BACKEND_DIR/venv/bin/python, 无法自动重启, 请人工处理"
    fi
fi

# 重启后等待 20 秒再确认结果
sleep 20
after=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000)
if [ "$after" = "200" ]; then
    log "自动重启成功, 后端已恢复 (HTTP 200)"
    echo 0 > "$STATE_FILE"
else
    log "自动重启后仍未恢复 (HTTP ${after}), 请人工检查 journalctl -u ${SERVICE_NAME} -n 200"
    echo $((FAIL_THRESHOLD - 1)) > "$STATE_FILE"
fi

exit 0
