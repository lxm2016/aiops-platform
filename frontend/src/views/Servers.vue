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

    <el-card class="tech-card" shadow="never">
      <el-table :data="servers" v-loading="loading">
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
            <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后上报" width="170">
          <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="$router.push(`/servers/${row.id}`)">
              详情
            </el-button>
            <el-popconfirm title="确定删除该服务器？" @confirm="handleDelete(row)">
              <template #reference>
                <el-button link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 添加对话框 -->
    <el-dialog v-model="dialogVisible" title="添加服务器" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="服务器名称" />
        </el-form-item>
        <el-form-item label="IP地址" prop="ip">
          <el-input v-model="form.ip" placeholder="如 192.168.1.10" />
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
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { serverApi } from '@/api'
import { formatTime, statusTagType, statusLabel } from '@/utils/format'

const servers = ref([])
const loading = ref(false)
const keyword = ref('')
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)

const form = reactive({
  name: '',
  ip: '',
  os_type: 'linux',
  os_distro: '',
  os_version: '',
  tags: ''
})

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
    servers.value = await serverApi.list(keyword.value ? { keyword: keyword.value } : {})
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  await formRef.value.validate()
  submitting.value = true
  try {
    await serverApi.create(form)
    ElMessage.success('添加成功')
    dialogVisible.value = false
    Object.assign(form, { name: '', ip: '', os_type: 'linux', os_distro: '', os_version: '', tags: '' })
    load()
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  await serverApi.remove(row.id)
  ElMessage.success('删除成功')
  load()
}

onMounted(load)
</script>
