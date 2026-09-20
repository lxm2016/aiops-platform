<template>
  <div>
    <!-- 统计卡片 -->
    <el-row :gutter="16">
      <el-col :span="6">
        <StatCard label="服务器总数" :value="summary.total" icon="Monitor" color="#00d4ff" to="/servers" />
      </el-col>
      <el-col :span="6">
        <StatCard label="在线服务器" :value="summary.online" icon="CircleCheck" color="#00e396" to="/servers" :query="{ status: 'online' }" />
      </el-col>
      <el-col :span="6">
        <StatCard label="离线服务器" :value="summary.offline" icon="CircleClose" color="#ff4d5e" to="/servers" :query="{ status: 'offline' }" />
      </el-col>
      <el-col :span="6">
        <StatCard label="未处理告警" :value="summary.open_alerts" icon="Bell" color="#ffb020" to="/alerts" :query="{ status: 'open' }" />
      </el-col>
    </el-row>

    <!-- 温湿度 -->
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col v-for="s in sensors" :key="s.id" :span="8">
        <div class="tech-card env-card">
          <div class="env-name">
            <el-icon color="#00d4ff"><Sunny /></el-icon>
            {{ s.name }}
            <span class="env-loc">{{ s.location }}</span>
          </div>
          <div class="env-values">
            <div class="env-item">
              <div class="env-num num-highlight" style="color: #ffb020">
                {{ s.temperature ?? '--' }}<span class="env-unit">°C</span>
              </div>
              <div class="env-label">温度</div>
            </div>
            <div class="env-divider"></div>
            <div class="env-item">
              <div class="env-num num-highlight" style="color: #00d4ff">
                {{ s.humidity ?? '--' }}<span class="env-unit">%</span>
              </div>
              <div class="env-label">湿度</div>
            </div>
          </div>
        </div>
      </el-col>
      <el-col v-if="!sensors.length" :span="24">
        <el-empty description="暂无温湿度传感器数据" :image-size="60" />
      </el-col>
    </el-row>

    <!-- 图表 -->
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="8">
        <el-card class="tech-card tech-card--link" shadow="never" @click="goServers">
          <template #header>服务器状态分布 <span class="chart-hint">点击跳转</span></template>
          <TrendChart :option="serverPieOption" :height="260" :clickHandlers="serverChartClickHandlers" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="tech-card tech-card--link" shadow="never" @click="goAlerts">
          <template #header>告警级别分布 <span class="chart-hint">点击跳转</span></template>
          <TrendChart :option="alertPieOption" :height="260" :clickHandlers="alertChartClickHandlers" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card class="tech-card tech-card--link" shadow="never" @click="goAlerts">
          <template #header>告警类别统计 <span class="chart-hint">点击跳转</span></template>
          <TrendChart :option="alertBarOption" :height="260" :clickHandlers="alertBarClickHandlers" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 最新告警 -->
    <el-card class="tech-card" shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>最新告警</span>
          <el-button link type="primary" @click="$router.push('/alerts')">
            查看全部&nbsp;<el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </template>
      <el-table :data="recentAlerts" size="small">
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="alertLevelTag(row.level)" size="small" effect="dark">
              {{ alertLevelLabel(row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="160" show-overflow-tooltip />
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="alertStatusTag(row.status)" size="small">
              {{ alertStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { serverApi, envApi, alertApi } from '@/api'
import StatCard from '@/components/StatCard.vue'
import TrendChart from '@/components/TrendChart.vue'
import {
  formatTime,
  alertLevelTag,
  alertLevelLabel,
  alertStatusTag,
  alertStatusLabel
} from '@/utils/format'

const router = useRouter()
const summary = ref({ total: 0, online: 0, offline: 0, open_alerts: 0 })
const sensors = ref([])
const alerts = ref([])

const recentAlerts = computed(() => alerts.value.slice(0, 8))

// ---------- 跳转函数 ----------
function goServers(status) {
  router.push({ path: '/servers', query: status ? { status } : {} })
}
function goAlerts(query = {}) {
  router.push({ path: '/alerts', query })
}

// ---------- 图表点击处理 ----------
// 服务器饼图点击: 点"在线"跳在线服务器, 点"离线"跳离线服务器
const serverChartClickHandlers = [
  {
    name: 'click',
    fn: (params) => {
      const map = { '在线': 'online', '离线': 'offline' }
      goServers(map[params.name] || '')
    }
  }
]

// 告警级别饼图点击: 点某级别跳告警列表并筛选
const alertChartClickHandlers = [
  {
    name: 'click',
    fn: (params) => {
      const levelMap = { '严重': 'critical', '警告': 'warning', '提示': 'info' }
      const level = levelMap[params.name]
      if (level) goAlerts({ level })
    }
  }
]

// 告警类别柱状图点击
const alertBarClickHandlers = [
  {
    name: 'click',
    fn: (params) => {
      if (params.name && params.name !== '暂无告警') {
        goAlerts({ category: params.name })
      }
    }
  }
]

const serverPieOption = computed(() => ({
  tooltip: { trigger: 'item' },
  series: [
    {
      type: 'pie',
      radius: ['45%', '70%'],
      center: ['50%', '55%'],
      itemStyle: { borderColor: '#0e1a30', borderWidth: 2 },
      label: { color: '#7d92b5' },
      data: [
        { value: summary.value.online, name: '在线', itemStyle: { color: '#00e396' } },
        { value: summary.value.offline, name: '离线', itemStyle: { color: '#ff4d5e' } }
      ]
    }
  ]
}))

const alertPieOption = computed(() => {
  const levels = { critical: '严重', warning: '警告', info: '提示' }
  const colors = { critical: '#ff4d5e', warning: '#ffb020', info: '#2f7bff' }
  const data = Object.keys(levels)
    .map((k) => ({
      value: alerts.value.filter((a) => a.level === k).length,
      name: levels[k],
      itemStyle: { color: colors[k] }
    }))
    .filter((d) => d.value > 0)
  return {
    tooltip: { trigger: 'item' },
    series: [
      {
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '55%'],
        itemStyle: { borderColor: '#0e1a30', borderWidth: 2 },
        label: { color: '#7d92b5' },
        data: data.length ? data : [{ value: 0, name: '暂无告警', itemStyle: { color: '#22375c' } }]
      }
    ]
  }
})

const alertBarOption = computed(() => {
  const counter = {}
  alerts.value.forEach((a) => {
    counter[a.category] = (counter[a.category] || 0) + 1
  })
  const cats = Object.keys(counter)
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 46, right: 20, top: 20, bottom: 30 },
    xAxis: {
      type: 'category',
      data: cats,
      axisLabel: { color: '#7d92b5', interval: 0, rotate: cats.length > 4 ? 30 : 0 },
      axisLine: { lineStyle: { color: '#1c2f4f' } }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#7d92b5' },
      splitLine: { lineStyle: { color: '#12213c' } }
    },
    series: [
      {
        type: 'bar',
        barWidth: 22,
        data: cats.map((c) => counter[c]),
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: '#00d4ff' },
              { offset: 1, color: '#2f7bff' }
            ]
          }
        }
      }
    ]
  }
})

let timer = null

async function loadAll() {
  try {
    const [s, e, a] = await Promise.allSettled([
      serverApi.summary(),
      envApi.list(),
      alertApi.list({ limit: 100 })
    ])
    if (s.status === 'fulfilled') summary.value = s.value
    if (e.status === 'fulfilled') sensors.value = e.value
    if (a.status === 'fulfilled') alerts.value = a.value
  } catch (e) {
    /* 拦截器已处理 */
  }
}

onMounted(() => {
  loadAll()
  timer = setInterval(loadAll, 30000)
})

onBeforeUnmount(() => {
  timer && clearInterval(timer)
})
</script>

<style scoped>
.env-card {
  padding: 16px 20px;
}

.env-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 12px;
}

.env-loc {
  color: var(--text-sub);
  font-size: 12px;
  font-weight: 400;
}

.env-values {
  display: flex;
  align-items: center;
}

.env-item {
  flex: 1;
  text-align: center;
}

.env-num {
  font-size: 28px;
}

.env-unit {
  font-size: 13px;
  color: var(--text-sub);
  margin-left: 2px;
}

.env-label {
  color: var(--text-sub);
  font-size: 12px;
  margin-top: 4px;
}

.env-divider {
  width: 1px;
  height: 40px;
  background: var(--border-tech);
}

.tech-card--link {
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.tech-card--link:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 28px rgba(0, 212, 255, 0.12);
  border-color: var(--accent, #00d4ff);
}

.chart-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-sub);
  opacity: 0;
  transition: opacity 0.2s;
}

.tech-card--link:hover .chart-hint {
  opacity: 1;
}
</style>
