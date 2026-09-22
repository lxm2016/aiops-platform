<template>
  <div>
    <div class="page-header">
      <div class="page-title">网络设备</div>
      <div style="display: flex; gap: 10px">
        <el-button type="primary" :icon="Plus" @click="resetForm(); dialogVisible = true">添加设备</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <el-card class="tech-card" shadow="never">
      <el-table :data="devices" v-loading="loading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="名称" min-width="140">
          <template #default="{ row }">
            <el-button link type="primary" @click="goDetail(row)">
              {{ row.name || row.ip }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="IP地址" width="140">
          <template #default="{ row }">
            <el-button link type="primary" @click="goDetail(row)">
              {{ row.ip }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column prop="vendor" label="厂商" width="100" />
        <el-table-column label="类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ typeLabel(row.device_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="型号" min-width="140" show-overflow-tooltip />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="端口 (UP/总)" width="110" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="openPorts(row)">
              <span class="num-highlight" style="color: #00e396">{{ row.port_up }}</span>
              <span style="color: var(--text-sub)"> / {{ row.port_total }}</span>
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="最后采集" width="170">
          <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button
              link
              type="primary"
              :loading="pollingId === row.id"
              @click="handlePoll(row)"
            >
              SNMP轮询
            </el-button>
            <el-popconfirm title="确定删除该设备？" @confirm="handleDelete(row)">
              <template #reference>
                <el-button link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑网络设备' : '添加网络设备'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="设备名称" />
        </el-form-item>
        <el-form-item label="IP地址" prop="ip">
          <el-input v-model="form.ip" />
        </el-form-item>
        <el-form-item label="厂商">
          <el-input v-model="form.vendor" placeholder="如 Huawei / H3C / Cisco" />
        </el-form-item>
        <el-form-item label="设备类型">
          <el-select v-model="form.device_type" style="width: 100%">
            <el-option label="交换机" value="switch" />
            <el-option label="路由器" value="router" />
            <el-option label="防火墙" value="firewall" />
            <el-option label="负载均衡" value="loadbalancer" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="型号">
          <el-input v-model="form.model" placeholder="设备型号" />
        </el-form-item>
        <el-form-item label="SNMP版本">
          <el-select v-model="form.snmp_version" style="width: 100%">
            <el-option label="v2c" value="2c" />
            <el-option label="v1" value="1" />
          </el-select>
        </el-form-item>
        <el-form-item label="SNMP Community">
          <el-input v-model="form.snmp_community" placeholder="默认 public" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 端口明细对话框 -->
    <el-dialog
      v-model="portsVisible"
      :title="`端口明细 - ${portsDevice?.name || ''}`"
      width="900px"
      top="6vh"
    >
      <div style="margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center">
        <span style="color: var(--text-sub); font-size: 12px">
          物理口 {{ ports.filter((p) => p.physical).length }} 个
          (UP {{ ports.filter((p) => p.physical && p.status === 'up').length }})，
          虚拟口(VLAN等) {{ ports.filter((p) => !p.physical).length }} 个；
          备注用于记录端口去向/用途，点"保存"生效
        </span>
        <el-button size="small" :icon="Refresh" @click="loadPorts">刷新</el-button>
      </div>
      <el-table :data="ports" v-loading="portsLoading" max-height="520" size="small">
        <el-table-column prop="name" label="端口" width="150" show-overflow-tooltip />
        <el-table-column label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.physical ? 'primary' : 'info'" size="small" effect="plain">
              {{ row.physical ? '物理' : '虚拟' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="portTagType(row.status)" size="small" effect="dark">
              {{ portStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="速率" width="100">
          <template #default="{ row }">{{ speedLabel(row.speed_mbps) }}</template>
        </el-table-column>
        <el-table-column prop="alias" label="交换机备注(ifAlias)" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.alias || '-' }}</template>
        </el-table-column>
        <el-table-column label="用途备注" min-width="220">
          <template #default="{ row }">
            <el-input
              v-model="row.remark"
              size="small"
              placeholder="如: 接HIS服务器 / 上联核心"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :loading="testingIndex === row.port_index"
              @click="handleTestPort(row)"
            >
              测试
            </el-button>
            <el-button
              link
              type="primary"
              :loading="savingIndex === row.port_index"
              @click="handleSaveRemark(row)"
            >
              保存
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { networkApi } from '@/api'
import { formatTime, statusTagType, statusLabel } from '@/utils/format'

const router = useRouter()
const devices = ref([])
const loading = ref(false)
const pollingId = ref(null)
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)
const editingId = ref(null)

function goDetail(row) {
  router.push(`/network/${row.id}`)
}

const form = reactive({
  name: '',
  ip: '',
  vendor: '',
  device_type: 'switch',
  model: '',
  snmp_community: 'public',
  snmp_version: '2c'
})

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  ip: [
    { required: true, message: '请输入IP地址', trigger: 'blur' },
    { pattern: /^(\d{1,3}\.){3}\d{1,3}$/, message: 'IP格式不正确', trigger: 'blur' }
  ]
}

function typeLabel(t) {
  const map = {
    switch: '交换机',
    router: '路由器',
    firewall: '防火墙',
    loadbalancer: '负载均衡',
    other: '其他'
  }
  return map[t] || t
}

async function load() {
  loading.value = true
  try {
    devices.value = await networkApi.list()
  } finally {
    loading.value = false
  }
}

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    name: '', ip: '', vendor: '', device_type: 'switch',
    model: '', snmp_community: 'public', snmp_version: '2c'
  })
}

function handleEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    name: row.name, ip: row.ip, vendor: row.vendor,
    device_type: row.device_type, model: row.model,
    snmp_community: row.snmp_community, snmp_version: row.snmp_version
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  await formRef.value.validate()
  submitting.value = true
  try {
    if (editingId.value) {
      await networkApi.update(editingId.value, form)
      ElMessage.success('修改成功')
    } else {
      await networkApi.create(form)
      ElMessage.success('添加成功')
    }
    dialogVisible.value = false
    resetForm()
    load()
  } finally {
    submitting.value = false
  }
}

async function handlePoll(row) {
  pollingId.value = row.id
  try {
    const data = await networkApi.poll(row.id)
    if (data.reachable) {
      ElMessage.success(`轮询成功：CPU ${data.cpu_percent}%，内存 ${data.mem_percent}%`)
    } else {
      ElMessage.warning('设备不可达')
    }
    load()
  } finally {
    pollingId.value = null
  }
}

async function handleDelete(row) {
  await networkApi.remove(row.id)
  ElMessage.success('删除成功')
  load()
}

// ---------- 端口明细 ----------
const portsVisible = ref(false)
const portsLoading = ref(false)
const portsDevice = ref(null)
const ports = ref([])
const testingIndex = ref(null)
const savingIndex = ref(null)

function portTagType(status) {
  return status === 'up' ? 'success' : status === 'down' ? 'danger' : 'info'
}

function portStatusLabel(status) {
  const map = {
    up: 'UP', down: 'DOWN', testing: '测试中',
    unknown: '未知', dormant: '休眠', notPresent: '不存在', lowerLayerDown: '下层DOWN'
  }
  return map[status] || status
}

function speedLabel(mbps) {
  if (!mbps) return '-'
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(0)}G`
  return `${mbps}M`
}

async function openPorts(row) {
  portsDevice.value = row
  portsVisible.value = true
  await loadPorts()
}

async function loadPorts() {
  portsLoading.value = true
  try {
    ports.value = await networkApi.ports(portsDevice.value.id)
  } finally {
    portsLoading.value = false
  }
}

async function handleTestPort(row) {
  testingIndex.value = row.port_index
  try {
    const res = await networkApi.testPort(portsDevice.value.id, row.port_index)
    if (res.ok) {
      row.status = res.status
      ElMessage.success(`${row.name}: ${res.detail}`)
    } else {
      ElMessage.warning(res.detail)
    }
  } finally {
    testingIndex.value = null
  }
}

async function handleSaveRemark(row) {
  savingIndex.value = row.port_index
  try {
    await networkApi.updateRemark(portsDevice.value.id, row.port_index, row.remark)
    ElMessage.success(`${row.name} 备注已保存`)
  } finally {
    savingIndex.value = null
  }
}

onMounted(load)
</script>
