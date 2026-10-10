<template>
  <div>
    <div class="page-header">
      <div class="page-title">服务器管理</div>
      <div style="display: flex; gap: 10px">
        <el-input
          v-model="keyword"
          placeholder="搜索名称 / IP"
          clearable
          style="width: 200px"
          :prefix-icon="Search"
          @keyup.enter="load"
          @clear="load"
        />
        <el-button type="primary" :icon="Plus" @click="dialogVisible = true">添加服务器</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div v-if="selectedRows.length" class="batch-bar">
      <span class="batch-info">已选 <b>{{ selectedRows.length }}</b> 台</span>
      <el-button size="small" type="warning" :loading="batching" @click="batchSet(false)">批量停用监测</el-button>
      <el-button size="small" type="success" :loading="batching" @click="batchSet(true)">批量启用监测</el-button>
      <el-button size="small" text @click="clearSelection">取消选择</el-button>
    </div>

    <el-card class="tech-card" shadow="never">
      <el-table
        ref="tableRef"
        :data="servers"
        v-loading="loading"
        row-key="id"
        @selection-change="(rows) => (selectedRows = rows)"
      >
        <el-table-column type="selection" width="50" />
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="名称" min-width="140">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push(`/servers/${row.id}`)">
              {{ row.name }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="IP地址" width="140" />
        <el-table-column label="操作系统" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag size="small" :type="row.os_type === 'linux' ? 'success' : 'primary'" effect="plain">
              {{ row.os_type === 'linux' ? 'Linux' : 'Windows' }}
            </el-tag>
            <span style="margin-left: 6px">{{ row.os_distro }} {{ row.os_version }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="cpu_cores" label="CPU核数" width="90" align="center" />
        <el-table-column label="内存" width="90" align="center">
          <template #default="{ row }">{{ row.mem_total_gb }} GB</template>
        </el-table-column>
        <el-table-column label="Agent" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.agent_installed ? 'success' : 'info'" size="small">
              {{ row.agent_installed ? '已安装' : '未安装' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="!row.enabled" type="info" size="small" effect="plain">已暂停</el-tag>
            <el-tag v-else :type="statusTagType(row.status)" size="small" effect="dark">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="监测" width="110" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              :loading="toggling[row.id]"
              inline-prompt
              active-text="监测"
              inactive-text="停用"
              @change="(val) => setMonitor(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="最后上报" width="170">
          <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="$router.push(`/servers/${row.id}`)">
              详情
            </el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-popconfirm title="确定删除该服务器？" @confirm="handleDelete(row)">
              <template #reference>
                <el-button link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 添加 / 编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑服务器' : '添加服务器'" width="500px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="服务器名称" />
        </el-form-item>
        <el-form-item label="IP地址" prop="ip">
          <el-input v-model="form.ip" :disabled="!!editingId" />
        </el-form-item>
        <el-form-item label="系统类型" prop="os_type">
          <el-select v-model="form.os_type" style="width: 100%">
            <el-option label="Linux" value="linux" />
            <el-option label="Windows" value="windows" />
          </el-select>
        </el-form-item>
        <el-form-item label="发行版">
          <el-input v-model="form.os_distro" placeholder="如 Ubuntu / CentOS" />
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="form.os_version" placeholder="如 22.04" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="多个标签用逗号分隔" />
        </el-form-item>

        <el-divider content-position="left">只读诊断凭据（AI 连服务器排查用）</el-divider>
        <el-form-item label="登录用户">
          <el-input v-model="form.diag_user" placeholder="SSH / WinRM 用户名" />
        </el-form-item>
        <el-form-item label="登录密码">
          <el-input
            v-model="form.diag_password"
            type="password"
            show-password
            placeholder="SSH / WinRM 密码（留空表示不修改）"
          />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.diag_port" :min="1" :max="65535" />
          <div class="form-tip">Linux SSH 默认 22；Windows WinRM 默认 5985</div>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        :closable="false"
        style="margin-bottom: 10px"
      >
        <template #title>
          {{ testResult.ok ? '✓ ' : '✗ ' }}{{ testResult.detail }}
          <span v-if="testResult.ok && testResult.latency_ms != null">（{{ testResult.latency_ms }}ms）</span>
        </template>
      </el-alert>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button :loading="testing" @click="handleTestConn">测试连接</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: rgba(47, 123, 255, 0.06);
  border: 1px solid var(--border-tech);
  border-radius: 6px;
}
.batch-bar .batch-info {
  color: var(--text-sub);
  font-size: 13px;
}
.batch-bar .batch-info b {
  color: var(--accent, #00d4ff);
}
</style>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { serverApi } from '@/api'
import { formatTime, statusTagType, statusLabel } from '@/utils/format'

const route = useRoute()
const servers = ref([])
const loading = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const formRef = ref(null)
const testing = ref(false)
const testResult = ref(null)
const toggling = reactive({})
const tableRef = ref(null)
const selectedRows = ref([])
const batching = ref(false)
const filterStatus = ref('')
const filterEnabled = ref(null)

const EMPTY_FORM = {
  name: '',
  ip: '',
  os_type: 'linux',
  os_distro: '',
  os_version: '',
  tags: '',
  enabled: true,
  diag_user: '',
  diag_password: '',
  diag_port: 22,
}
const form = reactive({ ...EMPTY_FORM })

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  ip: [
    { required: true, message: '请输入IP地址', trigger: 'blur' },
    {
      pattern: /^(\d{1,3}\.){3}\d{1,3}$/,
      message: 'IP格式不正确',
      trigger: 'blur'
    }
  ]
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (keyword.value) params.keyword = keyword.value
    if (filterStatus.value) params.status = filterStatus.value
    if (filterEnabled.value !== null) params.enabled = filterEnabled.value
    servers.value = await serverApi.list(params)
  } finally {
    loading.value = false
  }
}

// 让 Dashboard 的"在线/离线/已暂停"卡片点击能带着筛选条件跳进来
function applyQuery() {
  filterStatus.value = route.query.status || ''
  const en = route.query.enabled
  filterEnabled.value = en === undefined ? null : en === 'true'
  load()
}

// 批量暂停/恢复监测: 一次调用后端 /batch-enabled, 并更新本地行状态
async function batchSet(enabled) {
  const ids = selectedRows.value.map((r) => r.id)
  if (!ids.length) return
  batching.value = true
  try {
    const res = await serverApi.batchSetEnabled(ids, enabled)
    const set = new Set(ids)
    servers.value.forEach((s) => {
      if (set.has(s.id)) s.enabled = enabled
    })
    ElMessage.success(`已${enabled ? '启用' : '停用'} ${res.updated} 台服务器监测`)
    clearSelection()
  } catch (e) {
    ElMessage.error('批量操作失败: ' + ((e && e.message) || '未知错误'))
  } finally {
    batching.value = false
  }
}

function clearSelection() {
  tableRef.value?.clearSelection()
  selectedRows.value = []
}

async function handleSubmit() {
  await formRef.value.validate()
  submitting.value = true
  try {
    if (editingId.value) {
      // 编辑：密码留空则不要覆盖原值
      const payload = { ...form }
      if (!payload.diag_password) delete payload.diag_password
      await serverApi.update(editingId.value, payload)
      ElMessage.success('保存成功')
    } else {
      await serverApi.create(form)
      ElMessage.success('添加成功')
    }
    dialogVisible.value = false
    Object.assign(form, EMPTY_FORM)
    editingId.value = null
    load()
  } finally {
    submitting.value = false
  }
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, EMPTY_FORM, {
    name: row.name,
    ip: row.ip,
    os_type: row.os_type,
    os_distro: row.os_distro || '',
    os_version: row.os_version || '',
    tags: row.tags || '',
    enabled: row.enabled !== false,    // 暂停监测标记随编辑保留
    diag_user: row.diag_user || '',
    diag_password: '',                 // 出于安全不回显密码, 留空即不修改
    diag_port: row.diag_port || 22,
  })
  testResult.value = null
  dialogVisible.value = true
}

// 暂停/恢复监测: 关掉后该服务器不采集、不告警、不标离线, Agent 上报会被忽略
async function setMonitor(row, val) {
  toggling[row.id] = true
  try {
    await serverApi.setEnabled(row.id, val)
    row.enabled = val
    ElMessage.success(val ? '已恢复监测' : '已暂停监测')
  } catch (e) {
    ElMessage.error('操作失败: ' + ((e && e.message) || '未知错误'))
  } finally {
    toggling[row.id] = false
  }
}

// 测试连接：用表单里刚输入（尚未保存）的凭据直接测，密码留空则用已存密码
async function handleTestConn() {
  if (!editingId.value) {
    ElMessage.warning('新服务器请先点「确定」保存，再编辑测试连接')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const payload = {}
    if (form.diag_user) payload.diag_user = form.diag_user
    if (form.diag_password) payload.diag_password = form.diag_password
    if (form.diag_port) payload.diag_port = form.diag_port
    testResult.value = await serverApi.testConn(editingId.value, payload)
  } catch (e) {
    testResult.value = { ok: false, detail: (e && e.message) || '测试请求失败' }
  } finally {
    testing.value = false
  }
}

async function handleDelete(row) {
  await serverApi.remove(row.id)
  ElMessage.success('删除成功')
  load()
}

onMounted(applyQuery)
watch(() => route.query, applyQuery)
</script>
