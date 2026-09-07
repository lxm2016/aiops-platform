<template>
  <div>
    <div class="page-header">
      <div class="page-title">温湿度监控</div>
      <div style="display: flex; gap: 10px">
        <el-button type="primary" :icon="Plus" @click="dialogVisible = true">添加传感器</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 传感器卡片 -->
    <el-row :gutter="16">
      <el-col v-for="s in sensors" :key="s.id" :span="8" style="margin-bottom: 16px">
        <el-card class="tech-card env-card" shadow="never">
          <div class="env-head">
            <div class="env-name">
              <el-icon color="#00d4ff"><Sunny /></el-icon>
              {{ s.name }}
              <el-tag :type="statusTagType(s.status)" size="small" effect="dark">
                {{ statusLabel(s.status) }}
              </el-tag>
            </div>
            <span class="env-loc">{{ s.location || '未设置位置' }}</span>
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

          <div class="env-foot">
            <span class="env-time">最后读数：{{ formatTime(s.last_seen) }}</span>
            <div>
              <el-button link type="primary" @click="openHistory(s)">历史曲线</el-button>
              <el-button link type="success" @click="openPush(s)">推送读数</el-button>
              <el-popconfirm title="确定删除该传感器？" @confirm="handleDelete(s)">
                <template #reference>
                  <el-button link type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col v-if="!sensors.length" :span="24">
        <el-empty description="暂无温湿度传感器" :image-size="80" />
      </el-col>
    </el-row>

    <!-- 添加传感器 -->
    <el-dialog v-model="dialogVisible" title="添加传感器" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 1号机房温湿度计" />
        </el-form-item>
        <el-form-item label="位置">
          <el-input v-model="form.location" placeholder="如 A栋3层机房" />
        </el-form-item>
        <el-form-item label="数据源URL">
          <el-input v-model="form.source_url" placeholder="可选，外部网关推送地址" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">确定</el-button>
      </template>
    </el-dialog>

    <!-- 手动推送读数 -->
    <el-dialog v-model="pushVisible" :title="`推送读数 - ${currentSensor?.name || ''}`" width="420px">
      <el-form label-width="80px">
        <el-form-item label="温度 °C">
          <el-input-number v-model="pushForm.temperature" :precision="1" :step="0.1" :min="-40" :max="80" style="width: 100%" />
        </el-form-item>
        <el-form-item label="湿度 %">
          <el-input-number v-model="pushForm.humidity" :precision="1" :step="0.1" :min="0" :max="100" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pushVisible = false">取消</el-button>
        <el-button type="primary" :loading="pushing" @click="handlePush">推送</el-button>
      </template>
    </el-dialog>

    <!-- 历史曲线 -->
    <el-dialog v-model="historyVisible" :title="`历史曲线 - ${currentSensor?.name || ''}`" width="820px">
      <div style="display: flex; justify-content: flex-end; margin-bottom: 10px">
        <el-select v-model="historyHours" style="width: 150px" @change="loadHistory">
          <el-option :value="1" label="最近1小时" />
          <el-option :value="6" label="最近6小时" />
          <el-option :value="24" label="最近24小时" />
          <el-option :value="72" label="最近3天" />
          <el-option :value="168" label="最近7天" />
          <el-option :value="720" label="最近30天" />
          <el-option :value="2160" label="最近90天" />
        </el-select>
      </div>
      <TrendChart :option="historyOption" :height="320" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { envApi } from '@/api'
import TrendChart from '@/components/TrendChart.vue'
import { formatTime, formatHM, statusTagType, statusLabel } from '@/utils/format'

const sensors = ref([])
const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)

const pushVisible = ref(false)
const pushing = ref(false)
const currentSensor = ref(null)
const pushForm = reactive({ temperature: 25.0, humidity: 50.0 })

const historyVisible = ref(false)
const historyHours = ref(24)
const historyData = ref([])

const form = reactive({ name: '', location: '', source_url: '' })
const rules = { name: [{ required: true, message: '请输入名称', trigger: 'blur' }] }

const historyOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['温度', '湿度'], textStyle: { color: '#7d92b5' }, top: 0 },
  grid: { left: 46, right: 46, top: 34, bottom: 30 },
  xAxis: {
    type: 'category',
    data: historyData.value.map((r) => formatHM(r.collected_at)),
    axisLabel: { color: '#7d92b5' },
    axisLine: { lineStyle: { color: '#1c2f4f' } }
  },
  yAxis: [
    {
      type: 'value',
      name: '°C',
      nameTextStyle: { color: '#ffb020' },
      axisLabel: { color: '#7d92b5' },
      splitLine: { lineStyle: { color: '#12213c' } }
    },
    {
      type: 'value',
      name: '%',
      nameTextStyle: { color: '#00d4ff' },
      axisLabel: { color: '#7d92b5' },
      splitLine: { show: false }
    }
  ],
  series: [
    {
      name: '温度',
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      data: historyData.value.map((r) => r.temperature),
      lineStyle: { color: '#ffb020', width: 2 },
      itemStyle: { color: '#ffb020' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: '#ffb02044' },
            { offset: 1, color: '#ffb02000' }
          ]
        }
      }
    },
    {
      name: '湿度',
      type: 'line',
      smooth: true,
      yAxisIndex: 1,
      symbol: 'circle',
      symbolSize: 5,
      data: historyData.value.map((r) => r.humidity),
      lineStyle: { color: '#00d4ff', width: 2 },
      itemStyle: { color: '#00d4ff' }
    }
  ]
}))

async function load() {
  sensors.value = await envApi.list()
}

async function handleCreate() {
  await formRef.value.validate()
  submitting.value = true
  try {
    await envApi.create(form)
    ElMessage.success('添加成功')
    dialogVisible.value = false
    Object.assign(form, { name: '', location: '', source_url: '' })
    load()
  } finally {
    submitting.value = false
  }
}

function openPush(s) {
  currentSensor.value = s
  pushForm.temperature = s.temperature ?? 25.0
  pushForm.humidity = s.humidity ?? 50.0
  pushVisible.value = true
}

async function handlePush() {
  pushing.value = true
  try {
    await envApi.push(currentSensor.value.id, pushForm.temperature, pushForm.humidity)
    ElMessage.success('推送成功')
    pushVisible.value = false
    load()
  } finally {
    pushing.value = false
  }
}

async function openHistory(s) {
  currentSensor.value = s
  historyVisible.value = true
  await loadHistory()
}

async function loadHistory() {
  historyData.value = await envApi.history(currentSensor.value.id, historyHours.value)
}

async function handleDelete(s) {
  await envApi.remove(s.id)
  ElMessage.success('删除成功')
  load()
}

onMounted(load)
</script>

<style scoped>
.env-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.env-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 15px;
}

.env-loc {
  color: var(--text-sub);
  font-size: 12px;
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
  font-size: 30px;
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
  height: 44px;
  background: var(--border-tech);
}

.env-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  border-top: 1px solid var(--border-tech);
  padding-top: 10px;
}

.env-time {
  color: var(--text-sub);
  font-size: 12px;
}
</style>
