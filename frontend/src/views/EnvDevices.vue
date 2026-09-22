<template>
  <div class="page">
    <el-card shadow="never">
      <div class="head">
        <div>
          <h2>动环设备</h2>
          <p class="sub">
            温湿度 / 烟感 / 水浸 / UPS —— 平台按间隔主动读 Modbus TCP，
            超阈值直接走已配好的告警通道
          </p>
        </div>
        <div>
          <el-button :loading="polling" @click="pollAll">
            <el-icon><Refresh /></el-icon> 立即刷新全部
          </el-button>
          <el-button type="primary" @click="openDevice()">
            <el-icon><Plus /></el-icon> 新建设备
          </el-button>
        </div>
      </div>

      <div class="stats">
        <div class="stat">
          <span class="label">设备总数</span>
          <b>{{ devices.length }}</b>
        </div>
        <div class="stat">
          <span class="label">在线</span>
          <b class="ok">{{ onlineCount }}</b>
        </div>
        <div class="stat">
          <span class="label">离线</span>
          <b :class="{ bad: offlineCount }">{{ offlineCount }}</b>
        </div>
        <div class="stat">
          <span class="label">点位数</span>
          <b>{{ pointCount }}</b>
        </div>
        <div class="stat">
          <span class="label">报警中</span>
          <b :class="{ bad: alarmCount }">{{ alarmCount }}</b>
        </div>
      </div>

      <el-table
        :data="devices" v-loading="loading" row-key="id"
        :expand-row-keys="expanded"
        @expand-change="onExpand"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="points">
              <div class="points-head">
                <span>点位</span>
                <span>
                  <el-button link type="primary" size="small" @click="loadPoints(row)">
                    刷新点位
                  </el-button>
                  <el-button link type="primary" size="small" @click="openPoint(row)">
                    添加点位
                  </el-button>
                  <el-button
                    link type="primary" size="small" :loading="rowPolling[row.id]"
                    @click="pollOne(row)"
                  >
                    轮询本机
                  </el-button>
                </span>
              </div>
              <el-table
                :data="pointRows(row.id)" size="small" border
                :span-method="pointSpan"
              >
                <el-table-column label="名称" width="160">
                  <template #default="{ row: p }">
                    <b v-if="p.__header" class="pt-group">§ {{ p.name }}</b>
                    <span v-else>{{ p.name }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="地址" width="170">
                  <template #default="{ row: p }">
                    <code v-if="!p.__header">FC{{ String(p.fc).padStart(2, '0') }} @ {{ p.address }}</code>
                  </template>
                </el-table-column>
                <el-table-column label="当前值" width="130">
                  <template #default="{ row: p }">
                    <span v-if="p.__header || p.value === null || p.value === undefined" class="muted">—</span>
                    <b v-else :class="{ bad: isAlarm(p) }">
                      {{ formatPoint(p) }}
                    </b>
                  </template>
                </el-table-column>
                <el-table-column label="原始值" width="100">
                  <template #default="{ row: p }">
                    <span class="muted">{{ p.__header ? '' : (p.raw ?? '—') }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="100">
                  <template #default="{ row: p }">
                    <template v-if="!p.__header">
                      <el-tag v-if="isAlarm(p)" type="danger" size="small">报警</el-tag>
                      <el-tag v-else-if="p.ok" type="success" size="small">正常</el-tag>
                      <el-tag v-else type="info" size="small">读取失败</el-tag>
                    </template>
                  </template>
                </el-table-column>
                <el-table-column prop="unit" label="单位" width="70" />
                <el-table-column label="操作" min-width="150">
                  <template #default="{ row: p }">
                    <template v-if="!p.__header">
                      <el-button link type="primary" size="small" @click="openPoint(row, p)">
                        编辑
                      </el-button>
                      <el-button link type="danger" size="small" @click="delPoint(p, row)">
                        删除
                      </el-button>
                    </template>
                  </template>
                </el-table-column>
              </el-table>
              <p v-if="row.last_error" class="err">最近一次错误：{{ row.last_error }}</p>
              <p v-if="!pointsOf(row.id).length" class="muted">
                还没有点位。点「添加点位」配置寄存器地址。
              </p>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="设备名称" min-width="200" />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            {{ categoryLabel(row.category) }}
          </template>
        </el-table-column>
        <el-table-column label="Modbus 地址" min-width="170">
          <template #default="{ row }">
            <code>{{ row.ip }}:{{ row.port }} / 从站 {{ row.slave_id }}<template v-if="row.protocol === 'modbus_rtu'"> / RTU透传</template></code>
          </template>
        </el-table-column>
        <el-table-column prop="location" label="安装位置" min-width="180" />
        <el-table-column prop="cluster" label="采集集群" width="110" />
        <el-table-column label="轮询" width="90">
          <template #default="{ row }">
            {{ row.poll_interval ? row.poll_interval + 's' : '不轮询' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'online'" type="success" size="small">在线</el-tag>
            <el-tag v-else-if="row.status === 'offline'" type="danger" size="small">离线</el-tag>
            <el-tag v-else type="info" size="small">未探测</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近通信" width="160">
          <template #default="{ row }">{{ fmtTime(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDevice(row)">
              编辑
            </el-button>
            <el-button link type="danger" size="small" @click="delDevice(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 设备编辑 -->
    <el-dialog v-model="devVisible" :title="devForm.id ? '编辑设备' : '新建设备'" width="640px">
      <el-form :model="devForm" label-width="110px">
        <el-form-item label="设备名称" required>
          <el-input v-model="devForm.name" />
        </el-form-item>
        <el-form-item label="设备类型">
          <el-select v-model="devForm.category" style="width:100%" placeholder=" " @change="onCategoryChange">
            <el-option
              v-for="c in meta.categories" :key="c.value"
              :label="c.label" :value="c.value"
            />
          </el-select>
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="14">
            <el-form-item label="IP 地址" required>
              <el-input v-model="devForm.ip" />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <el-form-item label="TCP 端口" required>
              <el-input-number v-model="devForm.port" :min="1" :max="65535" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="10">
            <el-form-item label="传输协议">
              <el-select v-model="devForm.protocol" style="width:100%">
                <el-option
                  v-for="p in (meta.protocols || [])" :key="p.value"
                  :label="p.label" :value="p.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="从站地址（Modbus地址）">
              <el-input-number v-model="devForm.slave_id" :min="1" :max="247" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="轮询间隔">
              <el-select v-model="devForm.poll_interval" style="width:100%">
                <el-option label="不自动轮询" :value="0" />
                <el-option label="30 秒" :value="30" />
                <el-option label="1 分钟" :value="60" />
                <el-option label="5 分钟" :value="300" />
                <el-option label="10 分钟" :value="600" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="采集集群">
              <el-input v-model="devForm.cluster" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="资源组">
              <el-input v-model="devForm.resource_group" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="安装位置">
          <el-input v-model="devForm.location" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="devForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="devVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveDevice">保存</el-button>
      </template>
    </el-dialog>

    <!-- 点位编辑 -->
    <el-dialog v-model="ptVisible" :title="ptForm.id ? '编辑点位' : '添加点位'" width="620px">
      <el-form :model="ptForm" label-width="120px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="点位名称" required>
              <el-input v-model="ptForm.name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="告警标识">
              <el-select v-model="ptForm.key" clearable style="width:100%">
                <el-option
                  v-for="k in meta.point_keys" :key="k.value"
                  :label="k.label + '（' + k.value + '）'" :value="k.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="功能码">
              <el-select v-model="ptForm.fc" style="width:100%">
                <el-option
                  v-for="f in meta.function_codes" :key="f.value"
                  :label="f.label" :value="f.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="寄存器地址">
              <el-input-number v-model="ptForm.address" :min="0" :max="65535" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="数据类型">
              <el-select v-model="ptForm.data_type" style="width:100%">
                <el-option
                  v-for="d in meta.data_types" :key="d.value"
                  :label="d.label" :value="d.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item v-if="ptForm.data_type === 'bit'" label="第几位">
              <el-input-number v-model="ptForm.bit_index" :min="0" :max="15" style="width:100%" />
            </el-form-item>
            <el-form-item v-else label="系数">
              <el-input-number
                v-model="ptForm.scale" :step="0.1" :precision="4"
                style="width:100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="单位">
              <el-input v-model="ptForm.unit" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="点位分组">
              <el-input v-model="ptForm.group" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="报警值">
              <el-input-number v-model="ptForm.alarm_value" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="试读">
          <el-button :loading="testing" @click="testRead">读一次看看</el-button>
          <span v-if="testResult" class="test-result" :class="{ bad: !testResult.success }">
            {{ testResult.success
              ? `原始值 ${testResult.raw} → 显示值 ${testResult.value}`
              : testResult.error }}
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ptVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="savePoint">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { envDeviceApi } from '@/api'

const loading = ref(false)
const saving = ref(false)
const polling = ref(false)
const testing = ref(false)
const devices = ref([])
const pointsMap = reactive({})
const expanded = ref([])
const rowPolling = reactive({})
const meta = reactive({ categories: [], templates: {}, point_keys: [], function_codes: [], data_types: [] })

const devVisible = ref(false)
const ptVisible = ref(false)
const testResult = ref(null)

const emptyDevice = () => ({
  id: null, name: '', category: 'temp_humidity', protocol: 'modbus_tcp',
  ip: '', port: 502, slave_id: 1, location: '', cluster: '',
  resource_group: '', poll_interval: 60, enabled: true, remark: ''
})
const emptyPoint = () => ({
  id: null, name: '', key: '', group: '', fc: 3, address: 0, data_type: 'u16',
  bit_index: 0, scale: 1, offset: 0, unit: '', alarm_value: null,
  sort: 0, enabled: true
})
const devForm = reactive(emptyDevice())
const ptForm = reactive(emptyPoint())
const ptDeviceId = ref(null)

const onlineCount = computed(() => devices.value.filter(d => d.status === 'online').length)
const offlineCount = computed(() => devices.value.filter(d => d.status === 'offline').length)
const pointCount = computed(() =>
  Object.values(pointsMap).reduce((n, arr) => n + (arr?.length || 0), 0))
const alarmCount = computed(() =>
  Object.values(pointsMap).reduce((n, arr) => n + (arr || []).filter(isAlarm).length, 0))

function pointsOf(id) {
  return pointsMap[id] || []
}
// 把点位按 group 分组, 在每组前插一行"小节标题"行; 没有任何分组时保持原样
function pointRows(id) {
  const pts = pointsMap[id] || []
  if (!pts.some(p => (p.group || '') !== '')) return pts
  const rows = []
  let last = null
  for (const p of pts) {
    const g = p.group || ''
    if (g !== last) {
      // 小节标题行: 其余字段占位成"无害"值, 避免各列渲染时报错
      rows.push({ __header: true, name: g || '通用', fc: 4, address: 0,
                  data_type: 'u16', value: null, raw: null, ok: true,
                  unit: '', alarm_value: null })
      last = g
    }
    rows.push(p)
  }
  return rows
}
// 小节标题行横跨全部 7 列
function pointSpan({ row, columnIndex }) {
  if (row.__header) return columnIndex === 0 ? [1, 7] : [0, 0]
  return [1, 1]
}
function isAlarm(p) {
  if (p.alarm_value === null || p.alarm_value === undefined) return false
  if (p.value === null || p.value === undefined) return false
  return Math.abs(Number(p.value) - Number(p.alarm_value)) < 1e-6
}
function formatPoint(p) {
  const v = Number(p.value)
  if (p.data_type === 'bit' || p.alarm_value !== null) {
    return isAlarm(p) ? '报警' : '正常'
  }
  const num = Number.isInteger(v) ? v : v.toFixed(1)
  return `${num}${p.unit || ''}`
}
function categoryLabel(v) {
  return (meta.categories.find(c => c.value === v) || {}).label || v
}
function fmtTime(t) {
  if (!t) return '—'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

async function loadMeta() {
  try {
    const data = await envDeviceApi.meta()   // 拦截器已解包, 直接就是数据
    Object.assign(meta, data)
  } catch (e) { /* 忽略 */ }
}

async function load() {
  loading.value = true
  try {
    const data = await envDeviceApi.devices()   // 拦截器已解包, 直接就是数组
    devices.value = data
    for (const d of data) pointsMap[d.id] = d.points || []
  } catch (e) {
    ElMessage.error('加载动环设备失败')
  } finally {
    loading.value = false
  }
}

async function loadPoints(row) {
  const data = await envDeviceApi.points(row.id)   // 拦截器已解包
  pointsMap[row.id] = data
}

async function onExpand(row, rows) {
  expanded.value = rows.map(r => r.id)
  if (rows.some(r => r.id === row.id)) await loadPoints(row)
}

async function pollOne(row) {
  rowPolling[row.id] = true
  try {
    const data = await envDeviceApi.poll(row.id)   // 拦截器已解包
    if (data.ok) {
      ElMessage.success(`${row.name}：读到 ${data.ok} 个点位`)
    } else {
      ElMessage.error(`${row.name}：${data.error || '全部点位读取失败'}`)
    }
    await loadPoints(row)
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '轮询失败')
  } finally {
    rowPolling[row.id] = false
  }
}

async function pollAll() {
  polling.value = true
  try {
    const data = await envDeviceApi.pollAll()   // 拦截器已解包
    const ok = (data.devices || []).reduce((n, d) => n + (d.ok || 0), 0)
    const fail = (data.devices || []).reduce((n, d) => n + (d.fail || 0), 0)
    ElMessage.success(`轮询完成：成功 ${ok} 个点位，失败 ${fail} 个`)
    for (const d of devices.value) await loadPoints(d)
    await load()
  } catch (e) {
    ElMessage.error('批量轮询失败')
  } finally {
    polling.value = false
  }
}

function openDevice(row) {
  Object.assign(devForm, row ? { ...emptyDevice(), ...row } : emptyDevice())
  devVisible.value = true
}

function onCategoryChange(cat) {
  const tpl = meta.templates?.[cat]
  if (!devForm.id && tpl) {
    ElMessage.info(`已按「${categoryLabel(cat)}」预置 ${tpl.length} 个点位，保存后可在展开行里调整`)
  }
}

async function saveDevice() {
  if (!devForm.name || !devForm.ip) {
    return ElMessage.warning('设备名称和 IP 地址必填')
  }
  saving.value = true
  try {
    const payload = { ...devForm }
    delete payload.id
    delete payload.points
    delete payload.status
    delete payload.last_seen
    delete payload.last_error
    delete payload.created_at
    if (devForm.id) {
      await envDeviceApi.updateDevice(devForm.id, payload)
    } else {
      await envDeviceApi.createDevice(payload)
    }
    ElMessage.success('已保存')
    devVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function delDevice(row) {
  try {
    await ElMessageBox.confirm(`确定删除设备「${row.name}」及其所有点位和历史数据？`,
                               '删除确认', { type: 'warning' })
  } catch { return }
  await envDeviceApi.removeDevice(row.id)
  ElMessage.success('已删除')
  await load()
}

function openPoint(deviceRow, point) {
  ptDeviceId.value = deviceRow.id
  Object.assign(ptForm, point ? { ...point } : emptyPoint())
  testResult.value = null
  ptVisible.value = true
}

async function testRead() {
  testing.value = true
  testResult.value = null
  try {
    const data = await envDeviceApi.testRead(ptDeviceId.value, {   // 拦截器已解包
      name: ptForm.name, fc: ptForm.fc, address: ptForm.address,
      data_type: ptForm.data_type, bit_index: ptForm.bit_index,
      scale: ptForm.scale, offset: ptForm.offset
    })
    testResult.value = data
  } catch (e) {
    testResult.value = { success: false, error: e?.response?.data?.detail || '试读失败' }
  } finally {
    testing.value = false
  }
}

async function savePoint() {
  if (!ptForm.name) return ElMessage.warning('点位名称必填')
  saving.value = true
  try {
    const payload = { ...ptForm }
    delete payload.id
    delete payload.device_id
    delete payload.value
    delete payload.raw
    delete payload.ok
    delete payload.updated_at
    if (ptForm.id) {
      await envDeviceApi.updatePoint(ptForm.id, payload)
    } else {
      await envDeviceApi.createPoint(ptDeviceId.value, payload)
    }
    ElMessage.success('已保存')
    ptVisible.value = false
    const d = devices.value.find(x => x.id === ptDeviceId.value)
    if (d) await loadPoints(d)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function delPoint(point, deviceRow) {
  try {
    await ElMessageBox.confirm(`删除点位「${point.name}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  await envDeviceApi.removePoint(point.id)
  ElMessage.success('已删除')
  await loadPoints(deviceRow)
}

onMounted(async () => {
  await loadMeta()
  await load()
})
</script>

<style scoped>
.page { padding: 4px; }
.head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.head h2 { margin: 0 0 4px; font-size: 18px; font-weight: 500; }
.sub { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); }
.stats { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.stat {
  background: var(--el-fill-color-light); border-radius: 8px;
  padding: 10px 18px; display: flex; flex-direction: column; gap: 2px;
}
.stat .label { font-size: 12px; color: var(--el-text-color-secondary); }
.stat b { font-size: 20px; font-weight: 500; }
.stat b.ok { color: var(--el-color-success); }
.stat b.bad { color: var(--el-color-danger); }
.points { padding: 4px 16px 12px 40px; }
.points-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 13px; }
code { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.muted { color: var(--el-text-color-placeholder); }
.pt-group { color: var(--el-color-primary); font-size: 13px; }
.bad { color: var(--el-color-danger); }
.tip { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; margin-top: 2px; }
.err { margin: 8px 0 0; font-size: 12px; color: var(--el-color-danger); }
.test-result { margin-left: 12px; font-size: 13px; color: var(--el-color-success); }
.test-result.bad { color: var(--el-color-danger); }
</style>
