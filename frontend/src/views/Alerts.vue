<template>
  <div>
    <div class="page-header">
      <div class="page-title">告警中心</div>
      <div style="display: flex; gap: 10px">
        <el-select v-model="filter.status" placeholder="状态" clearable style="width: 130px" @change="load">
          <el-option label="未处理" value="open" />
          <el-option label="已确认" value="ack" />
          <el-option label="已解决" value="resolved" />
        </el-select>
        <el-select v-model="filter.level" placeholder="级别" clearable style="width: 130px" @change="load">
          <el-option label="严重" value="critical" />
          <el-option label="警告" value="warning" />
          <el-option label="提示" value="info" />
        </el-select>
        <el-button :icon="Setting" @click="$router.push('/alerts/config')">告警配置</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <el-card class="tech-card" shadow="never">
      <el-table :data="alerts" v-loading="loading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="级别" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="alertLevelTag(row.level)" size="small" effect="dark">
              {{ alertLevelLabel(row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="类别" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="150" show-overflow-tooltip />
        <el-table-column label="当前值" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.value !== null && row.value !== undefined" class="value-cell">
              {{ formatValue(row.metric, row.value) }}
            </span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="detail" label="详情" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="alertStatusTag(row.status)" size="small">
              {{ alertStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="发生时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :loading="analyzingId === row.id"
              @click="handleAnalyze(row)"
            >
              <el-icon><MagicStick /></el-icon>&nbsp;AI分析
            </el-button>
            <el-button
              v-if="row.status === 'open'"
              link
              type="warning"
              @click="handleAck(row)"
            >
              确认
            </el-button>
            <el-button
              v-if="row.status !== 'resolved'"
              link
              type="success"
              @click="handleResolve(row)"
            >
              解决
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- AI 分析结果 -->
    <el-dialog v-model="analyzeVisible" title="AI 告警分析" width="640px">
      <div class="analyze-info" v-if="analyzeTarget">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="级别">
            <el-tag :type="alertLevelTag(analyzeTarget.level)" size="small" effect="dark">
              {{ alertLevelLabel(analyzeTarget.level) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="来源">{{ analyzeTarget.source }}</el-descriptions-item>
          <el-descriptions-item label="标题" :span="2">{{ analyzeTarget.title }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <div class="analyze-result" v-loading="analyzing">
        <div class="analyze-result-title">
          <el-icon color="#00d4ff"><MagicStick /></el-icon>
          分析结果与处置建议
        </div>
        <div class="analyze-content">{{ analysisText || '暂无分析结果' }}</div>
      </div>
      <template #footer>
        <el-button type="primary" @click="analyzeVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, MagicStick, Setting } from '@element-plus/icons-vue'
import { alertApi } from '@/api'
import {
  formatTime,
  formatValue,
  alertLevelTag,
  alertLevelLabel,
  alertStatusTag,
  alertStatusLabel
} from '@/utils/format'

const alerts = ref([])
const loading = ref(false)
const filter = reactive({ status: '', level: '' })

const analyzeVisible = ref(false)
const analyzing = ref(false)
const analyzingId = ref(null)
const analyzeTarget = ref(null)
const analysisText = ref('')

async function load() {
  loading.value = true
  try {
    const params = { limit: 200 }
    if (filter.status) params.status = filter.status
    if (filter.level) params.level = filter.level
    alerts.value = await alertApi.list(params)
  } finally {
    loading.value = false
  }
}

async function handleAck(row) {
  await alertApi.ack(row.id)
  ElMessage.success('告警已确认')
  load()
}

async function handleResolve(row) {
  await alertApi.resolve(row.id)
  ElMessage.success('告警已解决')
  load()
}

async function handleAnalyze(row) {
  analyzeTarget.value = row
  analysisText.value = ''
  analyzeVisible.value = true
  analyzing.value = true
  analyzingId.value = row.id
  try {
    const res = await alertApi.analyze(row.id)
    analysisText.value = res.analysis
  } catch (e) {
    analysisText.value = 'AI 分析失败，请稍后重试'
  } finally {
    analyzing.value = false
    analyzingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.analyze-info {
  margin-bottom: 16px;
}

.analyze-result {
  background: rgba(18, 33, 60, 0.5);
  border: 1px solid var(--border-tech);
  border-radius: 8px;
  padding: 14px 16px;
  min-height: 120px;
}

.value-cell {
  font-weight: 600;
  color: var(--el-color-danger);
}
.muted {
  color: var(--el-text-color-secondary);
}

.analyze-result-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 10px;
  color: #00d4ff;
}

.analyze-content {
  white-space: pre-wrap;
  line-height: 1.8;
  font-size: 13px;
  color: var(--text-main);
}
</style>
