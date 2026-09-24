<template>
  <div v-loading="loading">
    <div class="page-header">
      <div class="page-title">
        <el-button link @click="$router.push('/servers')">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
        服务器详情 - {{ server?.name || '' }}
      </div>
      <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
        <el-select v-model="hours" style="width: 150px" @change="handleRangeChange">
          <el-option :value="1" label="最近1小时" />
          <el-option :value="6" label="最近6小时" />
          <el-option :value="12" label="最近12小时" />
          <el-option :value="24" label="最近24小时" />
          <el-option :value="168" label="最近一周" />
          <el-option :value="720" label="最近一个月" />
          <el-option :value="2160" label="最近三个月" />
          <el-option value="custom" label="自定义时间段" />
        </el-select>
        <template v-if="hours === 'custom'">
          <el-date-picker
            v-model="customRange[0]"
            type="datetime"
            placeholder="开始时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 200px"
          />
          <span style="color: var(--text-sub)">至</span>
          <el-date-picker
            v-model="customRange[1]"
            type="datetime"
            placeholder="结束时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DDTHH:mm:ss"
            style="width: 200px"
          />
          <el-button type="primary" @click="loadMetrics">查询</el-button>
        </template>
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button type="warning" :icon="Monitor" @click="openDiagnose" :loading="diagLoading">
          智能诊断（只读）
        </el-button>
      </div>
    </div>

    <!-- 基本信息 -->
    <el-card class="tech-card" shadow="never">
      <template #header>基本信息</template>
      <el-descriptions :column="4" border size="small" v-if="server">
        <el-descriptions-item label="名称">{{ server.name }}</el-descriptions-item>
        <el-descriptions-item label="IP地址">{{ server.ip }}</el-descriptions-item>
        <el-descriptions-item label="操作系统">
          {{ server.os_distro || (server.os_type === 'linux' ? 'Linux' : 'Windows') }}
          {{ server.os_version }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusTagType(server.status)" size="small" effect="dark">
            {{ statusLabel(server.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="CPU">{{ server.cpu_model || '-' }} × {{ server.cpu_cores }}核</el-descriptions-item>
        <el-descriptions-item label="内存总量">{{ server.mem_total_gb }} GB</el-descriptions-item>
        <el-descriptions-item label="标签">{{ server.tags || '-' }}</el-descriptions-item>
        <el-descriptions-item label="最后上报">{{ formatTime(server.last_seen) }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 实时指标 -->
    <el-row :gutter="16" style="margin-top: 16px" v-if="latest">
      <el-col :span="6">
        <StatCard label="CPU使用率" :value="latest.cpu_percent.toFixed(1)" unit="%" icon="Cpu" :color="percentColor(latest.cpu_percent)" />
      </el-col>
      <el-col :span="6">
        <StatCard label="内存使用率" :value="latest.mem_percent.toFixed(1)" unit="%" icon="Coin" :color="percentColor(latest.mem_percent)" />
      </el-col>
      <el-col :span="6">
        <StatCard label="磁盘使用率" :value="latest.disk_percent.toFixed(1)" unit="%" icon="Box" :color="percentColor(latest.disk_percent)" />
      </el-col>
      <el-col :span="6">
        <StatCard label="进程数" :value="latest.process_count" icon="List" color="#9b6bff" />
      </el-col>
    </el-row>

    <!-- 历史曲线 -->
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="12">
        <el-card class="tech-card" shadow="never">
          <template #header>CPU使用率历史 (%)</template>
          <TrendChart :option="cpuOption" :height="240" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="tech-card" shadow="never">
          <template #header>内存使用率历史 (%)</template>
          <TrendChart :option="memOption" :height="240" />
        </el-card>
      </el-col>
    </el-row>
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="12">
        <el-card class="tech-card" shadow="never">
          <template #header>磁盘使用率历史 (%) - 按分区</template>
          <TrendChart :option="diskOption" :height="240" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="tech-card" shadow="never">
          <template #header>网络流量 (Mbps)</template>
          <TrendChart :option="netOption" :height="240" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 磁盘分区 -->
    <el-card class="tech-card" shadow="never" style="margin-top: 16px" v-if="rawDetail && rawDetail.disks && rawDetail.disks.length">
      <template #header>磁盘分区</template>
      <el-table :data="rawDetail.disks" size="small">
        <el-table-column prop="device" label="设备" min-width="140" />
        <el-table-column prop="mount" label="挂载点" min-width="120" />
        <el-table-column label="总容量" width="110">
          <template #default="{ row }">{{ Number(row.total_gb || 0).toFixed(1) }} GB</template>
        </el-table-column>
        <el-table-column label="已用" width="110">
          <template #default="{ row }">{{ Number(row.used_gb || 0).toFixed(1) }} GB</template>
        </el-table-column>
        <el-table-column label="使用率" min-width="180">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.min(100, Number(row.percent || 0))"
              :color="percentColor(row.percent)"
            />
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 智能诊断抽屉（只读：只采集，不修改） -->
    <el-drawer v-model="diagVisible" title="智能诊断（只读排查）" size="62%" :destroy-on-close="true">
      <div v-loading="diagLoading">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="只读排查 · 人工处置"
          description="本功能仅通过 SSH(Linux) / WinRM(Windows) 执行只读命令采集 CPU/内存/磁盘/进程，不会做任何修改。AI 给出原因分析与人工处置建议，实际修复请运维人员手动执行。"
          style="margin-bottom: 16px"
        />

        <el-alert
          v-if="diagResult && !diagResult.ok"
          type="error"
          :closable="false"
          :title="diagResult.error"
          style="margin-bottom: 16px"
        />

        <template v-if="diagResult && diagResult.ok">
          <div class="diag-block-title">AI 分析（原因 + 人工处置建议）</div>
          <pre class="diag-analysis">{{ diagResult.analysis }}</pre>

          <div class="diag-block-title">原始只读诊断数据</div>
          <el-collapse v-model="activeDiag">
            <el-collapse-item
              v-for="sec in diagSections"
              :key="sec.key"
              :name="sec.key"
              :title="sec.label"
            >
              <pre class="diag-raw">{{ sec.content }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, ArrowLeft, Monitor } from '@element-plus/icons-vue'
import { serverApi } from '@/api'
import StatCard from '@/components/StatCard.vue'
import TrendChart from '@/components/TrendChart.vue'
import { formatTime, formatAxisTime, statusTagType, statusLabel, percentColor } from '@/utils/format'

const route = useRoute()
const serverId = route.params.id

const loading = ref(false)
const server = ref(null)
const metrics = ref([])
const rawDetail = ref(null)
const hours = ref(1)
const customRange = ref([null, null])

// 本地时间 -> UTC ISO (后端按UTC存储)
function toUtcIso(s) {
  return new Date(s).toISOString()
}

function handleRangeChange() {
  if (hours.value !== 'custom') loadMetrics()
}

const latest = computed(() => (metrics.value.length ? metrics.value[metrics.value.length - 1] : null))

const times = computed(() => metrics.value.map((m) => formatAxisTime(m.collected_at, hours.value)))

function lineOption(seriesData, name, color, max = 100) {
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 46, right: 20, top: 30, bottom: 30 },
    xAxis: {
      type: 'category',
      data: times.value,
      axisLabel: { color: '#7d92b5' },
      axisLine: { lineStyle: { color: '#1c2f4f' } }
    },
    yAxis: {
      type: 'value',
      max,
      axisLabel: { color: '#7d92b5' },
      splitLine: { lineStyle: { color: '#12213c' } }
    },
    series: [
      {
        name,
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: seriesData,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: color + '55' },
              { offset: 1, color: color + '00' }
            ]
          }
        }
      }
    ]
  }
}

const cpuOption = computed(() => lineOption(metrics.value.map((m) => m.cpu_percent), 'CPU%', '#00d4ff'))
const memOption = computed(() => lineOption(metrics.value.map((m) => m.mem_percent), '内存%', '#00e396'))

// 磁盘按分区分开画线: Linux显示挂载点(如 / 、/home), Windows显示盘符(如 C:\)
const DISK_COLORS = ['#ffb020', '#00d4ff', '#00e396', '#9b6bff', '#ff4d5e', '#4dd0e1', '#ff9f43', '#a55eea']
const diskOption = computed(() => {
  const mountMap = new Map() // mount -> [percent...]
  metrics.value.forEach((m) => {
    const disks = m.disks || []
    disks.forEach((d) => {
      const key = d.mount || d.device || '未知分区'
      if (!mountMap.has(key)) mountMap.set(key, [])
      mountMap.get(key).push(d.percent ?? 0)
    })
  })
  const hasPartitions = mountMap.size > 0
  const series = hasPartitions
    ? Array.from(mountMap.entries()).map(([mount, data], i) => ({
        name: mount,
        type: 'line',
        smooth: true,
        symbol: 'none',
        data,
        lineStyle: { color: DISK_COLORS[i % DISK_COLORS.length], width: 2 },
        itemStyle: { color: DISK_COLORS[i % DISK_COLORS.length] }
      }))
    : [{
        name: '总体',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: metrics.value.map((m) => m.disk_percent),
        lineStyle: { color: '#ffb020', width: 2 },
        itemStyle: { color: '#ffb020' }
      }]
  return {
    tooltip: { trigger: 'axis' },
    legend: {
      data: series.map((s) => s.name),
      textStyle: { color: '#7d92b5' },
      top: 0,
      type: 'scroll'
    },
    grid: { left: 46, right: 20, top: 30, bottom: 30 },
    xAxis: {
      type: 'category',
      data: times.value,
      axisLabel: { color: '#7d92b5' },
      axisLine: { lineStyle: { color: '#1c2f4f' } }
    },
    yAxis: {
      type: 'value',
      max: 100,
      axisLabel: { color: '#7d92b5' },
      splitLine: { lineStyle: { color: '#12213c' } }
    },
    series
  }
})

const netOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['下行', '上行'], textStyle: { color: '#7d92b5' }, top: 0 },
  grid: { left: 46, right: 20, top: 30, bottom: 30 },
  xAxis: {
    type: 'category',
    data: times.value,
    axisLabel: { color: '#7d92b5' },
    axisLine: { lineStyle: { color: '#1c2f4f' } }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#7d92b5' },
    splitLine: { lineStyle: { color: '#12213c' } }
  },
  series: [
    {
      name: '下行',
      type: 'line',
      smooth: true,
      symbol: 'none',
      data: metrics.value.map((m) => m.net_rx_mbps),
      lineStyle: { color: '#2f7bff', width: 2 },
      itemStyle: { color: '#2f7bff' }
    },
    {
      name: '上行',
      type: 'line',
      smooth: true,
      symbol: 'none',
      data: metrics.value.map((m) => m.net_tx_mbps),
      lineStyle: { color: '#9b6bff', width: 2 },
      itemStyle: { color: '#9b6bff' }
    }
  ]
}))

async function loadServer() {
  const list = await serverApi.list()
  server.value = list.find((s) => String(s.id) === String(serverId)) || null
}

async function loadMetrics() {
  if (hours.value === 'custom') {
    const [start, end] = customRange.value
    if (!start || !end) {
      ElMessage.warning('请选择开始和结束时间')
      return
    }
    metrics.value = await serverApi.metrics(serverId, {
      start: toUtcIso(start),
      end: toUtcIso(end)
    })
  } else {
    metrics.value = await serverApi.metrics(serverId, { hours: hours.value })
  }
}

async function loadDetail() {
  try {
    const res = await serverApi.detail(serverId)
    rawDetail.value = res.raw || null
  } catch (e) {
    rawDetail.value = null
  }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.allSettled([loadServer(), loadMetrics(), loadDetail()])
  } finally {
    loading.value = false
  }
}

// ---------- 智能诊断（只读） ----------
const diagVisible = ref(false)
const diagLoading = ref(false)
const diagResult = ref(null)
const activeDiag = ref(['cpu', 'memory', 'disk', 'process'])

const diagSections = computed(() => {
  const sec = diagResult.value?.sections || {}
  const labels = { cpu: 'CPU', memory: '内存', disk: '磁盘', process: '进程/IO' }
  return Object.keys(labels)
    .filter((k) => sec[k])
    .map((k) => ({ key: k, label: labels[k], content: sec[k] }))
})

async function openDiagnose() {
  diagVisible.value = true
  diagResult.value = null
  diagLoading.value = true
  try {
    const res = await serverApi.diagnose(serverId)
    diagResult.value = res
    if (!res.ok) ElMessage.error(res.error || '诊断失败')
  } catch (e) {
    ElMessage.error('诊断请求失败，请检查后端日志')
  } finally {
    diagLoading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.diag-block-title {
  font-weight: 600;
  color: #00d4ff;
  margin: 14px 0 8px;
  font-size: 14px;
}
.diag-analysis {
  background: rgba(18, 33, 60, 0.85);
  border: 1px solid var(--border-tech, #1c2f4f);
  border-radius: 8px;
  padding: 14px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
  font-size: 13px;
  color: #d6e2f5;
}
.diag-raw {
  background: #0c1626;
  border-radius: 6px;
  padding: 10px 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
  color: #9fb3d1;
  max-height: 360px;
  overflow: auto;
}
</style>

