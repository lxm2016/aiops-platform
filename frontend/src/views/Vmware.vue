<template>
  <div>
    <div class="page-header">
      <div class="page-title">VMware 管理</div>
      <div style="display: flex; gap: 10px">
        <el-button type="primary" :icon="Plus" @click="dialogVisible = true">添加 vCenter</el-button>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <!-- vCenter 列表 -->
    <el-card class="tech-card" shadow="never">
      <template #header>vCenter / ESXi 主机</template>
      <el-table :data="hosts" v-loading="loadingHosts">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="host" label="地址" min-width="160" />
        <el-table-column prop="port" label="端口" width="80" align="center" />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后同步" width="170">
          <template #default="{ row }">{{ formatTime(row.last_sync) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :loading="syncingId === row.id"
              @click="handleSync(row)"
            >
              同步VM
            </el-button>
            <el-popconfirm title="确定删除该主机？" @confirm="handleDelete(row)">
              <template #reference>
                <el-button link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- VM 列表 -->
    <el-card class="tech-card" shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>虚拟机列表</span>
          <el-select
            v-model="filterHostId"
            placeholder="按主机筛选"
            clearable
            style="width: 200px"
            size="small"
          >
            <el-option v-for="h in hosts" :key="h.id" :label="h.name" :value="h.id" />
          </el-select>
        </div>
      </template>
      <el-table :data="filteredVms" v-loading="loadingVms">
        <el-table-column prop="name" label="虚拟机名称" min-width="160" show-overflow-tooltip />
        <el-table-column label="电源状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag
              :type="row.power_state === 'poweredOn' ? 'success' : 'info'"
              size="small"
              effect="dark"
            >
              {{ row.power_state === 'poweredOn' ? '运行中' : row.power_state === 'poweredOff' ? '已关机' : row.power_state }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="guest_os" label="客户机OS" min-width="160" show-overflow-tooltip />
        <el-table-column prop="ip" label="IP地址" width="140" />
        <el-table-column prop="cpu_cores" label="vCPU" width="80" align="center" />
        <el-table-column label="内存" width="90" align="center">
          <template #default="{ row }">{{ (row.mem_mb / 1024).toFixed(1) }} GB</template>
        </el-table-column>
        <el-table-column label="CPU%" width="130">
          <template #default="{ row }">
            <el-progress :percentage="Math.min(100, row.cpu_percent)" :color="percentColor(row.cpu_percent)" :stroke-width="8" />
          </template>
        </el-table-column>
        <el-table-column label="内存%" width="130">
          <template #default="{ row }">
            <el-progress :percentage="Math.min(100, row.mem_percent)" :color="percentColor(row.mem_percent)" :stroke-width="8" />
          </template>
        </el-table-column>
        <el-table-column prop="uptime" label="运行时长" width="120" show-overflow-tooltip />
        <el-table-column label="所属主机" width="140">
          <template #default="{ row }">{{ hostName(row.host_id) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 添加对话框 -->
    <el-dialog v-model="dialogVisible" title="添加 vCenter / ESXi" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 生产vCenter" />
        </el-form-item>
        <el-form-item label="地址" prop="host">
          <el-input v-model="form.host" placeholder="IP或域名" />
        </el-form-item>
        <el-form-item label="端口" prop="port">
          <el-input-number v-model="form.port" :min="1" :max="65535" style="width: 100%" />
        </el-form-item>
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="如 administrator@vsphere.local" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { vmwareApi } from '@/api'
import { formatTime, statusTagType, statusLabel, percentColor } from '@/utils/format'

const hosts = ref([])
const vms = ref([])
const loadingHosts = ref(false)
const loadingVms = ref(false)
const syncingId = ref(null)
const filterHostId = ref(null)
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)

const form = reactive({
  name: '',
  host: '',
  port: 443,
  username: '',
  password: ''
})

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  host: [{ required: true, message: '请输入地址', trigger: 'blur' }]
}

const filteredVms = computed(() =>
  filterHostId.value ? vms.value.filter((v) => v.host_id === filterHostId.value) : vms.value
)

function hostName(id) {
  return hosts.value.find((h) => h.id === id)?.name || id
}

async function loadHosts() {
  loadingHosts.value = true
  try {
    hosts.value = await vmwareApi.listHosts()
  } finally {
    loadingHosts.value = false
  }
}

async function loadVms() {
  loadingVms.value = true
  try {
    vms.value = await vmwareApi.listVms()
  } finally {
    loadingVms.value = false
  }
}

function loadAll() {
  loadHosts()
  loadVms()
}

async function handleCreate() {
  await formRef.value.validate()
  submitting.value = true
  try {
    await vmwareApi.addHost(form)
    ElMessage.success('添加成功')
    dialogVisible.value = false
    Object.assign(form, { name: '', host: '', port: 443, username: '', password: '' })
    loadHosts()
  } finally {
    submitting.value = false
  }
}

async function handleSync(row) {
  syncingId.value = row.id
  try {
    const res = await vmwareApi.syncHost(row.id)
    ElMessage.success(`同步完成，共 ${res.vm_count} 台虚拟机`)
    loadAll()
  } catch (e) {
    loadHosts()
  } finally {
    syncingId.value = null
  }
}

async function handleDelete(row) {
  await vmwareApi.deleteHost(row.id)
  ElMessage.success('删除成功')
  loadAll()
}

onMounted(loadAll)
</script>
