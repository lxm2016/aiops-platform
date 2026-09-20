<template>
  <div v-loading="loading">
    <div class="page-header">
      <div class="page-title">
        <el-button link @click="$router.push('/network')">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        网络设备详情 - {{ device?.name || '' }}
      </div>
      <div style="display: flex; gap: 10px">
        <el-button
          type="primary"
          :icon="Refresh"
          :loading="polling"
          @click="handlePoll"
        >
          立即轮询(SNMP)
        </el-button>
      </div>
    </div>

    <!-- 基本信息 -->
    <el-card class="tech-card" shadow="never">
      <template #header>基本信息</template>
      <el-descriptions :column="4" border size="small" v-if="device">
        <el-descriptions-item label="名称">{{ device.name }}</el-descriptions-item>
        <el-descriptions-item label="IP地址">{{ device.ip }}</el-descriptions-item>
        <el-descriptions-item label="厂商">{{ device.vendor || '-' }}</el-descriptions-item>
        <el-descriptions-item label="类型">
          <el-tag size="small" effect="plain">{{ typeLabel(device.device_type) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusTagType(device.status)" size="small" effect="dark">
            {{ statusLabel(device.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="SNMP版本">{{ device.snmp_version || '2c' }}</el-descriptions-item>
        <el-descriptions-item label="最后采集">{{ formatTime(device.last_seen) }}</el-descriptions-item>
        <el-descriptions-item label="型号">{{ device.model || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 实时指标 (SNMP轮询后刷新) -->
    <el-row :gutter="16" style="margin-top: 16px" v-if="device">
      <el-col :span="6">
        <StatCard
          label="CPU使用率"
          :value="device.cpu_percent?.toFixed(1) || '--'"
          :unit="device.cpu_percent > 0 ? '%' : ''"
          icon="Cpu"
          :color="percentColor(device.cpu_percent || 0)"
        />
      </el-col>
      <el-col :span="6">
        <StatCard
          label="内存使用率"
          :value="device.mem_percent?.toFixed(1) || '--'"
          :unit="device.mem_percent > 0 ? '%' : ''"
          icon="Coin"
          :color="percentColor(device.mem_percent || 0)"
        />
      </el-col>
      <el-col :span="6">
        <StatCard
          label="端口UP/总数"
          :value="device.port_up || 0"
          :unit="`/ ${device.port_total || 0}`"
          icon="Connection"
          color="#00e396"
        />
      </el-col>
      <el-col :span="6">
        <StatCard
          label="SNMP状态"
          :value="device.status === 'online' ? '在线' : '离线'"
          icon="Monitor"
          :color="device.status === 'online' ? '#00e396' : '#ff4d5e'"
        />
      </el-col>
    </el-row>

    <!-- 系统版本信息 (sysDescr) -->
    <el-card class="tech-card" shadow="never" style="margin-top: 16px" v-if="sysDescr">
      <template #header>设备系统信息 (sysDescr)</template>
      <div class="sys-descr">{{ sysDescr }}</div>
    </el-card>

    <!-- 端口明细 -->
    <el-card class="tech-card" shadow="never" style="margin-top: 16px">
      <template #header>
        端口明细
        <span style="color: var(--text-sub); font-size: 12px; font-weight: normal; margin-left: 8px">
          物理口 {{ physicalCount }} 个 (UP {{ physicalUpCount }})，虚拟口 {{ virtualCount }} 个
        </span>
      </template>
      <el-table :data="ports" v-loading="portsLoading" max-height="520" size="small">
        <el-table-column prop="name" label="端口" min-width="150" show-overflow-tooltip />
        <el-table-column label="类型" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.physical ? 'primary' : 'info'" size="small" effect="plain">
              {{ row.physical ? '物理' : '虚拟' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status === 'up' ? 'success' : row.status === 'down' ? 'danger' : 'info'" size="small" effect="dark">
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
        <el-table-column prop="remark" label="用途备注" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.remark || '-' }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, ArrowLeft } from '@element-plus/icons-vue'
import { networkApi } from '@/api'
import StatCard from '@/components/StatCard.vue'
import { formatTime, statusTagType, statusLabel, percentColor } from '@/utils/format'

const route = useRoute()
const deviceId = route.params.id

const loading = ref(false)
const polling = ref(false)
const device = ref(null)
const ports = ref([])
const portsLoading = ref(false)
const sysDescr = ref('')

function typeLabel(t) {
  const map = { switch: '交换机', router: '路由器', firewall: '防火墙', loadbalancer: '负载均衡', other: '其他' }
  return map[t] || t
}

function portStatusLabel(s) {
  const map = { up: 'UP', down: 'DOWN', testing: '测试中', unknown: '未知', dormant: '休眠', notPresent: '不存在', lowerLayerDown: '下层DOWN' }
  return map[s] || s
}

function speedLabel(mbps) {
  if (!mbps) return '-'
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(0)}G`
  return `${mbps}M`
}

const physicalCount = computed(() => ports.value.filter(p => p.physical).length)
const physicalUpCount = computed(() => ports.value.filter(p => p.physical && p.status === 'up').length)
const virtualCount = computed(() => ports.value.filter(p => !p.physical).length)

async function loadDevice() {
  const list = await networkApi.list()
  device.value = list.find(d => String(d.id) === String(deviceId)) || null
}

async function loadPorts() {
  portsLoading.value = true
  try {
    ports.value = await networkApi.ports(deviceId)
  } finally {
    portsLoading.value = false
  }
}

async function handlePoll() {
  polling.value = true
  try {
    const data = await networkApi.poll(deviceId)
    // 更新本地显示的设备数据
    if (device.value) {
      device.value.cpu_percent = data.cpu_percent
      device.value.mem_percent = data.mem_percent
      device.value.port_total = data.port_total
      device.value.port_up = data.port_up
      device.value.status = data.reachable ? 'online' : 'offline'
      device.value.last_seen = new Date().toISOString()
    }
    // 显示sysDescr
    if (data.sys_descr) {
      sysDescr.value = data.sys_descr
      if (device.value) device.value.model = data.sys_descr.substring(0, 120)
    }
    // 刷新端口
    if (data.ports && data.ports.length) {
      ports.value = data.ports
    } else {
      await loadPorts()
    }
    if (data.reachable) {
      ElMessage.success(`轮询成功：CPU ${data.cpu_percent || 0}%，内存 ${data.mem_percent || 0}%`)
    } else {
      ElMessage.warning('设备不可达')
    }
  } finally {
    polling.value = false
  }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.allSettled([loadDevice(), loadPorts()])
    // 自动轮询一次获取最新CPU/内存
    if (device.value?.status === 'online') {
      await handlePoll()
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.sys-descr {
  background: rgba(0, 212, 255, 0.06);
  border: 1px solid var(--border-tech);
  border-radius: 6px;
  padding: 12px 16px;
  font-family: 'Courier New', Consolas, monospace;
  font-size: 13px;
  color: #b8c8e0;
  line-height: 1.6;
  word-break: break-all;
  white-space: pre-wrap;
}
</style>
