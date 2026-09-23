<template>
  <div>
    <div class="page-header">
      <div class="page-title">存储设备管理</div>
      <div style="display: flex; gap: 10px">
        <el-button type="primary" :icon="Plus" @click="openCreate">添加设备</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="storage-tip">
      支持多种接入方式：<b>SNMP</b>（多数存储通用，配置community即可采集容量/磁盘）、
      <b>SMI-S</b>（华为OceanStor/EMC等，走WBEM 5988/5989端口，需存储管理账号，可采集存储池/卷/磁盘/控制器明细）、
      <b>无</b>（仅手工登记容量）。添加后点"采集"即可拉取数据，之后每5分钟自动采集。
      <br />
      <b>华为 OceanStor 特别注意</b>：这类设备<u>不暴露标准 HOST-RESOURCES-MIB</u>，
      平台已内置其私有 MIB（存储池/控制器CPU内存/硬盘温度/健康分）可直接采集；
      但设备上「SNMPv1&SNMPv2c协议开关」默认是<b>关闭</b>的——要么打开该开关用 v2c，
      要么保持关闭、用 DeviceManager 里创建的 <b>SNMPv3 USM 用户</b>（更安全，推荐）。
    </div>

    <el-row :gutter="16">
      <el-col v-for="d in devices" :key="d.id" :span="8" style="margin-bottom: 16px">
        <el-card class="tech-card storage-card" shadow="never">
          <div class="storage-head">
            <div class="storage-name">
              <el-icon color="#00d4ff"><Box /></el-icon>
              {{ d.name }}
            </div>
            <el-tag :type="statusTagType(d.status)" size="small" effect="dark">
              {{ statusLabel(d.status) }}
            </el-tag>
          </div>
          <div class="storage-meta">
            <span>{{ d.vendor || '未知厂商' }} · {{ d.model || '未知型号' }}</span>
            <span>IP: {{ d.ip }} · 协议: {{ protocolLabel(d.protocol) }}</span>
          </div>
          <div class="storage-cap">
            <div class="storage-cap-label">
              <span>容量使用</span>
              <span class="num-highlight" :style="{ color: percentColor(d.used_percent) }">
                {{ d.used_tb.toFixed(1) }} / {{ d.capacity_tb.toFixed(1) }} TB
              </span>
            </div>
            <el-progress
              :percentage="Math.min(100, Number(d.used_percent.toFixed(1)))"
              :color="percentColor(d.used_percent)"
              :stroke-width="10"
            />
          </div>
          <div class="storage-foot">
            <span class="storage-time">最后采集：{{ formatTime(d.last_seen) }}</span>
            <div>
              <el-button link type="primary" @click="openDetail(d)">明细</el-button>
              <el-button link type="primary" :loading="pollingId === d.id" @click="handlePoll(d)">采集</el-button>
              <el-button link type="primary" @click="openEdit(d)">编辑</el-button>
              <el-popconfirm title="确定删除该设备？" @confirm="handleDelete(d)">
                <template #reference>
                  <el-button link type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col v-if="!devices.length" :span="24">
        <el-empty description="暂无存储设备" :image-size="80" />
      </el-col>
    </el-row>

    <!-- 添加/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑存储设备' : '添加存储设备'" width="520px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="设备名称" />
        </el-form-item>
        <el-form-item label="IP地址" prop="ip">
          <el-input v-model="form.ip" placeholder="管理IP" />
        </el-form-item>
        <el-form-item label="厂商">
          <el-select v-model="form.vendor" filterable allow-create style="width: 100%">
            <el-option label="H3C" value="H3C" />
            <el-option label="华为" value="华为" />
            <el-option label="宏杉" value="宏杉" />
            <el-option label="EMC" value="EMC" />
            <el-option label="Dell" value="Dell" />
            <el-option label="NetApp" value="NetApp" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-form-item>
        <el-form-item label="型号">
          <el-input v-model="form.model" placeholder="设备型号" />
        </el-form-item>
        <el-form-item label="采集协议">
          <el-select v-model="form.protocol" style="width: 100%">
            <el-option label="SNMP (通用, 推荐先试)" value="snmp" />
            <el-option label="SMI-S / WBEM (华为/EMC等)" value="smi-s" />
            <el-option label="无 (仅手工登记)" value="none" />
          </el-select>
        </el-form-item>
        <template v-if="form.protocol === 'snmp'">
          <el-form-item label="SNMP版本">
            <el-select v-model="form.snmp_version" style="width: 100%">
              <el-option label="v2c" value="2c" />
              <el-option label="v1" value="1" />
              <el-option label="v3 (USM, 华为OceanStor默认只开这个)" value="3" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.snmp_version !== '3'" label="Community">
            <el-input v-model="form.snmp_community" placeholder="默认 public" />
          </el-form-item>
          <template v-else>
            <el-alert
              type="info"
              :closable="false"
              show-icon
              style="margin-bottom: 14px"
              title="华为 OceanStor 的「SNMPv1&SNMPv2c协议开关」默认是关闭的，只能走 v3"
              description="请在设备 DeviceManager → 设置 → SNMP协议 → USM用户管理里创建/查看用户，把用户名、认证密码、加密密码填到这里；上下文名称与设备上那一栏保持一致。"
            />
            <el-form-item label="USM 用户名">
              <el-input v-model="form.snmp_v3_user" placeholder="设备上创建的 SNMPv3 用户名" />
            </el-form-item>
            <el-form-item label="认证算法">
              <el-select v-model="form.snmp_v3_auth_proto" style="width: 100%">
                <el-option label="SHA (推荐)" value="sha" />
                <el-option label="MD5" value="md5" />
                <el-option label="SHA-256" value="sha256" />
              </el-select>
            </el-form-item>
            <el-form-item label="认证密码">
              <el-input v-model="form.snmp_v3_auth_pass" type="password" show-password
                        placeholder="留空 = 不认证 (noAuthNoPriv)" />
            </el-form-item>
            <el-form-item label="加密算法">
              <el-select v-model="form.snmp_v3_priv_proto" style="width: 100%">
                <el-option label="AES (推荐)" value="aes" />
                <el-option label="DES" value="des" />
                <el-option label="AES-256" value="aes256" />
              </el-select>
            </el-form-item>
            <el-form-item label="加密密码">
              <el-input v-model="form.snmp_v3_priv_pass" type="password" show-password
                        placeholder="留空 = 不加密" />
            </el-form-item>
            <el-form-item label="上下文名称">
              <el-input v-model="form.snmp_context"
                        placeholder="填设备页面上「上下文名称」的值，不确定可留空" />
            </el-form-item>
          </template>
        </template>
        <template v-if="form.protocol === 'smi-s'">
          <el-form-item label="管理账号" prop="username">
            <el-input v-model="form.username" placeholder="存储管理用户名" />
          </el-form-item>
          <el-form-item label="管理密码" prop="password">
            <el-input v-model="form.password" type="password" show-password placeholder="存储管理密码" />
          </el-form-item>
        </template>
        <el-form-item label="容量 (TB)">
          <el-input-number v-model="form.capacity_tb" :min="0" :precision="1" style="width: 100%" />
          <span class="form-hint">协议采集成功后将自动覆盖此值</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 明细对话框 -->
    <el-dialog v-model="detailVisible" :title="`存储明细 - ${detailDevice?.name || ''}`" width="860px" top="6vh">
      <template v-if="detailDevice">
        <el-tabs v-model="detailTab">
          <el-tab-pane label="存储池" name="pools">
            <el-table :data="detail.pools" size="small" border empty-text="暂无数据 (协议不支持或未采集)">
              <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
              <el-table-column prop="size_tb" label="总容量(TB)" width="110" />
              <el-table-column prop="used_tb" label="已用(TB)" width="100" />
              <el-table-column label="使用率" width="140">
                <template #default="{ row }">
                  <el-progress :percentage="Math.min(100, row.used_percent || 0)" :stroke-width="8" :color="percentColor(row.used_percent || 0)" />
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`磁盘 (${detail.disks.length})`" name="disks">
            <el-table :data="detail.disks" size="small" border empty-text="暂无数据 (协议不支持或未采集)">
              <el-table-column prop="name" label="磁盘" min-width="200" show-overflow-tooltip />
              <el-table-column prop="size_tb" label="容量(TB)" width="110" />
              <el-table-column prop="used_tb" label="已用(TB)" width="100" />
              <el-table-column label="使用率" width="140">
                <template #default="{ row }">
                  <el-progress v-if="row.used_percent != null" :percentage="Math.min(100, row.used_percent || 0)" :stroke-width="8" :color="percentColor(row.used_percent || 0)" />
                  <span v-else style="color: var(--text-sub)">-</span>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="110">
                <template #default="{ row }">
                  <el-tag v-if="row.status" :type="row.status === '正常' ? 'success' : 'danger'" size="small" effect="dark">
                    {{ row.status }}
                  </el-tag>
                  <span v-else style="color: var(--text-sub)">-</span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`控制器 (${detail.controllers.length})`" name="controllers">
            <el-table :data="detail.controllers" size="small" border empty-text="暂无数据 (协议不支持或未采集)">
              <el-table-column prop="name" label="控制器" min-width="240" show-overflow-tooltip />
              <el-table-column prop="status" label="状态" width="140">
                <template #default="{ row }">
                  <el-tag :type="row.status === '正常' ? 'success' : 'danger'" size="small" effect="dark">
                    {{ row.status }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`卷/LUN (${detail.volumes.length})`" name="volumes">
            <el-table :data="detail.volumes" size="small" border empty-text="暂无数据 (协议不支持或未采集)">
              <el-table-column prop="name" label="卷" min-width="240" show-overflow-tooltip />
              <el-table-column prop="size_tb" label="容量(TB)" width="120" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Box } from '@element-plus/icons-vue'
import { storageApi } from '@/api'
import { formatTime, statusTagType, statusLabel, percentColor } from '@/utils/format'

const devices = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)
const editingId = ref(null)
const formRef = ref(null)
const pollingId = ref(null)

const V3_DEFAULTS = {
  snmp_v3_user: '',
  snmp_v3_auth_proto: 'sha',
  snmp_v3_auth_pass: '',
  snmp_v3_priv_proto: 'aes',
  snmp_v3_priv_pass: '',
  snmp_context: ''
}

const form = reactive({
  name: '', ip: '', vendor: '', model: '',
  protocol: 'snmp', snmp_community: 'public', snmp_version: '2c',
  username: '', password: '', capacity_tb: 0,
  ...V3_DEFAULTS
})

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  ip: [{ required: true, message: '请输入IP地址', trigger: 'blur' }]
}

function protocolLabel(p) {
  return { snmp: 'SNMP', 'smi-s': 'SMI-S', none: '手工' }[p] || p
}

async function load() {
  loading.value = true
  try {
    devices.value = await storageApi.list()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    name: '', ip: '', vendor: '', model: '',
    protocol: 'snmp', snmp_community: 'public', snmp_version: '2c',
    username: '', password: '', capacity_tb: 0,
    ...V3_DEFAULTS
  })
  dialogVisible.value = true
}

function openEdit(d) {
  editingId.value = d.id
  Object.assign(form, {
    name: d.name, ip: d.ip, vendor: d.vendor, model: d.model,
    protocol: d.protocol || 'none',
    snmp_community: d.snmp_community || 'public',
    snmp_version: d.snmp_version || '2c',
    username: d.username || '', password: d.password || '',
    capacity_tb: d.capacity_tb,
    snmp_v3_user: d.snmp_v3_user || '',
    snmp_v3_auth_proto: d.snmp_v3_auth_proto || 'sha',
    snmp_v3_auth_pass: d.snmp_v3_auth_pass || '',
    snmp_v3_priv_proto: d.snmp_v3_priv_proto || 'aes',
    snmp_v3_priv_pass: d.snmp_v3_priv_pass || '',
    snmp_context: d.snmp_context || ''
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  await formRef.value.validate()
  submitting.value = true
  try {
    if (editingId.value) {
      await storageApi.update(editingId.value, form)
      ElMessage.success('更新成功')
    } else {
      await storageApi.create(form)
      ElMessage.success('添加成功')
    }
    dialogVisible.value = false
    load()
  } finally {
    submitting.value = false
  }
}

async function handlePoll(d) {
  if (d.protocol === 'none') {
    ElMessage.warning('该设备协议为"无", 请先编辑选择SNMP或SMI-S')
    return
  }
  pollingId.value = d.id
  try {
    const res = await storageApi.poll(d.id)
    if (res.ok) {
      const cap = res.data.capacity_tb
      ElMessage.success(cap ? `采集成功: 容量 ${cap} TB` : '采集成功')
    } else {
      ElMessage.error('采集失败: ' + (res.detail || '设备不可达'))
    }
    load()
  } finally {
    pollingId.value = null
  }
}

async function handleDelete(d) {
  await storageApi.remove(d.id)
  ElMessage.success('删除成功')
  load()
}

// ---------- 明细 ----------
const detailVisible = ref(false)
const detailDevice = ref(null)
const detailTab = ref('pools')

const detail = computed(() => {
  const d = detailDevice.value?.details || {}
  return {
    pools: d.pools || [],
    disks: d.disks || [],
    controllers: d.controllers || [],
    volumes: d.volumes || []
  }
})

function openDetail(d) {
  detailDevice.value = d
  detailTab.value = 'pools'
  detailVisible.value = true
}

onMounted(load)
</script>

<style scoped>
.storage-tip {
  margin-bottom: 16px;
  padding: 10px 14px;
  border: 1px solid var(--border-tech);
  border-radius: 6px;
  background: rgba(47, 123, 255, 0.04);
  font-size: 12px;
  color: var(--text-sub);
  line-height: 1.8;
}

.storage-card {
  height: 100%;
}

.storage-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.storage-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 15px;
}

.storage-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--text-sub);
  font-size: 12px;
  margin-bottom: 14px;
}

.storage-cap-label {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  color: var(--text-sub);
  margin-bottom: 6px;
}

.storage-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  border-top: 1px solid var(--border-tech);
  padding-top: 10px;
}

.storage-time {
  color: var(--text-sub);
  font-size: 12px;
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--text-sub);
}
</style>
