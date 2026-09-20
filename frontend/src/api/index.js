import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000, // 30秒超时(之前60秒太长)
})

// 网络错误重试: 最多重试2次, 间隔1秒
const MAX_RETRY = 2
const RETRY_DELAY = 1000

// 请求拦截器：附加 Token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // 标记重试次数
  config._retryCount = config._retryCount || 0
  return config
})

// 响应拦截器：统一错误处理 + 网络错误自动重试
http.interceptors.response.use(
  (res) => res.data,
  async (err) => {
    const config = err.config || {}
    const status = err.response?.status
    const detail = err.response?.data?.detail

    // 401: 登录过期
    if (status === 401) {
      localStorage.removeItem('token')
      if (router.currentRoute.value.path !== '/login') {
        router.push('/login')
        ElMessage.error('登录已过期，请重新登录')
      }
      return Promise.reject(err)
    }

    // 网络错误/超时/502/503/504: 自动重试
    const isRetryable =
      err.code === 'ECONNABORTED' || // 超时
      err.code === 'ERR_NETWORK' || // 网络错误
      !err.response || // 无响应(连接失败)
      [502, 503, 504].includes(status) // 网关错误

    if (isRetryable && config._retryCount < MAX_RETRY) {
      config._retryCount += 1
      console.warn(`[axios] 请求失败, 第${config._retryCount}次重试: ${config.url || ''}`)
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY))
      return http(config)
    }

    // 重试耗尽或非重试错误
    if (isRetryable) {
      ElMessage.error('服务器响应超时，请稍后重试')
    } else {
      ElMessage.error(typeof detail === 'string' ? detail : `请求失败: ${err.message}`)
    }
    return Promise.reject(err)
  }
)

// ---------- 认证 ----------
export const authApi = {
  login: (data) => http.post('/auth/login', data),
  changePassword: (data) => http.post('/auth/change-password', data)
}

// ---------- 系统设置 (AI模型) ----------
export const settingsApi = {
  getLlm: () => http.get('/settings/llm'),
  saveLlm: (data) => http.put('/settings/llm', data),
  testLlm: (data) => http.post('/settings/llm/test', data)
}

// ---------- 服务器 ----------
export const serverApi = {
  list: (params) => http.get('/servers', { params }),
  create: (data) => http.post('/servers', data),
  remove: (id) => http.delete(`/servers/${id}`),
  metrics: (id, params) => http.get(`/servers/${id}/metrics`, { params }),
  detail: (id) => http.get(`/servers/${id}/detail`),
  summary: () => http.get('/servers/stats/summary')
}

// ---------- VMware ----------
export const vmwareApi = {
  listHosts: () => http.get('/vmware/hosts'),
  addHost: (data) => http.post('/vmware/hosts', data),
  deleteHost: (id) => http.delete(`/vmware/hosts/${id}`),
  syncHost: (id) => http.post(`/vmware/hosts/${id}/sync`),
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
  poll: (id) => http.post(`/network/${id}/poll`),
  ports: (id) => http.get(`/network/${id}/ports`),
  testPort: (id, portIndex) => http.post(`/network/${id}/ports/${portIndex}/test`),
  updateRemark: (id, portIndex, remark) =>
    http.put(`/network/${id}/ports/${portIndex}/remark`, { remark })
}

// ---------- 存储设备 ----------
export const storageApi = {
  list: () => http.get('/storage'),
  create: (data) => http.post('/storage', data),
  update: (id, data) => http.put(`/storage/${id}`, data),
  remove: (id) => http.delete(`/storage/${id}`),
  poll: (id) => http.post(`/storage/${id}/poll`)
}

// ---------- 温湿度 ----------
export const envApi = {
  list: () => http.get('/env'),
  create: (data) => http.post('/env', data),
  remove: (id) => http.delete(`/env/${id}`),
  push: (id, temperature, humidity) =>
    http.post(`/env/${id}/push`, null, { params: { temperature, humidity } }),
  history: (id, hours = 24) => http.get(`/env/${id}/history`, { params: { hours } })
}

// ---------- 告警 ----------
export const alertApi = {
  list: (params) => http.get('/alerts', { params }),
  ack: (id) => http.post(`/alerts/${id}/ack`),
  resolve: (id) => http.post(`/alerts/${id}/resolve`),
  analyze: (id) => http.get(`/alerts/${id}/analyze`)
}

// ---------- AI 聊天 ----------
export const chatApi = {
  send: (message, session_id = 'default') => http.post('/chat', { message, session_id }),
  history: (session_id) => http.get(`/chat/history/${session_id}`)
}

export default http
