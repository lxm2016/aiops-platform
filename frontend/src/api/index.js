import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { markBackendDown, connState } from '@/utils/connection'

const http = axios.create({
  baseURL: '/api',
  // 20秒超时: 后端正常时所有接口都在百毫秒级返回, 超过20秒基本可判定异常,
  // 早点失败早点重试, 比干等60秒让用户以为系统死了要好。
  timeout: 20000,
})

// ---------------------------------------------------------------------------
// 失败重试策略
//   读操作(GET)允许重试 3 次, 因为重复读取是无副作用的;
//   写操作(POST/PUT/DELETE)只重试 1 次, 且仅在"请求明显没送达"的错误上重试,
//   避免超时重试造成重复提交(比如重复添加设备)。
//   退避间隔: 0.5s -> 1.5s -> 3s, 给后端留出恢复时间。
// ---------------------------------------------------------------------------
const READ_RETRY = 3
const WRITE_RETRY = 1
const BACKOFF = [500, 1500, 3000]

function maxRetryFor(config) {
  const method = (config.method || 'get').toLowerCase()
  return method === 'get' ? READ_RETRY : WRITE_RETRY
}

function isConnectionError(err) {
  if (err.code === 'ERR_CANCELED') return false   // 主动取消, 不要重试
  // 明确"请求没有被处理"的几类错误, 重试是安全的
  return (
    err.code === 'ECONNABORTED' ||   // 超时
    err.code === 'ERR_NETWORK' ||    // 网络错误/连接被拒
    !err.response                    // 无响应体: 连接层失败
  )
}

function isRetryable(err) {
  const status = err.response?.status
  // 网关类错误说明后端暂时不可用, 可以重试
  if ([502, 503, 504].includes(status)) return true
  // 5xx 服务端错误: 只对 GET 重试(写操作重试可能产生重复数据)
  if (status >= 500 && (err.config?.method || 'get').toLowerCase() === 'get') return true
  return isConnectionError(err)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// 请求拦截器：附加 Token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  config._retryCount = config._retryCount || 0
  return config
})

// 响应拦截器：统一错误处理 + 自动重试 + 连接状态联动
http.interceptors.response.use(
  (res) => res.data,
  async (err) => {
    const config = err.config || {}
    const status = err.response?.status
    const detail = err.response?.data?.detail

    // 401: 登录过期, 直接回登录页 (不重试)
    if (status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('username')
      if (router.currentRoute.value.path !== '/login') {
        router.push('/login')
        ElMessage.error('登录已过期，请重新登录')
      }
      return Promise.reject(err)
    }

    // 自动重试
    const limit = maxRetryFor(config)
    if (isRetryable(err) && (config._retryCount || 0) < limit) {
      config._retryCount = (config._retryCount || 0) + 1
      const delay = BACKOFF[Math.min(config._retryCount - 1, BACKOFF.length - 1)]
      console.warn(
        `[axios] 请求失败(${err.code || status}), 第 ${config._retryCount}/${limit} 次重试: ${config.url || ''}`
      )
      await sleep(delay)
      return http(config)
    }

    // 重试耗尽: 判定后端不可用, 交给连接监视器去持续探测/自动恢复
    if (isRetryable(err)) {
      markBackendDown(err.code === 'ECONNABORTED' ? '请求超时' : '无法连接服务器')
      // 连接类错误由顶部横幅统一提示, 避免每个接口都弹一次
      if (!connState.online && !config.silent) {
        ElMessage.error('服务器响应超时，正在自动重连…')
      }
    } else if (!config.silent) {
      ElMessage.error(typeof detail === 'string' ? detail : `请求失败: ${err.message}`)
    }
    return Promise.reject(err)
  }
)

// ---------- 系统健康 ----------
export const healthApi = {
  check: () => http.get('/health', { silent: true, timeout: 8000 })
}

// ---------- 认证 ----------
export const authApi = {
  login: (data) => http.post('/auth/login', data),
  me: () => http.get('/auth/me', { silent: true }),
  changePassword: (data) => http.post('/auth/change-password', data)
}

// ---------- 系统设置 (AI模型 / 多提供商) ----------
export const settingsApi = {
  getLlm: () => http.get('/settings/llm'),
  saveLlm: (data) => http.put('/settings/llm', data),
  testLlm: (data) => http.post('/settings/llm/test', data, { timeout: 60000 }),
  // 提供商注册表 (下拉选项, 不含密钥)
  providers: () => http.get('/settings/llm/providers'),
  // 拉取某 endpoint 可用模型列表
  models: (data) => http.post('/settings/llm/models', data, { timeout: 30000 })
}

// ---------- 服务器 ----------
export const serverApi = {
  list: (params) => http.get('/servers', { params }),
  create: (data) => http.post('/servers', data),
  remove: (id) => http.delete(`/servers/${id}`),
  metrics: (id, params) => http.get(`/servers/${id}/metrics`, { params, timeout: 60000 }),
  detail: (id) => http.get(`/servers/${id}/detail`),
  summary: () => http.get('/servers/stats/summary')
}

// ---------- VMware ----------
export const vmwareApi = {
  listHosts: () => http.get('/vmware/hosts'),
  addHost: (data) => http.post('/vmware/hosts', data),
  deleteHost: (id) => http.delete(`/vmware/hosts/${id}`),
  syncHost: (id) => http.post(`/vmware/hosts/${id}/sync`, null, { timeout: 180000 }),
  listVms: (params) => http.get('/vmware/vms', { params }),
  listHostVms: (hostId) => http.get(`/vmware/hosts/${hostId}/vms`)
}

// ---------- 机柜管理 ----------
export const rackApi = {
  list: () => http.get('/racks'),
  create: (data) => http.post('/racks', data),
  update: (id, data) => http.put(`/racks/${id}`, data),
  remove: (id) => http.delete(`/racks/${id}`),
  devices: (rackId) => http.get(`/racks/${rackId}/devices`),
  addDevice: (rackId, data) => http.post(`/racks/${rackId}/devices`, data),
  updateDevice: (deviceId, data) => http.put(`/racks/devices/${deviceId}`, data),
  removeDevice: (deviceId) => http.delete(`/racks/devices/${deviceId}`),
  importDevices: (items) => http.post('/racks/devices/import', { items }),
  overview: () => http.get('/racks/overview')
}

// ---------- 网络设备 ----------
export const networkApi = {
  list: () => http.get('/network'),
  create: (data) => http.post('/network', data),
  update: (id, data) => http.put(`/network/${id}`, data),
  remove: (id) => http.delete(`/network/${id}`),
  // 单台轮询可能要等待 SNMP 超时, 给足时间
  poll: (id) => http.post(`/network/${id}/poll`, null, { timeout: 120000 }),
  ports: (id) => http.get(`/network/${id}/ports`),
  testPort: (id, portIndex) =>
    http.post(`/network/${id}/ports/${portIndex}/test`, null, { timeout: 60000 }),
  updateRemark: (id, portIndex, remark) =>
    http.put(`/network/${id}/ports/${portIndex}/remark`, { remark })
}

// ---------- 存储设备 ----------
export const storageApi = {
  list: () => http.get('/storage'),
  create: (data) => http.post('/storage', data),
  update: (id, data) => http.put(`/storage/${id}`, data),
  remove: (id) => http.delete(`/storage/${id}`),
  poll: (id) => http.post(`/storage/${id}/poll`, null, { timeout: 120000 })
}

// ---------- 动环设备 (Modbus TCP: 温湿度/烟感/水浸/UPS) ----------
export const envDeviceApi = {
  meta: () => http.get('/env/meta'),
  summary: () => http.get('/env/summary'),
  devices: () => http.get('/env/devices'),
  createDevice: (data) => http.post('/env/devices', data),
  updateDevice: (id, data) => http.put(`/env/devices/${id}`, data),
  removeDevice: (id) => http.delete(`/env/devices/${id}`),
  points: (id) => http.get(`/env/devices/${id}/points`),
  createPoint: (id, data) => http.post(`/env/devices/${id}/points`, data),
  updatePoint: (id, data) => http.put(`/env/points/${id}`, data),
  removePoint: (id) => http.delete(`/env/points/${id}`),
  poll: (id) => http.post(`/env/devices/${id}/poll`, null, { timeout: 40000 }),
  pollAll: () => http.post('/env/poll-all', null, { timeout: 120000 }),
  testRead: (id, data) => http.post(`/env/devices/${id}/read-point`, data, { timeout: 20000 }),
  history: (pointId, hours = 24) =>
    http.get(`/env/points/${pointId}/history`, { params: { hours } })
}

// ---------- 告警 ----------
export const alertApi = {
  list: (params) => http.get('/alerts', { params }),
  ack: (id) => http.post(`/alerts/${id}/ack`),
  resolve: (id) => http.post(`/alerts/${id}/resolve`),
  analyze: (id) => http.get(`/alerts/${id}/analyze`, { timeout: 120000 })
}

// ---------- 告警规则 ----------
export const alertRuleApi = {
  list: () => http.get('/alerts/rules'),
  create: (data) => http.post('/alerts/rules', data),
  update: (id, data) => http.put(`/alerts/rules/${id}`, data),
  remove: (id) => http.delete(`/alerts/rules/${id}`),
  // 一键生成默认规则 (CPU/内存/磁盘 80%提示 90%告警 …)
  defaults: (channelIds = []) => http.post('/alerts/rules/defaults', { channel_ids: channelIds }),
  meta: () => http.get('/alerts/meta')
}

// ---------- 通知渠道 ----------
export const notifyApi = {
  types: () => http.get('/notify/types'),
  channels: () => http.get('/notify/channels'),
  create: (data) => http.post('/notify/channels', data),
  update: (id, data) => http.put(`/notify/channels/${id}`, data),
  remove: (id) => http.delete(`/notify/channels/${id}`),
  test: (id, data) => http.post(`/notify/channels/${id}/test`, data, { timeout: 30000 }),
  preview: (id) => http.post('/notify/preview', null, { params: { channel_id: id } }),
  logs: (limit = 100) => http.get('/notify/logs', { params: { limit } }),
  clearLogs: () => http.delete('/notify/logs')
}

// ---------- AI 聊天 ----------
export const chatApi = {
  send: (message, session_id = 'default') =>
    http.post('/chat', { message, session_id }, { timeout: 180000 }),
  history: (session_id) => http.get(`/chat/history/${session_id}`)
}

export default http
