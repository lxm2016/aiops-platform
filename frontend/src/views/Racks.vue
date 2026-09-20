<template>
  <div v-loading="loading" class="idc-page">
    <!-- 顶部工具栏 -->
    <div class="page-header">
      <div class="page-title">机柜管理</div>
      <div style="display: flex; gap: 10px; align-items: center">
        <el-input
          v-model="searchKeyword"
          placeholder="搜索设备名称/SN/IP定位"
          :prefix-icon="Search"
          style="width: 260px"
          clearable
          @keyup.enter="handleSearch"
          @clear="clearSearch"
        />
        <el-tooltip content="大屏巡检模式" placement="bottom">
          <el-button :icon="FullScreen" :type="bigScreen ? 'primary' : 'default'" @click="toggleBigScreen" />
        </el-tooltip>
        <el-button type="primary" :icon="Plus" @click="openRackDialog()">添加机柜</el-button>
        <el-button :icon="Upload" @click="openImport">Excel导入</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 图例 -->
    <div class="legend-bar">
      <span v-for="(label, t) in TYPE_MAP" :key="t" class="legend-item">
        <i class="led-dot" :style="{ background: TYPE_COLOR_HEX[t] }"></i>{{ label }}
      </span>
      <span class="legend-item"><i class="led-dot led-on"></i>在线</span>
      <span class="legend-item"><i class="led-dot led-warn"></i>告警</span>
      <span class="legend-tip">鼠标拖拽旋转 · 滚轮缩放 · 点击机柜进入3D详情 · 搜索定位设备</span>
    </div>

    <!-- 大屏模式信息条 -->
    <transition name="slide-down">
      <div v-if="bigScreen" class="bigscreen-bar">
        <div class="bs-stat"><span class="bs-num">{{ racks.length }}</span><span class="bs-lbl">机柜</span></div>
        <div class="bs-stat"><span class="bs-num">{{ totalDevices }}</span><span class="bs-lbl">设备</span></div>
        <div class="bs-stat"><span class="bs-num" style="color:#00e396">{{ onlineCount }}</span><span class="bs-lbl">在线</span></div>
        <div class="bs-stat"><span class="bs-num" style="color:#ff4d5e">{{ offlineCount }}</span><span class="bs-lbl">离线</span></div>
        <div class="bs-stat"><span class="bs-num" style="color:#ffb020">{{ warningCount }}</span><span class="bs-lbl">告警</span></div>
        <div class="bs-clock">{{ clockStr }}</div>
      </div>
    </transition>

    <el-empty v-if="!racks.length" description="暂无机柜，请先添加机柜或通过Excel导入设备" :image-size="80" />

    <!-- Three.js 3D机房场景 -->
    <Rack3DScene
      v-if="racks.length"
      :racks="racks"
      :devices-map="devicesMap"
      :big-screen="bigScreen"
      :highlight-rack-id="searchRackId"
      @rack-click="openRackDetail"
      @clear-search="clearSearch"
    />

    <!-- 机柜列表(表格视图，辅助管理) -->
    <div v-if="racks.length" class="rack-list-section">
      <div class="section-title">
        <el-icon><Grid /></el-icon> 机柜列表
      </div>
      <el-table :data="racks" size="small" border>
        <el-table-column prop="name" label="机柜编号" width="100" />
        <el-table-column prop="row_name" label="所在列" width="90" />
        <el-table-column prop="u_height" label="U数" width="60" />
        <el-table-column label="设备数" width="70">
          <template #default="{ row }">{{ (devicesMap[row.id] || []).length }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openRackDetail(row)">3D详情</el-button>
            <el-button link size="small" @click="openRackDialog(row)">编辑</el-button>
            <el-popconfirm title="删除机柜将同时删除其中所有设备，确定？" @confirm="deleteRack(row)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ============ 3D机柜详情抽屉 ============ -->
    <el-drawer v-model="detailVisible" :title="`机柜 ${detailRack?.name || ''} · ${detailRack?.u_height || 0}U`" size="880px">
      <template v-if="detailRack">
        <div class="u-toolbar">
          <el-radio-group v-model="detailSide" size="small">
            <el-radio-button value="front">前侧</el-radio-button>
            <el-radio-button value="back">后侧</el-radio-button>
          </el-radio-group>
          <el-radio-group v-model="detailView" size="small">
            <el-radio-button value="3d">3D 视图</el-radio-button>
            <el-radio-button value="2d">2D U位图</el-radio-button>
          </el-radio-group>
          <el-button size="small" type="primary" :icon="Plus" @click="openDeviceDialog()">添加设备</el-button>
        </div>

        <!-- 真 3D 单机柜: 看 U 位刻度、设备面板、状态灯 -->
        <RackDetail3D
          v-if="detailView === '3d'"
          :rack="detailRack"
          :devices="detailDevices"
          :side="detailSide"
          @device-click="openDeviceDialog"
        />

        <!-- 2D U位图: 一屏看清 U 位占用 -->
        <div v-else class="detail-3d-scene">
          <div class="detail-3d-rack">
            <div class="d-frame-top"></div>
            <div class="d-frame-bottom"></div>
            <div class="d-frame-left">
              <div class="d-u-scale">
                <span v-for="u in descUnits" :key="u" class="d-u-num">{{ u }}</span>
              </div>
            </div>
            <div class="d-frame-right"></div>
            <div class="d-device-area" :style="{ height: detailRack.u_height * D_U_ROW + 'px' }">
              <div
                v-for="u in descUnits"
                :key="'grid-'+u"
                class="d-u-grid"
                :style="{ top: (detailRack.u_height - u) * D_U_ROW + 'px', height: D_U_ROW + 'px' }"
                @click="openDeviceDialog(null, u)"
              ></div>
              <div
                v-for="d in sideDevices"
                :key="d.id"
                class="d-device"
                :class="'dev-' + d.device_type"
                :style="detailDevStyle(d)"
                @click.stop="openDeviceDialog(d)"
              >
                <div class="d-dev-panel">
                  <div class="d-dev-leds">
                    <span class="d-led" :class="devLedClass(d)"></span>
                    <span class="d-led d-led-small"></span>
                    <span class="d-led d-led-small"></span>
                  </div>
                  <div class="d-dev-info">
                    <div class="d-dev-name">{{ d.name }}</div>
                    <div class="d-dev-sub">{{ TYPE_MAP[d.device_type] || d.device_type }} · {{ d.u_start }}-{{ d.u_start + d.u_size - 1 }}U</div>
                  </div>
                  <div v-if="d.device_type === 'server'" class="d-vents"><i v-for="n in 6" :key="n"></i></div>
                  <div v-if="d.device_type === 'switch'" class="d-ports"><i v-for="n in 8" :key="n"></i></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="dev-table-title">设备清单（前后侧全部）</div>
        <el-table :data="detailDevices" size="small" border max-height="260">
          <el-table-column prop="name" label="设备名称" min-width="110" />
          <el-table-column label="状态" width="88">
            <template #default="{ row }">
              <span class="st-tag" :style="{ color: statusCss(row.status), borderColor: statusCss(row.status) }">
                {{ statusText(row.status) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="dark" :color="TYPE_COLOR_HEX[row.device_type]" style="border: none">
                {{ TYPE_MAP[row.device_type] || row.device_type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="U位" width="90">
            <template #default="{ row }">{{ row.u_start }}-{{ row.u_start + row.u_size - 1 }}U</template>
          </el-table-column>
          <el-table-column label="侧" width="60">
            <template #default="{ row }">{{ row.side === 'front' ? '前' : '后' }}</template>
          </el-table-column>
          <el-table-column prop="remark" label="备注" min-width="100" show-overflow-tooltip />
          <el-table-column label="操作" width="110" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDeviceDialog(row)">编辑</el-button>
              <el-popconfirm title="确定删除该设备？" @confirm="deleteDevice(row)">
                <template #reference>
                  <el-button link type="danger" size="small">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>

    <!-- 添加/编辑机柜 -->
    <el-dialog v-model="rackDialogVisible" :title="rackForm.id ? '编辑机柜' : '添加机柜'" width="460px">
      <el-form ref="rackFormRef" :model="rackForm" :rules="rackRules" label-width="90px">
        <el-form-item label="机柜编号" prop="name">
          <el-input v-model="rackForm.name" placeholder="如 A01" />
        </el-form-item>
        <el-form-item label="所在列" prop="row_name">
          <el-input v-model="rackForm.row_name" placeholder="如 A列" />
        </el-form-item>
        <el-form-item label="U数" prop="u_height">
          <el-input-number v-model="rackForm.u_height" :min="1" :max="60" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="rackForm.remark" type="textarea" :rows="2" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rackDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleRackSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 添加/编辑设备 -->
    <el-dialog
      v-model="devDialogVisible"
      :title="devForm.id ? '编辑设备' : `添加设备 - ${detailRack?.name || ''}`"
      width="480px"
    >
      <el-form ref="devFormRef" :model="devForm" :rules="devRules" label-width="90px">
        <el-form-item label="设备名称" prop="name">
          <el-input v-model="devForm.name" placeholder="如 核心交换机-1" />
        </el-form-item>
        <el-form-item label="设备类型" prop="device_type">
          <el-select v-model="devForm.device_type" style="width: 100%">
            <el-option v-for="(label, t) in TYPE_MAP" :key="t" :value="t" :label="label" />
          </el-select>
        </el-form-item>
        <el-form-item label="起始U位" prop="u_start">
          <el-input-number v-model="devForm.u_start" :min="1" :max="detailRack?.u_height || 42" style="width: 100%" />
        </el-form-item>
        <el-form-item label="占用U数" prop="u_size">
          <el-input-number v-model="devForm.u_size" :min="1" :max="detailRack?.u_height || 42" style="width: 100%" />
        </el-form-item>
        <el-form-item label="放置侧" prop="side">
          <el-radio-group v-model="devForm.side">
            <el-radio value="front">前侧</el-radio>
            <el-radio value="back">后侧</el-radio>
          </el-radio-group>
          <span class="form-tip">同一U位前后侧可各放一台设备</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="devForm.remark" type="textarea" :rows="2" placeholder="选填，如用途/去向" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="devDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleDevSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- Excel导入 -->
    <el-dialog v-model="importVisible" title="从Excel导入设备" width="720px">
      <div class="import-tip">
        表头须包含：<b>机柜编号、设备名称、设备类型、起始U位、占用U数、放置侧、备注</b>。
        设备类型支持：服务器/交换机/存储/安全设备/其他；放置侧支持：前/后。
        机柜编号不存在时将自动创建（默认42U，列名取编号中的字母前缀）。
      </div>
      <div style="display: flex; gap: 10px; margin: 12px 0">
        <el-button size="small" :icon="Download" @click="downloadTemplate">下载导入模板</el-button>
        <el-upload :auto-upload="false" :show-file-list="false" accept=".xlsx,.xls" :on-change="handleFileChange">
          <el-button size="small" type="primary" :icon="Upload">选择Excel文件</el-button>
        </el-upload>
        <span v-if="importFileName" style="color: var(--text-sub); font-size: 12px; align-self: center">
          {{ importFileName }}（{{ importPreview.length }} 行）
        </span>
      </div>
      <el-table v-if="importPreview.length" :data="importPreview" size="small" border max-height="300">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="rack_name" label="机柜编号" width="90" />
        <el-table-column prop="name" label="设备名称" min-width="120" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">{{ TYPE_MAP[row.device_type] || row.device_type }}</template>
        </el-table-column>
        <el-table-column label="U位" width="90">
          <template #default="{ row }">{{ row.u_start }}-{{ row.u_start + row.u_size - 1 }}U</template>
        </el-table-column>
        <el-table-column label="侧" width="60">
          <template #default="{ row }">{{ row.side === 'front' ? '前' : '后' }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="100" show-overflow-tooltip />
      </el-table>
      <div v-if="importErrors.length" class="import-errors">
        <div v-for="(e, i) in importErrors" :key="i">{{ e }}</div>
      </div>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="!importPreview.length" @click="handleImport">
          导入 {{ importPreview.length }} 条
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Upload, Download, Grid, Search, FullScreen } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'
import { rackApi } from '@/api'
import Rack3DScene from '@/components/Rack3DScene.vue'
import RackDetail3D from '@/components/RackDetail3D.vue'

const D_U_ROW = 28

const TYPE_MAP = { server: '服务器', switch: '交换机', storage: '存储', security: '安全设备', other: '其他' }
const TYPE_COLOR_HEX = {
  server: '#2f7bff',
  switch: '#00c48f',
  storage: '#ff9f43',
  security: '#a55eea',
  other: '#5d7092'
}
const TYPE_REV = {
  '服务器': 'server', '交换机': 'switch', '存储': 'storage', '存储设备': 'storage',
  '安全设备': 'security', '安全': 'security', '其他': 'other', '其它': 'other',
  'server': 'server', 'switch': 'switch', 'storage': 'storage', 'security': 'security', 'other': 'other'
}

const STATUS_TEXT = { online: '在线', offline: '离线', warning: '告警', critical: '严重', unknown: '未纳管' }
const STATUS_CSS = {
  online: '#00e396', offline: '#4a5a70', warning: '#ffb020',
  critical: '#ff4d5e', unknown: '#5d7092'
}
function statusText(s) { return STATUS_TEXT[s] || '未纳管' }
function statusCss(s) { return STATUS_CSS[s] || STATUS_CSS.unknown }

const loading = ref(false)
const submitting = ref(false)
const racks = ref([])
const devicesMap = ref({})

const searchKeyword = ref('')
const searchRackId = ref(null)
const bigScreen = ref(false)
const clockStr = ref('')
let bsTimer = null
let patrolTimer = null

const totalDevices = computed(() => Object.values(devicesMap.value).reduce((s, arr) => s + arr.length, 0))
const allDevices = computed(() => Object.values(devicesMap.value).flat())
const onlineCount = computed(() => allDevices.value.filter(d => d.status === 'online').length)
const offlineCount = computed(() => allDevices.value.filter(d => d.status === 'offline').length)
const warningCount = computed(() => allDevices.value.filter(d => d.status === 'warning' || d.status === 'critical').length)

function devLedClass(d) {
  if (d.status === 'critical') return 'led-critical'
  if (d.status === 'warning') return 'led-warn'
  if (d.status === 'offline') return 'led-off'
  return 'led-on'
}

function updateClock() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  clockStr.value = `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function toggleBigScreen() {
  bigScreen.value = !bigScreen.value
  if (bigScreen.value) {
    updateClock()
    bsTimer = setInterval(updateClock, 1000)
    patrolTimer = setInterval(() => load(), 30000)
  } else {
    clearInterval(bsTimer)
    clearInterval(patrolTimer)
  }
}

function handleSearch() {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) { searchRackId.value = null; return }
  for (const [rackId, devs] of Object.entries(devicesMap.value)) {
    const found = devs.some(d =>
      (d.name || '').toLowerCase().includes(kw) ||
      (d.sn || '').toLowerCase().includes(kw) ||
      (d.ip || '').toLowerCase().includes(kw) ||
      (d.remark || '').toLowerCase().includes(kw)
    )
    if (found) {
      searchRackId.value = parseInt(rackId)
      ElMessage.success(`已定位到机柜 ${racks.value.find(r => r.id === parseInt(rackId))?.name || rackId}`)
      return
    }
  }
  ElMessage.warning('未找到匹配设备')
  searchRackId.value = null
}

function clearSearch() {
  searchKeyword.value = ''
  searchRackId.value = null
}

const stats = ref(null)

async function load() {
  loading.value = true
  try {
    // 优先一次请求取回机柜+设备+统计(含状态联动), 避免逐柜请求的 N+1
    try {
      const ov = await rackApi.overview()
      racks.value = ov.racks || []
      devicesMap.value = ov.devices || {}
      stats.value = ov.stats || null
      return
    } catch (e) {
      // 后端较旧没有该接口时回退到逐柜请求
    }
    racks.value = await rackApi.list()
    const entries = await Promise.all(
      racks.value.map(async (r) => [r.id, await rackApi.devices(r.id)])
    )
    devicesMap.value = Object.fromEntries(entries)
  } finally {
    loading.value = false
  }
}

const rackDialogVisible = ref(false)
const rackFormRef = ref(null)
const rackForm = reactive({ id: null, name: '', row_name: 'A列', u_height: 42, remark: '' })
const rackRules = {
  name: [{ required: true, message: '请输入机柜编号', trigger: 'blur' }],
  row_name: [{ required: true, message: '请输入所在列', trigger: 'blur' }]
}

function openRackDialog(rack) {
  Object.assign(rackForm, rack
    ? { id: rack.id, name: rack.name, row_name: rack.row_name, u_height: rack.u_height, remark: rack.remark }
    : { id: null, name: '', row_name: 'A列', u_height: 42, remark: '' })
  rackDialogVisible.value = true
}

async function handleRackSubmit() {
  await rackFormRef.value.validate()
  submitting.value = true
  try {
    const payload = {
      name: rackForm.name.trim(),
      row_name: rackForm.row_name.trim(),
      u_height: rackForm.u_height,
      remark: rackForm.remark
    }
    if (rackForm.id) {
      await rackApi.update(rackForm.id, payload)
      ElMessage.success('更新成功')
    } else {
      await rackApi.create(payload)
      ElMessage.success('添加成功')
    }
    rackDialogVisible.value = false
    load()
  } finally {
    submitting.value = false
  }
}

async function deleteRack(rack) {
  await rackApi.remove(rack.id)
  ElMessage.success('删除成功')
  load()
}

const detailVisible = ref(false)
const detailRack = ref(null)
const detailSide = ref('front')
const detailView = ref('3d')
const devDialogVisible = ref(false)
const devFormRef = ref(null)
const devForm = reactive({
  id: null, name: '', device_type: 'server', u_start: 1, u_size: 1, side: 'front', remark: ''
})
const devRules = {
  name: [{ required: true, message: '请输入设备名称', trigger: 'blur' }],
  u_start: [{ required: true, message: '请输入起始U位', trigger: 'blur' }]
}

const detailDevices = computed(() => {
  if (!detailRack.value) return []
  return devicesMap.value[detailRack.value.id] || []
})

const sideDevices = computed(() => detailDevices.value.filter((d) => d.side === detailSide.value))

const descUnits = computed(() => {
  if (!detailRack.value) return []
  const arr = []
  for (let u = detailRack.value.u_height; u >= 1; u--) arr.push(u)
  return arr
})

function detailDevStyle(d) {
  const top = (detailRack.value.u_height - (d.u_start + d.u_size - 1)) * D_U_ROW
  return { top: top + 'px', height: d.u_size * D_U_ROW - 2 + 'px' }
}

function openRackDetail(rack) {
  detailRack.value = rack
  detailSide.value = 'front'
  detailVisible.value = true
}

function openDeviceDialog(device, presetU) {
  if (device) {
    Object.assign(devForm, {
      id: device.id, name: device.name, device_type: device.device_type,
      u_start: device.u_start, u_size: device.u_size, side: device.side, remark: device.remark
    })
  } else {
    Object.assign(devForm, {
      id: null, name: '', device_type: 'server',
      u_start: presetU || 1, u_size: 1, side: detailSide.value, remark: ''
    })
  }
  devDialogVisible.value = true
}

async function handleDevSubmit() {
  await devFormRef.value.validate()
  submitting.value = true
  try {
    const payload = {
      name: devForm.name.trim(),
      device_type: devForm.device_type,
      u_start: devForm.u_start,
      u_size: devForm.u_size,
      side: devForm.side,
      remark: devForm.remark
    }
    if (devForm.id) {
      await rackApi.updateDevice(devForm.id, payload)
      ElMessage.success('更新成功')
    } else {
      await rackApi.addDevice(detailRack.value.id, payload)
      ElMessage.success('添加成功')
    }
    devDialogVisible.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

async function deleteDevice(device) {
  await rackApi.removeDevice(device.id)
  ElMessage.success('删除成功')
  load()
}

// Excel导入
const importVisible = ref(false)
const importPreview = ref([])
const importErrors = ref([])
const importFileName = ref('')

function openImport() {
  importPreview.value = []
  importErrors.value = []
  importFileName.value = ''
  importVisible.value = true
}

function downloadTemplate() {
  const rows = [
    { '机柜编号': 'A01', '设备名称': '核心交换机-1', '设备类型': '交换机', '起始U位': 40, '占用U数': 1, '放置侧': '前', '备注': '核心层' },
    { '机柜编号': 'A01', '设备名称': '数据库服务器-1', '设备类型': '服务器', '起始U位': 12, '占用U数': 2, '放置侧': '前', '备注': '' }
  ]
  const ws = XLSX.utils.json_to_sheet(rows)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '机柜设备')
  XLSX.writeFile(wb, '机柜设备导入模板.xlsx')
}

function pickField(row, keys) {
  for (const k of Object.keys(row)) {
    const kk = String(k).trim()
    if (keys.includes(kk)) return row[k]
  }
  return ''
}

async function handleFileChange(uploadFile) {
  try {
    const buf = await uploadFile.raw.arrayBuffer()
    const wb = XLSX.read(buf)
    const ws = wb.Sheets[wb.SheetNames[0]]
    const rawRows = XLSX.utils.sheet_to_json(ws, { defval: '' })
    const items = []
    const errs = []
    rawRows.forEach((r, i) => {
      const rackName = String(pickField(r, ['机柜编号', '机柜', 'rack', 'rack_name'])).trim()
      const name = String(pickField(r, ['设备名称', '名称', 'name'])).trim()
      const typeRaw = String(pickField(r, ['设备类型', '类型', 'type', 'device_type'])).trim()
      const uStart = parseInt(pickField(r, ['起始U位', '起始U', 'U位', 'u_start']), 10)
      const uSizeRaw = pickField(r, ['占用U数', 'U数', '高度U', 'u_size'])
      const uSize = uSizeRaw === '' || uSizeRaw == null ? 1 : parseInt(uSizeRaw, 10)
      const sideRaw = String(pickField(r, ['放置侧', '侧面', 'side'])).trim()
      const remark = String(pickField(r, ['备注', 'remark'])).trim()
      const rowLabel = `第${i + 2}行`

      if (!rackName || !name) { errs.push(`${rowLabel}: 机柜编号/设备名称为空，已跳过`); return }
      if (!Number.isInteger(uStart) || uStart < 1) { errs.push(`${rowLabel}: 起始U位无效，已跳过`); return }
      const deviceType = TYPE_REV[typeRaw] || 'other'
      const side = ['后', '后侧', 'back'].includes(sideRaw) ? 'back' : 'front'
      items.push({
        rack_name: rackName, name, device_type: deviceType,
        u_start: uStart, u_size: Number.isInteger(uSize) && uSize > 0 ? uSize : 1,
        side, remark
      })
    })
    importPreview.value = items
    importErrors.value = errs
    importFileName.value = uploadFile.name
    if (!items.length) ElMessage.warning('未解析到有效数据，请检查表头格式')
  } catch (e) {
    ElMessage.error('Excel解析失败: ' + e.message)
  }
}

async function handleImport() {
  submitting.value = true
  try {
    const res = await rackApi.importDevices(importPreview.value)
    if (res.errors?.length) {
      importErrors.value = res.errors
      ElMessage.warning(`成功导入 ${res.success} 条，${res.errors.length} 条失败`)
    } else {
      ElMessage.success(`成功导入 ${res.success} 条设备`)
      importVisible.value = false
    }
    load()
  } finally {
    submitting.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => {
  clearInterval(bsTimer)
  clearInterval(patrolTimer)
})
</script>

<style scoped>
.idc-page { min-height: 100%; }

.legend-bar {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  margin-bottom: 16px; padding: 10px 14px;
  border: 1px solid var(--border-tech, #1a2a44);
  border-radius: 6px; background: rgba(47, 123, 255, 0.04);
}
.legend-item { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-sub, #7d92b5); }
.led-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 4px currentColor; }
.led-on { background: #00e396; box-shadow: 0 0 6px #00e396; animation: led-breath 2s ease-in-out infinite; }
.led-warn { background: #ffb020; box-shadow: 0 0 6px #ffb020; }
.led-critical { background: #ff4d5e; box-shadow: 0 0 6px #ff4d5e; animation: led-breath 0.8s ease-in-out infinite; }
.led-off { background: #4a5a70; }
@keyframes led-breath { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.legend-tip { margin-left: auto; font-size: 12px; color: var(--text-sub, #7d92b5); opacity: 0.75; }

.bigscreen-bar {
  display: flex; align-items: center; gap: 32px; padding: 14px 24px; margin-bottom: 16px;
  border: 1px solid rgba(0, 212, 255, 0.3); border-radius: 8px;
  background: linear-gradient(135deg, rgba(0, 212, 255, 0.06), rgba(47, 123, 255, 0.04));
}
.bs-stat { display: flex; align-items: baseline; gap: 6px; }
.bs-num { font-size: 28px; font-weight: 700; color: #00d4ff; font-family: 'Courier New', monospace; }
.bs-lbl { font-size: 13px; color: var(--text-sub, #7d92b5); }
.bs-clock { margin-left: auto; font-size: 16px; font-family: 'Courier New', monospace; color: #00d4ff; letter-spacing: 1px; }
.slide-down-enter-active, .slide-down-leave-active { transition: all 0.3s; }
.slide-down-enter-from, .slide-down-leave-to { opacity: 0; transform: translateY(-10px); }

.rack-list-section { margin-top: 20px; }
.section-title { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 600; margin-bottom: 12px; padding-left: 10px; border-left: 3px solid #00d4ff; }

.u-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.dev-table-title { margin: 16px 0 8px; font-size: 13px; font-weight: 600; }
.st-tag { display: inline-block; padding: 1px 7px; font-size: 11px; border: 1px solid; border-radius: 10px; line-height: 16px; }
.form-tip { margin-left: 12px; font-size: 12px; color: var(--text-sub, #7d92b5); }
.import-tip { font-size: 12px; color: var(--text-sub, #7d92b5); line-height: 1.8; padding: 10px 12px; border: 1px solid var(--border-tech, #1a2a44); border-radius: 6px; background: rgba(47, 123, 255, 0.04); }
.import-errors { margin-top: 10px; max-height: 140px; overflow-y: auto; padding: 8px 12px; border: 1px solid rgba(245, 108, 108, 0.4); border-radius: 6px; background: rgba(245, 108, 108, 0.06); color: #f56c6c; font-size: 12px; line-height: 1.8; }

/* 3D单机柜详情视图 */
.detail-3d-scene { background: linear-gradient(180deg, #060d1a, #03060e); border: 1px solid var(--border-tech, #1a2a44); border-radius: 8px; padding: 20px; margin-bottom: 16px; overflow-x: auto; }
.detail-3d-rack { position: relative; margin: 0 auto; width: 260px; padding: 0 0 0 40px; }
.d-frame-top { height: 12px; background: linear-gradient(180deg, #3a4a66, #1e2a42); border: 1px solid rgba(0, 212, 255, 0.3); border-radius: 2px 2px 0 0; }
.d-frame-bottom { height: 12px; background: linear-gradient(180deg, #1e2a42, #0a1424); border: 1px solid rgba(47, 123, 255, 0.2); border-radius: 0 0 2px 2px; }
.d-frame-left { position: absolute; left: 0; top: 12px; bottom: 12px; width: 40px; background: linear-gradient(90deg, #0a1424, #060a14); border: 1px solid rgba(47, 123, 255, 0.2); border-right: none; }
.d-frame-right { position: absolute; right: 0; top: 12px; bottom: 12px; width: 8px; background: linear-gradient(90deg, #060a14, #0a1424); border: 1px solid rgba(47, 123, 255, 0.2); border-left: none; }
.d-u-scale { position: absolute; right: 4px; top: 0; display: flex; flex-direction: column; align-items: flex-end; }
.d-u-num { height: 28px; line-height: 28px; font-size: 9px; color: var(--text-sub, #7d92b5); font-family: 'Courier New', monospace; opacity: 0.6; }
.d-device-area { position: relative; background: repeating-linear-gradient(180deg, rgba(125, 146, 181, 0.05) 0, rgba(125, 146, 181, 0.05) 1px, transparent 1px, transparent 28px), linear-gradient(180deg, #0d1830, #060d1a); border-left: 1px solid rgba(47, 123, 255, 0.15); border-right: 1px solid rgba(47, 123, 255, 0.15); }
.d-u-grid { position: absolute; left: 0; right: 0; border-bottom: 1px dashed rgba(125, 146, 181, 0.15); cursor: pointer; }
.d-u-grid:hover { background: rgba(47, 123, 255, 0.08); }
.d-device { position: absolute; left: 2px; right: 2px; border-radius: 2px; cursor: pointer; overflow: hidden; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.1); transition: filter 0.15s, transform 0.15s; }
.d-device:hover { filter: brightness(1.25); transform: translateX(2px); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.15), 0 0 10px rgba(0, 212, 255, 0.2); }
.d-dev-panel { height: 100%; display: flex; align-items: center; gap: 8px; padding: 0 10px; }
.d-dev-leds { display: flex; align-items: center; gap: 3px; flex-shrink: 0; }
.d-led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.d-led-small { width: 4px; height: 4px; background: #2a3a50; }
.d-led.led-on { background: #00e396; box-shadow: 0 0 4px #00e396; animation: led-breath 2s ease-in-out infinite; }
.d-led.led-warn { background: #ffb020; box-shadow: 0 0 4px #ffb020; }
.d-led.led-critical { background: #ff4d5e; box-shadow: 0 0 4px #ff4d5e; animation: led-breath 0.6s ease-in-out infinite; }
.d-led.led-off { background: #4a5a70; }
.d-dev-info { flex: 1; overflow: hidden; }
.d-dev-name { font-size: 12px; font-weight: 600; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 1px 2px rgba(0,0,0,0.5); }
.d-dev-sub { font-size: 10px; color: rgba(255, 255, 255, 0.7); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.d-vents { display: flex; gap: 3px; flex-shrink: 0; }
.d-vents i { width: 12px; height: 3px; background: rgba(0, 0, 0, 0.4); border-radius: 1px; display: inline-block; }
.d-ports { display: flex; gap: 2px; flex-shrink: 0; }
.d-ports i { width: 8px; height: 6px; background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 1px; display: inline-block; }

.dev-server { background: linear-gradient(135deg, #2f7bff, #1a4db8); border-color: rgba(47, 123, 255, 0.6); }
.dev-switch { background: linear-gradient(135deg, #00c48f, #008a60); border-color: rgba(0, 196, 143, 0.6); }
.dev-storage { background: linear-gradient(135deg, #ff9f43, #cc6b00); border-color: rgba(255, 159, 67, 0.6); }
.dev-security { background: linear-gradient(135deg, #a55eea, #6c3fb8); border-color: rgba(165, 94, 234, 0.6); }
.dev-other { background: linear-gradient(135deg, #5d7092, #3a4a66); border-color: rgba(93, 112, 146, 0.6); }
</style>
