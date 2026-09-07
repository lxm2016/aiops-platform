<template>
  <div v-loading="loading">
    <div class="page-header">
      <div class="page-title">机柜管理</div>
      <div style="display: flex; gap: 10px">
        <el-button type="primary" :icon="Plus" @click="openRackDialog()">添加机柜</el-button>
        <el-button :icon="Upload" @click="openImport">Excel导入设备</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 图例 -->
    <div class="legend">
      <span v-for="(label, t) in TYPE_MAP" :key="t" class="legend-item">
        <i class="legend-dot" :class="'t-' + t"></i>{{ label }}
      </span>
      <span class="legend-tip">点击机柜查看U位详情 · 同一U位支持前后侧各放一台设备</span>
    </div>

    <el-empty v-if="!rows.length" description="暂无机柜，请先添加机柜或通过Excel导入设备" :image-size="80" />

    <!-- 按列分组 -->
    <div v-for="row in rows" :key="row.name" class="row-section">
      <div class="row-title">
        <el-icon color="#00d4ff"><Grid /></el-icon>
        {{ row.name }}
        <span class="row-count">{{ row.racks.length }} 个机柜</span>
      </div>
      <div class="rack-grid">
        <div v-for="rack in row.racks" :key="rack.id" class="rack-3d-wrap">
          <div class="rack-3d" @click="openRackDetail(rack)">
            <div class="rack-cube">
              <div class="face top"></div>
              <div class="face side"></div>
              <div class="face front">
                <div class="rack-name-bar">{{ rack.name }}</div>
                <div class="mini-u">
                  <div
                    v-for="d in frontDevices(rack.id)"
                    :key="d.id"
                    class="mini-dev"
                    :class="'t-' + d.device_type"
                    :style="miniStyle(rack, d)"
                    :title="`${d.name} (${d.u_start}-${d.u_start + d.u_size - 1}U)`"
                  ></div>
                </div>
                <div class="rack-bottom-bar">{{ rack.u_height }}U</div>
              </div>
            </div>
          </div>
          <div class="rack-actions">
            <div class="rack-label">
              {{ rack.name }} · {{ rack.u_height }}U · {{ (devicesMap[rack.id] || []).length }}台设备
            </div>
            <div>
              <el-button link type="primary" size="small" @click.stop="openRackDetail(rack)">详情</el-button>
              <el-button link size="small" @click.stop="openRackDialog(rack)">编辑</el-button>
              <el-popconfirm title="删除机柜将同时删除其中所有设备，确定？" @confirm="deleteRack(rack)">
                <template #reference>
                  <el-button link type="danger" size="small" @click.stop>删除</el-button>
                </template>
              </el-popconfirm>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 机柜详情抽屉: U位图 -->
    <el-drawer v-model="detailVisible" :title="`机柜 ${detailRack?.name || ''} · ${detailRack?.u_height || 0}U`" size="640px">
      <template v-if="detailRack">
        <div class="u-toolbar">
          <el-radio-group v-model="detailSide" size="small">
            <el-radio-button value="front">前侧</el-radio-button>
            <el-radio-button value="back">后侧</el-radio-button>
          </el-radio-group>
          <el-button size="small" type="primary" :icon="Plus" @click="openDeviceDialog()">添加设备</el-button>
        </div>

        <div class="u-scroll">
          <div class="u-body" :style="{ height: detailRack.u_height * U_ROW + 'px' }">
            <div
              v-for="u in descUnits"
              :key="u"
              class="u-row"
              :style="{ top: (detailRack.u_height - u) * U_ROW + 'px', height: U_ROW + 'px' }"
              @click="openDeviceDialog(null, u)"
            >
              <span class="u-num">{{ u }}</span>
            </div>
            <div
              v-for="d in sideDevices"
              :key="d.id"
              class="u-dev"
              :class="'t-' + d.device_type"
              :style="devStyle(d)"
              @click.stop="openDeviceDialog(d)"
            >
              <span class="u-dev-name">{{ d.name }}</span>
              <span class="u-dev-info">
                {{ TYPE_MAP[d.device_type] || d.device_type }} · {{ d.u_start }}-{{ d.u_start + d.u_size - 1 }}U
                <template v-if="d.remark"> · {{ d.remark }}</template>
              </span>
            </div>
          </div>
        </div>

        <!-- 设备清单 -->
        <div class="dev-table-title">设备清单（前后侧全部）</div>
        <el-table :data="detailDevices" size="small" border max-height="260">
          <el-table-column prop="name" label="设备名称" min-width="120" />
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="dark" :color="TYPE_COLOR[row.device_type]" style="border: none">
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
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".xlsx,.xls"
          :on-change="handleFileChange"
        >
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
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Upload, Download, Grid } from '@element-plus/icons-vue'
import * as XLSX from 'xlsx'
import { rackApi } from '@/api'

const U_ROW = 26 // U位图每行像素高度

const TYPE_MAP = { server: '服务器', switch: '交换机', storage: '存储', security: '安全设备', other: '其他' }
const TYPE_COLOR = {
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

const loading = ref(false)
const submitting = ref(false)
const racks = ref([])
const devicesMap = ref({}) // rackId -> devices

// ---------- 数据加载 ----------
async function load() {
  loading.value = true
  try {
    racks.value = await rackApi.list()
    const entries = await Promise.all(
      racks.value.map(async (r) => [r.id, await rackApi.devices(r.id)])
    )
    devicesMap.value = Object.fromEntries(entries)
  } finally {
    loading.value = false
  }
}

const rows = computed(() => {
  const map = {}
  for (const r of racks.value) {
    const key = r.row_name || 'A'
    ;(map[key] = map[key] || []).push(r)
  }
  return Object.keys(map)
    .sort()
    .map((name) => ({ name, racks: map[name].sort((a, b) => a.name.localeCompare(b.name)) }))
})

function frontDevices(rackId) {
  return (devicesMap.value[rackId] || []).filter((d) => d.side === 'front')
}

function miniStyle(rack, d) {
  const top = ((rack.u_height - (d.u_start + d.u_size - 1)) / rack.u_height) * 100
  const height = (d.u_size / rack.u_height) * 100
  return { top: top + '%', height: `calc(${height}% - 1px)` }
}

// ---------- 机柜增删改 ----------
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

// ---------- 机柜详情 / 设备增删改 ----------
const detailVisible = ref(false)
const detailRack = ref(null)
const detailSide = ref('front')
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

function devStyle(d) {
  const top = (detailRack.value.u_height - (d.u_start + d.u_size - 1)) * U_ROW
  return { top: top + 'px', height: d.u_size * U_ROW - 2 + 'px' }
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

// ---------- Excel导入 ----------
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
    { '机柜编号': 'A01', '设备名称': '数据库服务器-1', '设备类型': '服务器', '起始U位': 12, '占用U数': 2, '放置侧': '前', '备注': '' },
    { '机柜编号': 'A01', '设备名称': '后端配线架', '设备类型': '其他', '起始U位': 12, '占用U数': 2, '放置侧': '后', '备注': '与前侧设备同U位' }
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
      ElMessage.warning(`成功导入 ${res.success} 条，${res.errors.length} 条失败，详见错误列表`)
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
</script>

<style scoped>
.legend {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding: 10px 14px;
  border: 1px solid var(--border-tech);
  border-radius: 6px;
  background: rgba(47, 123, 255, 0.04);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-sub);
}

.legend-dot {
  width: 12px;
  height: 12px;
  border-radius: 2px;
  display: inline-block;
}

.legend-tip {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-sub);
  opacity: 0.75;
}

.row-section {
  margin-bottom: 28px;
}

.row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 14px;
  padding-left: 10px;
  border-left: 3px solid #00d4ff;
}

.row-count {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-sub);
}

.rack-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 34px;
}

/* ---------- 3D机柜 ---------- */
.rack-3d-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.rack-3d {
  width: 170px;
  height: 280px;
  perspective: 900px;
  cursor: pointer;
}

.rack-cube {
  position: relative;
  width: 100%;
  height: 100%;
  transform-style: preserve-3d;
  transform: rotateX(-6deg) rotateY(-18deg);
  transition: transform 0.3s;
}

.rack-3d:hover .rack-cube {
  transform: rotateX(-4deg) rotateY(-8deg) scale(1.03);
}

.face {
  position: absolute;
  backface-visibility: hidden;
}

.face.front {
  width: 170px;
  height: 280px;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #16233b, #0c1524);
  border: 1px solid rgba(47, 123, 255, 0.45);
  box-shadow: inset 0 0 24px rgba(0, 212, 255, 0.06);
}

.face.side {
  width: 34px;
  height: 280px;
  left: 170px;
  top: 0;
  background: linear-gradient(90deg, #0a1322, #060c16);
  border: 1px solid rgba(47, 123, 255, 0.2);
  transform-origin: left center;
  transform: rotateY(90deg);
}

.face.top {
  width: 170px;
  height: 34px;
  left: 0;
  top: 0;
  background: linear-gradient(180deg, #22385c, #16253f);
  border: 1px solid rgba(47, 123, 255, 0.3);
  transform-origin: center top;
  transform: rotateX(-90deg);
}

.rack-name-bar {
  height: 22px;
  line-height: 22px;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  color: #00d4ff;
  background: rgba(0, 212, 255, 0.08);
  border-bottom: 1px solid rgba(47, 123, 255, 0.3);
}

.rack-bottom-bar {
  height: 16px;
  line-height: 16px;
  text-align: center;
  font-size: 10px;
  color: var(--text-sub);
  border-top: 1px solid rgba(47, 123, 255, 0.3);
}

.mini-u {
  flex: 1;
  position: relative;
  margin: 3px 6px;
  background: repeating-linear-gradient(
    180deg,
    rgba(125, 146, 181, 0.08) 0,
    rgba(125, 146, 181, 0.08) 1px,
    transparent 1px,
    transparent 6px
  );
}

.mini-dev {
  position: absolute;
  left: 1px;
  right: 1px;
  border-radius: 1px;
  opacity: 0.92;
}

.rack-actions {
  width: 170px;
  text-align: center;
}

.rack-label {
  font-size: 12px;
  color: var(--text-sub);
  margin-bottom: 2px;
}

/* ---------- 类型颜色 ---------- */
.t-server { background: #2f7bff; }
.t-switch { background: #00c48f; }
.t-storage { background: #ff9f43; }
.t-security { background: #a55eea; }
.t-other { background: #5d7092; }

/* ---------- U位图 ---------- */
.u-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.u-scroll {
  max-height: 520px;
  overflow-y: auto;
  border: 1px solid var(--border-tech);
  border-radius: 6px;
  padding: 8px;
  background: rgba(10, 18, 32, 0.5);
}

.u-body {
  position: relative;
  margin-left: 40px;
  margin-right: 4px;
}

.u-row {
  position: absolute;
  left: 0;
  right: 0;
  border-bottom: 1px dashed rgba(125, 146, 181, 0.18);
  cursor: pointer;
}

.u-row:hover {
  background: rgba(47, 123, 255, 0.08);
}

.u-num {
  position: absolute;
  left: -36px;
  top: 50%;
  transform: translateY(-50%);
  width: 30px;
  text-align: right;
  font-size: 11px;
  color: var(--text-sub);
}

.u-dev {
  position: absolute;
  left: 0;
  right: 0;
  border-radius: 3px;
  padding: 2px 8px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
  transition: filter 0.15s;
}

.u-dev:hover {
  filter: brightness(1.2);
}

.u-dev-name {
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.u-dev-info {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.75);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dev-table-title {
  margin: 16px 0 8px;
  font-size: 13px;
  font-weight: 600;
}

.form-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--text-sub);
}

.import-tip {
  font-size: 12px;
  color: var(--text-sub);
  line-height: 1.8;
  padding: 10px 12px;
  border: 1px solid var(--border-tech);
  border-radius: 6px;
  background: rgba(47, 123, 255, 0.04);
}

.import-errors {
  margin-top: 10px;
  max-height: 140px;
  overflow-y: auto;
  padding: 8px 12px;
  border: 1px solid rgba(245, 108, 108, 0.4);
  border-radius: 6px;
  background: rgba(245, 108, 108, 0.06);
  color: #f56c6c;
  font-size: 12px;
  line-height: 1.8;
}
</style>
