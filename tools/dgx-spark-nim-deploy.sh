#!/bin/bash
# ============================================================================
#  DGX Spark · NVIDIA NIM 推理服务部署脚本 (OpenAI 兼容)
# ----------------------------------------------------------------------------
#  用途: 在你的 DGX Spark (Grace Blackwell GB10, DGX OS / Ubuntu) 上拉起一个
#        对外提供 OpenAI 兼容接口 (http://<DGX-IP>:8000/v1) 的本地大模型服务,
#        供 AIOps 平台「AI 模型配置」直接对接 (零后端改动)。
#
#  前置条件:
#    - DGX Spark 已装好 DGX OS, 且 nvidia-container-toolkit / docker 可用
#      (DGX OS 出厂默认已带, 可用 `docker info | grep Runtime` 确认有 nvidia)
#    - 已注册 NGC 账号并拿到 API Key (86 位 base64, 结尾是 ==)
#    - 能联网拉取 nvcr.io 镜像 (首次需下载, 镜像约 15~30GB, 耐心等)
#
#  用法:
#    export NGC_API_KEY="你的86位key(结尾==)"
#    ./dgx-spark-nim-deploy.sh deploy            # 单模型 (默认 llama-3.1-8b)
#    ./dgx-spark-nim-deploy.sh deploy MODEL=qwen3-32b HOST_PORT=8001
#    ./dgx-spark-nim-deploy.sh models            # 查看已拉起服务的可用模型名
#    ./dgx-spark-nim-deploy.sh status            # 查看容器与端口状态
#    ./dgx-spark-nim-deploy.sh logs              # 跟踪日志
#    ./dgx-spark-nim-deploy.sh stop              # 停止并删除容器
#    ./dgx-spark-nim-deploy.sh multi "llama-3.1-8b-instruct-dgx-spark:8001" \
#                                    "qwen3-32b-dgx-spark:8002"
#                                    # 一台机器同时跑多个模型(不同宿主机端口)
#
#  平台侧对接 (部署成功后, 纯界面操作, 见脚本末尾提示):
#    系统设置 → AI 模型 → 新增 → 提供商选「自定义 / 其他 OpenAI 兼容」
#    base_url: http://<DGX-Spark-IP>:8000/v1
#    model   : models 子命令回显的模型 id (如 meta/llama-3.1-8b-instruct)
#    API Key : 留 EMPTY (本地 NIM 默认无鉴权)
# ============================================================================
set -euo pipefail

# ---------------- 可覆盖的默认参数 (环境变量 / 命令行) ----------------
NGC_API_KEY="${NGC_API_KEY:-}"
NIM_IMAGE="${NIM_IMAGE:-nvcr.io/nim/meta/llama-3.1-8b-instruct-dgx-spark:latest}"
HOST_PORT="${HOST_PORT:-8000}"
NIM_CACHE="${NIM_CACHE:-$HOME/nim-cache}"
CONTAINER_NAME="${CONTAINER_NAME:-nim-$(echo "$NIM_IMAGE" | md5sum | cut -c1-8)}"

# DGX Spark 专用镜像清单 (供 MODEL= 简写引用)
declare -A PRESETS=(
  [llama-3.1-8b]="nvcr.io/nim/meta/llama-3.1-8b-instruct-dgx-spark:latest"
  [qwen3-32b]="nvcr.io/nim/qwen/qwen3-32b-dgx-spark:latest"
  [nemotron-nano-9b]="nvcr.io/nim/nvidia/nemotron-nano-9b-v2-dgx-spark:latest"
)

# ---------------- 工具函数 ----------------
log()  { echo -e "\033[36m[$(date +%H:%M:%S)]\033[0m $*"; }
err()  { echo -e "\033[31m[错误]\033[0m $*" >&2; }
ok()   { echo -e "\033[32m[OK]\033[0m $*"; }

need_rootless_note() {
  # DGX 上通常用普通用户 + sudo 跑 docker; 这里只做存在性检查
  if ! command -v docker >/dev/null 2>&1; then
    err "未检测到 docker, 请先在 DGX Spark 上安装 Docker + nvidia-container-toolkit"
    exit 1
  fi
}

check_runtime() {
  if ! docker info 2>/dev/null | grep -q "nvidia"; then
    err "Docker 未启用 nvidia runtime。请安装 nvidia-container-toolkit 后执行:"
    echo "    sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    exit 1
  fi
}

resolve_image() {
  local m="${1:-}"
  if [[ -z "$m" ]]; then echo "$NIM_IMAGE"; return; fi
  if [[ -v PRESETS["$m"] ]]; then echo "${PRESETS[$m]}"; return; fi
  # 否则视为完整镜像引用
  echo "$m"
}

ngc_login() {
  if [[ -z "$NGC_API_KEY" ]]; then
    err "未设置 NGC_API_KEY 环境变量。请先: export NGC_API_KEY=\"你的86位key\""
    exit 1
  fi
  log "正在登录 nvcr.io (NGC) ..."
  # 用户名固定为 \$oauthtoken, 密码即 API Key
  echo "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
  ok "NGC 登录成功"
}

# 拉起单个 NIM 容器
deploy_one() {
  local img="$1" host_port="$2"
  local cname="nim-$(echo "$img" | md5sum | cut -c1-8)"

  ngc_login
  check_runtime

  log "拉取镜像 (首次较慢, 约 15~30GB): $img"
  docker pull "$img"

  # 若同名容器已存在, 先清掉
  if docker ps -a --format '{{.Names}}' | grep -qx "$cname"; then
    log "发现同名容器 $cname, 先停止并删除"
    docker rm -f "$cname" >/dev/null 2>&1 || true
  fi

  log "启动 NIM 容器: $cname  →  宿主机端口 $host_port"
  docker run -d --name "$cname" \
    --gpus all \
    --shm-size=16g \
    -e NGC_API_KEY="$NGC_API_KEY" \
    -e NIM_SERVER_PORT=8000 \
    -v "$NIM_CACHE":/opt/nim/.cache \
    -p "${host_port}:8000" \
    --restart unless-stopped \
    "$img"

  ok "容器已启动, 正在等待模型加载 (首次加载需 1~3 分钟)..."
  # 轮询 /v1/models 直到就绪
  local waited=0
  until curl -s -o /dev/null -w '%{http_code}' "http://localhost:${host_port}/v1/models" 2>/dev/null | grep -q 200; do
    sleep 10; waited=$((waited+10))
    if [[ $waited -ge 600 ]]; then
      err "等待 10 分钟仍未就绪, 请用 './dgx-spark-nim-deploy.sh logs' 查看原因"
      exit 1
    fi
    printf "."
  done
  echo
  ok "NIM 就绪! OpenAI 兼容接口: http://localhost:${host_port}/v1"
  echo "  可用模型 (复制到平台『模型名称』):"
  curl -s "http://localhost:${host_port}/v1/models" | python3 -c "import sys,json; [print('   -',m['id']) for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null || \
    curl -s "http://localhost:${host_port}/v1/models"
  echo
  echo "  快速验证对话:"
  echo "    curl -s http://localhost:${host_port}/v1/chat/completions -H 'Content-Type: application/json' \\"
  echo "      -d '{\"model\":\"<上面任一模型id>\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}]}'"
}

cmd_deploy() {
  need_rootless_note
  local model="${MODEL:-}"
  local img; img="$(resolve_image "$model")"
  [[ "$model" != "" ]] && NIM_IMAGE="$img" && HOST_PORT="${HOST_PORT:-8000}"
  deploy_one "$img" "${HOST_PORT}"
  print_platform_hint "$HOST_PORT"
}

cmd_multi() {
  need_rootless_note
  if [[ $# -lt 1 ]]; then err "multi 需要至少一个 '镜像:宿主机端口' 参数"; exit 1; fi
  for pair in "$@"; do
    local img="${pair%%:*}" port="${pair##*:}"
    # 允许简写: 把 preset 名展开
    img="$(resolve_image "$img")"
    deploy_one "$img" "$port"
  done
  print_platform_hint "${HOST_PORT}"
}

cmd_models() {
  local port="${HOST_PORT}"
  log "查询 http://localhost:${port}/v1/models"
  curl -s "http://localhost:${port}/v1/models" | python3 -m json.tool 2>/dev/null || \
    curl -s "http://localhost:${port}/v1/models"
}

cmd_status() {
  docker ps --filter "name=nim-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
}

cmd_logs() {
  local cname="nim-$(echo "$NIM_IMAGE" | md5sum | cut -c1-8)"
  docker logs -f "$cname" 2>&1 || err "容器 $cname 不存在"
}

cmd_stop() {
  docker ps -a --filter "name=nim-" --format '{{.Names}}' | while read -r c; do
    log "停止并删除 $c"; docker rm -f "$c" >/dev/null 2>&1 || true
  done
  ok "已停止所有 nim-* 容器"
}

print_platform_hint() {
  local port="$1"
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "=================================================================="
  echo "  平台对接 (纯界面, 无需改后端):"
  echo "    系统设置 → AI 模型 → 新增配置"
  echo "    提供商  : 自定义 / 其他 OpenAI 兼容"
  echo "    base_url: http://${ip:-<DGX-SPARK-IP>}:${port}/v1"
  echo "    model   : 用上面的 'models' 子命令查到的模型 id"
  echo "    API Key : 留 EMPTY (DGX 本地 NIM 默认免鉴权)"
  echo "  注意: DGX 与平台若跨网段, 请在 DGX 防火墙放行 ${port}/tcp,"
  echo "        或仅允许平台服务器 IP 访问 (不要对公网暴露)。"
  echo "=================================================================="
}

# ---------------- 入口 ----------------
case "${1:-deploy}" in
  deploy) shift; cmd_deploy "$@" ;;
  multi)  shift; cmd_multi "$@" ;;
  models) shift; cmd_models "$@" ;;
  status) shift; cmd_status "$@" ;;
  logs)   shift; cmd_logs "$@" ;;
  stop)   shift; cmd_stop "$@" ;;
  -h|--help|help)
    sed -n '2,40p' "$0" | grep -E '^\s*#|^\s*#' >/dev/null
    grep -E '^\s*#\s' "$0" | sed 's/^ *# *//' | head -40
    ;;
  *) err "未知子命令: $1"; exit 1 ;;
esac
