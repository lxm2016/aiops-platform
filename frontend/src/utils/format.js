// 通用格式化工具
// 后端时间均为UTC且不带时区后缀, 这里统一补Z按UTC解析, 再转本地时间显示

function toDate(value) {
  if (!value) return null
  let s = String(value)
  if (!/[Z+]/.test(s.slice(-6))) s += 'Z'
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatTime(value) {
  const d = toDate(value)
  if (!d) return value || '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export function formatHM(value) {
  const d = toDate(value)
  if (!d) return value || '-'
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 图表X轴时间: 范围<=24小时显示 时:分, 否则显示 月-日 时:分
export function formatAxisTime(value, hours) {
  const d = toDate(value)
  if (!d) return value || '-'
  const pad = (n) => String(n).padStart(2, '0')
  if (hours && Number(hours) <= 24) {
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function statusTagType(status) {
  const map = {
    online: 'success',
    offline: 'danger',
    error: 'danger',
    unknown: 'info'
  }
  return map[status] || 'info'
}

export function statusLabel(status) {
  const map = {
    online: '在线',
    offline: '离线',
    error: '异常',
    unknown: '未知'
  }
  return map[status] || status || '-'
}

export function alertLevelTag(level) {
  const map = { critical: 'danger', warning: 'warning', info: 'primary' }
  return map[level] || 'info'
}

export function alertLevelLabel(level) {
  const map = { critical: '严重', warning: '警告', info: '提示' }
  return map[level] || level
}

export function alertStatusLabel(status) {
  const map = { open: '未处理', ack: '已确认', resolved: '已解决' }
  return map[status] || status
}

export function alertStatusTag(status) {
  const map = { open: 'danger', ack: 'warning', resolved: 'success' }
  return map[status] || 'info'
}

export function percentColor(v) {
  if (v >= 90) return '#ff4d5e'
  if (v >= 70) return '#ffb020'
  return '#00e396'
}
