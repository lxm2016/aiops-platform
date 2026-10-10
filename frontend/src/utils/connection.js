/**
 * 后端连接状态监视器
 * ------------------------------------------------------------------
 * 解决的问题: 后端如果因为异常(长时间运行资源耗尽、被阻塞等)变成
 * "进程还在但接口全挂", 用户切回页面只会看到一片空白, 且不知道是
 * 网络断了、还是服务端出了问题, 只能手动重启服务。
 *
 * 这里做三件事:
 *   1. 后台定时探活 /api/health (正常 30s 一次, 异常时 5s 一次快速重连);
 *   2. 页面从后台切回前台时立刻探活一次 —— 这正是"长时间无操作"的场景;
 *   3. 一旦探测到后端恢复, 广播 aiops:backend-restored 事件,
 *      上层据此自动重新加载当前页面的数据, 无需用户手动刷新。
 */
import { reactive } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const HEARTBEAT_INTERVAL = 30000   // 正常时 30 秒探活一次
const RETRY_INTERVAL = 5000        // 异常时 5 秒重试一次
const PROBE_TIMEOUT = 8000
// 连续探测失败达到该次数才判定"后端已断开": 避免单次瞬断/瞬超时造成反复横跳
const MAX_CONSECUTIVE_FAILURES = 2

export const connState = reactive({
  online: true,          // 后端是否可达且健康
  degraded: false,       // 可达但状态异常(如数据库故障、句柄告急)
  checking: false,
  failures: 0,
  downSince: null,       // 开始异常的本地时间戳
  lastMessage: ''        // 异常/降级原因, 展示给用户
})

// 用独立实例探活, 避免走业务拦截器造成递归重试
const probe = axios.create({ baseURL: '/api', timeout: PROBE_TIMEOUT })

let timer = null
let started = false
let everDown = false
let consecutiveFails = 0
// 每个"离线 episode"只提示一次, 避免后端抖动时每 5s 弹一次重连 Toast 刷屏
let offlineToastShown = false

async function checkNow() {
  if (connState.checking) return connState.online
  connState.checking = true
  try {
    // 用 validateStatus 让 503(degraded) 也走到这里, 便于区分"不可达"和"不健康"
    const res = await probe.get('/health', { validateStatus: () => true })
    const body = res.data || {}
    const reachable = res.status >= 200 && res.status < 500
    const unhealthy = body.status && body.status !== 'ok'

    if (!reachable) throw new Error(`服务返回 ${res.status}`)

    // 探测成功: 复位连续失败计数
    consecutiveFails = 0
    const wasOffline = !connState.online
    connState.online = true
    connState.degraded = !!unhealthy
    connState.failures = 0
    connState.lastMessage = body.warning || ''
    if (wasOffline) {
      connState.downSince = null
      offlineToastShown = false   // 恢复后重置, 下次离线再允许提示一次
      // 广播: 服务已恢复, 页面应重新拉取数据
      window.dispatchEvent(new CustomEvent('aiops:backend-restored'))
    }
    return true
  } catch (e) {
    // 业务请求失败 / 探测超时才走到这里; 单次失败不足以判定离线, 需连续阈值
    consecutiveFails += 1
    connState.failures = consecutiveFails
    if (consecutiveFails >= MAX_CONSECUTIVE_FAILURES) {
      const wasOnline = connState.online
      if (wasOnline || connState.downSince === null) {
        connState.downSince = Date.now()
      }
      connState.online = false
      connState.degraded = false
      connState.lastMessage = e?.message || '无法连接服务器'
      // 判定离线的瞬间提示一次(每个 episode 只提示一次)
      if (wasOnline && consumeDownToast()) {
        ElMessage.error('服务器响应超时，正在自动重连…')
      }
    }
    everDown = true
    return false
  } finally {
    connState.checking = false
  }
}

function schedule() {
  clearTimeout(timer)
  // 有失败记录时加快探测节奏(5s), 完全健康时才回到 30s
  const delay = (connState.online && connState.failures === 0) ? HEARTBEAT_INTERVAL : RETRY_INTERVAL
  timer = setTimeout(async () => {
    await checkNow()
    schedule()
  }, delay)
}

/**
 * 业务请求失败(超时/连接错误)时调用。
 * 关键改动: 【不再直接判定后端已死】—— 单个慢接口(如 SNMP 轮询 118 个口的
 * 交换机)超时时, 后端其实活得好好的, 直接判离线会造成"页面数据明明正常
 * 却弹重连提示"的误报。这里改为【立即发起一次健康探活来确认】:
 *   探活成功 => 后端健在, 只是该请求自身失败, 不弹横幅/不弹 Toast;
 *   探活失败 => 计入连续失败, 达到阈值(2 次)才判离线并提示一次。
 */
export function markBackendDown(reason) {
  everDown = true
  clearTimeout(timer)
  Promise.resolve(checkNow()).finally(schedule)
}

export function startConnectionMonitor() {
  if (started) return
  started = true
  checkNow()
  schedule()

  // 长时间无操作后切回页面 -> 立即确认后端是否还活着
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') checkNow()
  })
  window.addEventListener('online', checkNow)
  // 网络恢复(比如 VPN 重连)
  window.addEventListener('focus', () => {
    if (!connState.online) checkNow()
  })
}

export function hasEverBeenDown() {
  return everDown
}

/** 每个离线 episode 只返回一次 true: 用于让重连 Toast 不刷屏。 */
export function consumeDownToast() {
  if (offlineToastShown) return false
  offlineToastShown = true
  return true
}

export function downDurationText() {
  if (!connState.downSince) return ''
  const s = Math.round((Date.now() - connState.downSince) / 1000)
  if (s < 60) return `${s} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分钟`
  return `${Math.round(s / 3600)} 小时`
}
