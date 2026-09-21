<template>
  <div class="alert-config">
    <el-tabs v-model="activeTab" class="config-tabs">
      <!-- ==================== 告警规则 ==================== -->
      <el-tab-pane label="告警规则" name="rules">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-button type="primary" @click="openRuleDialog()">
              <el-icon><Plus /></el-icon>&nbsp;新建规则
            </el-button>
            <el-button @click="handleInitDefaults">
              <el-icon><MagicStick /></el-icon>&nbsp;一键生成默认规则
            </el-button>
          </div>
          <el-alert
            type="info"
            :closable="false"
            show-icon
            class="tip"
          >
            <template #title>
              默认策略：资源使用率 <b>≥80% 提示</b>、<b>≥90% 严重告警</b>。
              指标回落会自动关闭告警并推送恢复通知；同一告警在静默期内不重复打扰。
            </template>
          </el-alert>
        </div>

        <el-table :data="rules" v-loading="loadingRules" stripe>
          <el-table-column prop="name" label="规则名称" min-width="150" />
          <el-table-column label="监控对象" width="110">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ categoryLabel(row.category) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="指标" width="120">
            <template #default="{ row }">{{ metricLabel(row.metric) }}</template>
          </el-table-column>
          <el-table-column label="触发条件" min-width="230">
            <template #default="{ row }">
              <span class="cond">
                <el-tag size="small" type="warning">
                  {{ opText(row.operator) }}{{ row.warning_threshold }} 提示
                </el-tag>
                <el-tag size="small" type="danger">
                  {{ opText(row.operator) }}{{ row.critical_threshold }} 告警
                </el-tag>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="连续/静默" width="140">
            <template #default="{ row }">
              <span class="muted">连续{{ row.duration_times }}次 · 静默{{ row.silence_minutes }}分</span>
            </template>
          </el-table-column>
          <el-table-column label="通知渠道" min-width="160">
            <template #default="{ row }">
              <template v-if="row.channel_ids_list && row.channel_ids_list.length">
                <el-tag
                  v-for="cid in row.channel_ids_list"
                  :key="cid"
                  size="small"
                  class="ch-tag"
                >
                  {{ channelName(cid) }}
                </el-tag>
              </template>
              <span v-else class="muted">仅记录不通知</span>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="80" align="center">
            <template #default="{ row }">
              <el-switch
                v-model="row.enabled"
                size="small"
                @change="toggleRule(row)"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openRuleDialog(row)">编辑</el-button>
              <el-button link type="danger" @click="removeRule(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-hint">
              还没有告警规则。点「一键生成默认规则」快速创建（CPU/内存/磁盘 80%提示、90%告警）。
            </div>
          </template>
        </el-table>
      </el-tab-pane>

      <!-- ==================== 通知渠道 ==================== -->
      <el-tab-pane label="通知渠道" name="channels">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-button type="primary" @click="openChannelDialog()">
              <el-icon><Plus /></el-icon>&nbsp;新建渠道
            </el-button>
            <el-button @click="loadLogs">
              <el-icon><Refresh /></el-icon>&nbsp;刷新记录
            </el-button>
          </div>
          <el-alert type="info" :closable="false" show-icon class="tip">
            <template #title>
              短信平台与电话告警盒子用「通用 HTTP 网关」对接：填平台接口地址 + 参数模板即可，
              不用改代码。配好后一定点「测试」验证能收到。
            </template>
          </el-alert>
        </div>

        <el-table :data="channels" v-loading="loadingChannels" stripe>
          <el-table-column prop="name" label="渠道名称" min-width="150" />
          <el-table-column label="类型" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="typeTag(row.type)">{{ typeLabel(row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="号码 / 地址" min-width="280">
            <template #default="{ row }">
              <div v-if="row.targets" class="url">号码: {{ row.targets }}</div>
              <span class="url">{{ row.webhook_url || row.http_url || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="80" align="center">
            <template #default="{ row }">
              <el-switch v-model="row.enabled" size="small" @change="toggleChannel(row)" />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="230" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" @click="testChannel(row)">测试</el-button>
              <el-button link type="primary" @click="openChannelDialog(row)">编辑</el-button>
              <el-button link @click="previewChannel(row)">预览</el-button>
              <el-button link type="danger" @click="removeChannel(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="empty-hint">
              还没有通知渠道。新建一个钉钉/企业微信机器人，或对接院内短信平台、电话告警盒子。
            </div>
          </template>
        </el-table>

        <!-- 发送记录 -->
        <div class="logs">
          <div class="logs-title">
            <span>最近发送记录</span>
            <el-button link type="danger" size="small" @click="clearLogs">清空</el-button>
          </div>
          <el-table :data="logs" size="small" max-height="260">
            <el-table-column label="时间" width="90">
              <template #default="{ row }">{{ fmtTime(row.sent_at) }}</template>
            </el-table-column>
            <el-table-column prop="channel_name" label="渠道" width="150" />
            <el-table-column label="结果" width="70" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.success ? 'success' : 'danger'">
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="target" label="内容摘要" min-width="200" show-overflow-tooltip />
            <el-table-column label="平台返回/失败原因" min-width="220">
              <template #default="{ row }">
                <span class="muted">{{ row.success ? (row.response || '-') : row.error }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- ==================== 规则编辑对话框 ==================== -->
    <el-dialog
      v-model="ruleDialog"
      :title="editingRule ? '编辑告警规则' : '新建告警规则'"
      width="620px"
    >
      <el-form :model="ruleForm" label-width="110px">
        <el-form-item label="规则名称" required>
          <el-input v-model="ruleForm.name" placeholder="如: CPU使用率告警" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="监控对象">
              <el-select v-model="ruleForm.category" class="full">
                <el-option
                  v-for="c in meta.categories" :key="c.value"
                  :label="c.label" :value="c.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="监控指标">
              <el-select v-model="ruleForm.metric" class="full">
                <el-option
                  v-for="m in meta.metrics" :key="m.value"
                  :label="m.label" :value="m.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">阈值（两级）</el-divider>
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="比较方式">
              <el-select v-model="ruleForm.operator" class="full">
                <el-option
                  v-for="o in meta.operators" :key="o.value"
                  :label="o.label" :value="o.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="提示阈值">
              <el-input-number v-model="ruleForm.warning_threshold" :min="0" :max="100" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="告警阈值">
              <el-input-number v-model="ruleForm.critical_threshold" :min="0" :max="100" />
            </el-form-item>
          </el-col>
        </el-row>
        <div class="form-hint">
          {{ previewCondition }}
        </div>

        <el-divider content-position="left">防打扰</el-divider>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="连续次数">
              <el-input-number v-model="ruleForm.duration_times" :min="1" :max="20" />
              <span class="unit">次超限才告警</span>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="静默期">
              <el-input-number v-model="ruleForm.silence_minutes" :min="0" :max="1440" />
              <span class="unit">分钟内不重发</span>
            </el-form-item>
          </el-col>
        </el-row>
        <div class="form-hint">
          连续次数用于过滤瞬时抖动；静默期内同一告警不重复打扰。
          但<b>级别升级（提示 → 严重）会立即再发一次</b>，不受静默期限制。
        </div>

        <el-divider content-position="left">通知</el-divider>
        <el-form-item label="通知级别">
          <el-checkbox-group v-model="ruleForm.notify_levels">
            <el-checkbox label="warning">提示</el-checkbox>
            <el-checkbox label="critical">严重告警</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="通知渠道">
          <el-select
            v-model="ruleForm.channel_ids"
            multiple
            class="full"
            placeholder="选择要推送到的渠道（不选则只记录不通知）"
          >
            <el-option
              v-for="ch in channels" :key="ch.id"
              :label="ch.name + '（' + typeLabel(ch.type) + '）'" :value="ch.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="恢复通知">
          <el-switch v-model="ruleForm.notify_on_recovery" />
          <span class="unit">指标恢复正常时也推送一条</span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="ruleForm.remark" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingRule" @click="saveRule">保存</el-button>
      </template>
    </el-dialog>

    <!-- ==================== 渠道编辑对话框 ==================== -->
    <el-dialog
      v-model="channelDialog"
      :title="editingChannel ? '编辑通知渠道' : '新建通知渠道'"
      width="680px"
    >
      <el-form :model="channelForm" label-width="120px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="渠道名称" required>
              <el-input v-model="channelForm.name" placeholder="如: 运维钉钉群" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="渠道类型">
              <el-select v-model="channelForm.type" class="full" @change="onTypeChange">
                <el-option
                  v-for="(t, k) in types" :key="k"
                  :label="t.label" :value="k"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-alert
          v-if="currentType"
          type="info"
          :closable="false"
          class="type-tip"
        >
          <template #title>
            <div><b>怎么获取：</b>{{ currentType.desc }}</div>
            <div v-if="currentType.tips" class="type-tip-extra">{{ currentType.tips }}</div>
          </template>
        </el-alert>

        <!-- 钉钉 / 企业微信 -->
        <template v-if="isRobot">
          <el-form-item label="Webhook地址">
            <el-input v-model="channelForm.webhook_url" placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." />
          </el-form-item>
          <template v-if="channelForm.type === 'dingtalk'">
            <el-form-item label="加签密钥">
              <el-input v-model="channelForm.secret" placeholder="安全设置为'加签'时填写，可留空" show-password />
            </el-form-item>
            <el-form-item label="@手机号">
              <el-input v-model="channelForm.at_mobiles" placeholder="多个用逗号分隔，如 13800138000,13900139000" />
            </el-form-item>
            <el-form-item label="@所有人">
              <el-switch v-model="channelForm.at_all" />
            </el-form-item>
          </template>
        </template>

        <!-- 短信 / 电话 / 自定义网关 -->
        <template v-else>
          <el-form-item :label="targetsLabel">
            <el-input
              v-model="channelForm.targets"
              :placeholder="targetsPlaceholder"
            />
            <div class="field-tip">
              填在这里一次即可 —— 模板里用 {phone} 或 {tts} 这类变量引用。
              换号码只改这里，不用动模板。
            </div>
          </el-form-item>
          <el-form-item label="接口地址">
            <el-input v-model="channelForm.http_url" :placeholder="urlPlaceholder" />
          </el-form-item>
          <el-form-item label="请求方式">
            <el-radio-group v-model="channelForm.http_method">
              <el-radio label="POST">POST</el-radio>
              <el-radio label="GET">GET</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="请求头">
            <el-input
              v-model="channelForm.http_headers"
              type="textarea" :rows="2"
              placeholder='JSON格式，如 {"Content-Type":"application/json"}'
            />
          </el-form-item>
          <el-form-item label="请求体模板">
            <el-input
              v-model="channelForm.http_body"
              type="textarea" :rows="4"
              :placeholder="templatePlaceholder"
            />
            <div class="field-tip">
              号码别名 {{ '{targets} {phone} {mobile} {tel} {called} {callee}' }}；
              内容别名 {{ '{tts} {text} {msg}' }}（电话盒子用 {tts}）；
              地址 {{ '{ip}' }}、类别 {{ '{category} {group}' }}、
              数值 {{ '{value} {threshold} {state}' }}；
              其他 {{ '{title} {content} {short} {level} {source} {metric} {time}' }}
            </div>
            <div class="tmpl-btns">
              <el-button
                v-if="currentType && currentType.sample_body"
                link type="primary" size="small"
                @click="channelForm.http_body = currentType.sample_body"
              >
                填入表单示例
              </el-button>
              <el-button
                v-if="currentType && currentType.sample_body_json"
                link type="primary" size="small"
                @click="channelForm.http_body = currentType.sample_body_json"
              >
                填入JSON示例
              </el-button>
              <el-button
                v-if="channelForm.type === 'voice' || channelForm.type === 'sms'"
                link type="primary" size="small"
                @click="fillMfmBox()"
              >
                填入融智云告警盒子示例
              </el-button>
            </div>
            <div v-if="channelForm.type === 'voice' || channelForm.type === 'sms'" class="field-tip">
              多个号码会<b>逐个发送</b>（这类设备的号码字段只收单个号码，一次塞多个会被拒）。
            </div>
          </el-form-item>
          <el-form-item label="成功标识">
            <el-input
              v-model="channelForm.success_keyword"
              placeholder="平台返回内容包含该字符串才算成功，如 code:0（留空则只看HTTP状态）"
            />
          </el-form-item>
        </template>

        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="超时(秒)">
              <el-input-number v-model="channelForm.timeout_seconds" :min="3" :max="60" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="启用">
              <el-switch v-model="channelForm.enabled" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="channelForm.remark" placeholder="选填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="channelDialog = false">取消</el-button>
        <el-button @click="previewChannel(channelForm)">预览渲染</el-button>
        <el-button type="primary" :loading="savingChannel" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { alertRuleApi, notifyApi } from '@/api'

const activeTab = ref('rules')

// ---------- 数据 ----------
const rules = ref([])
const channels = ref([])
const logs = ref([])
const types = ref({})
const meta = reactive({ categories: [], metrics: [], operators: [], levels: [] })
const loadingRules = ref(false)
const loadingChannels = ref(false)

// ---------- 规则对话框 ----------
const ruleDialog = ref(false)
const editingRule = ref(null)
const savingRule = ref(false)
const ruleForm = reactive({
  name: '', category: 'server', metric: 'cpu_percent', operator: 'gte',
  warning_threshold: 80, critical_threshold: 90, duration_times: 1,
  silence_minutes: 30, notify_levels: ['warning', 'critical'], channel_ids: [],
  enabled: true, notify_on_recovery: true, remark: ''
})

// ---------- 渠道对话框 ----------
const channelDialog = ref(false)
const editingChannel = ref(null)
const savingChannel = ref(false)
const channelForm = reactive({
  name: '', type: 'dingtalk', enabled: true,
  webhook_url: '', secret: '', at_mobiles: '', at_all: false,
  targets: '',
  http_method: 'POST', http_url: '', http_headers: '', http_body: '',
  success_keyword: '', timeout_seconds: 10, remark: ''
})

const targetsLabel = computed(() => (
  { voice: '被叫号码', sms: '接收号码' }[channelForm.type] || '号码(可选)'
))
const targetsPlaceholder = computed(() => (
  channelForm.type === 'voice'
    ? '值班手机号，多个用逗号分隔，如 13800138000,13900139000'
    : channelForm.type === 'sms'
      ? '接收短信的手机号，多个用逗号分隔'
      : '不需要号码可留空；模板里用 {to} 等变量引用'
))
const urlPlaceholder = computed(() => (
  channelForm.type === 'voice' ? 'http://告警盒子IP/呼叫接口路径'
    : channelForm.type === 'sms' ? 'http://短信平台地址/send'
      : 'https://自定义回调地址'
))
const templatePlaceholder = computed(() => (
  channelForm.type === 'voice'
    ? '如 called={called}&tts={tts}'
    : channelForm.type === 'sms'
      ? '如 phone={phone}&content={content}'
      : '任意格式，如 {"text":"{title}","desp":"{content}"}'
))

// 融智云 MFM-920E / EdgeOS 告警盒子: 实测可用的接口格式, 一键填好
function fillMfmBox() {
  const isVoice = channelForm.type === 'voice'
  channelForm.http_url = 'http://172.16.0.214/cgi-bin/msg_send'
  channelForm.http_method = 'POST'
  channelForm.http_headers = JSON.stringify({
    'Content-Type': 'application/json',
    Cookie: 'auth=YWRtaW46YWRtaW4%3D; roles=Super_Admin'
  }, null, 2)
  channelForm.http_body = isVoice
    ? '{"Type":"Call","To":"{phone}","Text":"{tts}","Encoding":"UTF-8"}'
    : '{"Type":"SMS","To":"{phone}","Text":"{content}","Encoding":"UTF-8"}'
  channelForm.success_keyword = '"Reply":"OK"'
  channelForm.remark = '融智云 MFM-920E 告警盒子（Type: Call=电话 / SMS=短信）'
}

const isRobot = computed(() => ['dingtalk', 'wecom'].includes(channelForm.type))
const currentType = computed(() => types.value[channelForm.type] || null)

const previewCondition = computed(() => {
  const op = ruleForm.operator === 'gte' ? '≥' : '≤'
  const m = (meta.metrics.find((x) => x.value === ruleForm.metric) || {}).label || ruleForm.metric
  if (ruleForm.operator === 'gte') {
    return `当 ${m} ${op} ${ruleForm.warning_threshold}% 时提示；${op} ${ruleForm.critical_threshold}% 时严重告警`
  }
  return `当 ${m} ${op} ${ruleForm.warning_threshold} 时提示；${op} ${ruleForm.critical_threshold} 时严重告警`
})

// ---------- 加载 ----------
async function loadAll() {
  await Promise.all([loadRules(), loadChannels(), loadTypes(), loadMeta(), loadLogs()])
}

async function loadRules() {
  loadingRules.value = true
  try {
    rules.value = await alertRuleApi.list()
  } finally {
    loadingRules.value = false
  }
}

async function loadChannels() {
  loadingChannels.value = true
  try {
    channels.value = await notifyApi.channels()
  } finally {
    loadingChannels.value = false
  }
}

async function loadTypes() {
  types.value = await notifyApi.types()
}

async function loadMeta() {
  const m = await alertRuleApi.meta()
  Object.assign(meta, m)
}

async function loadLogs() {
  logs.value = await notifyApi.logs(100)
}

// ---------- 辅助显示 ----------
const categoryLabel = (v) => (meta.categories.find((c) => c.value === v) || {}).label || v
const metricLabel = (v) => (meta.metrics.find((m) => m.value === v) || {}).label || v
const typeLabel = (v) => (types.value[v] || {}).label || v
const opText = (v) => (v === 'gte' ? '≥' : '≤')
const typeTag = (v) => (
  { dingtalk: 'primary', wecom: 'success', sms: 'warning', voice: 'danger', webhook: 'info' }[v] || 'info'
)
const channelName = (id) => {
  const ch = channels.value.find((c) => c.id === id)
  return ch ? ch.name : `#${id}`
}
const fmtTime = (t) => (t ? String(t).slice(11, 19) : '')

// ---------- 规则操作 ----------
function openRuleDialog(row) {
  editingRule.value = row || null
  Object.assign(ruleForm, row ? {
    name: row.name, category: row.category, metric: row.metric, operator: row.operator,
    warning_threshold: row.warning_threshold, critical_threshold: row.critical_threshold,
    duration_times: row.duration_times, silence_minutes: row.silence_minutes,
    notify_levels: row.notify_levels_list || ['warning', 'critical'],
    channel_ids: row.channel_ids_list || [],
    enabled: row.enabled, notify_on_recovery: row.notify_on_recovery, remark: row.remark
  } : {
    name: '', category: 'server', metric: 'cpu_percent', operator: 'gte',
    warning_threshold: 80, critical_threshold: 90, duration_times: 1,
    silence_minutes: 30, notify_levels: ['warning', 'critical'], channel_ids: [],
    enabled: true, notify_on_recovery: true, remark: ''
  })
  ruleDialog.value = true
}

async function saveRule() {
  if (!ruleForm.name.trim()) {
    ElMessage.warning('请填写规则名称')
    return
  }
  if (ruleForm.operator === 'gte' && ruleForm.critical_threshold < ruleForm.warning_threshold) {
    ElMessage.warning('"大于等于"时，告警阈值应大于提示阈值')
    return
  }
  if (ruleForm.operator === 'lte' && ruleForm.critical_threshold > ruleForm.warning_threshold) {
    ElMessage.warning('"小于等于"时，告警阈值应小于提示阈值')
    return
  }
  savingRule.value = true
  try {
    const payload = { ...ruleForm }
    if (editingRule.value) {
      await alertRuleApi.update(editingRule.value.id, payload)
      ElMessage.success('规则已更新')
    } else {
      await alertRuleApi.create(payload)
      ElMessage.success('规则已创建')
    }
    ruleDialog.value = false
    await loadRules()
  } finally {
    savingRule.value = false
  }
}

async function toggleRule(row) {
  await alertRuleApi.update(row.id, {
    ...row,
    notify_levels: row.notify_levels_list || ['warning', 'critical'],
    channel_ids: row.channel_ids_list || []
  })
  ElMessage.success(row.enabled ? '规则已启用' : '规则已停用')
  await loadRules()
}

async function removeRule(row) {
  await ElMessageBox.confirm(`确定删除规则「${row.name}」？`, '删除确认', { type: 'warning' })
  await alertRuleApi.remove(row.id)
  ElMessage.success('已删除')
  await loadRules()
}

async function handleInitDefaults() {
  const ids = channels.value.filter((c) => c.enabled).map((c) => c.id)
  const res = await alertRuleApi.defaults(ids)
  ElMessage.success(res.message || '默认规则已生成')
  await loadRules()
}

// ---------- 渠道操作 ----------
function onTypeChange() {
  // 切换类型时清掉不适用的字段, 避免残留脏数据
  if (isRobot.value) {
    channelForm.http_url = ''
    channelForm.http_body = ''
    channelForm.http_headers = ''
    channelForm.success_keyword = ''
    channelForm.targets = ''
  } else {
    channelForm.webhook_url = ''
    channelForm.secret = ''
    channelForm.at_mobiles = ''
    channelForm.at_all = false
  }
}

function openChannelDialog(row) {
  editingChannel.value = row || null
  Object.assign(channelForm, row ? { ...row } : {
    name: '', type: 'dingtalk', enabled: true,
    webhook_url: '', secret: '', at_mobiles: '', at_all: false,
    targets: '',
    http_method: 'POST', http_url: '', http_headers: '', http_body: '',
    success_keyword: '', timeout_seconds: 10, remark: ''
  })
  channelDialog.value = true
}

async function saveChannel() {
  if (!channelForm.name.trim()) {
    ElMessage.warning('请填写渠道名称')
    return
  }
  if (isRobot.value && !channelForm.webhook_url.trim()) {
    ElMessage.warning('请填写 Webhook 地址')
    return
  }
  if (!isRobot.value && !channelForm.http_url.trim()) {
    ElMessage.warning('请填写接口地址')
    return
  }
  if (['voice', 'sms'].includes(channelForm.type) && !channelForm.targets.trim()) {
    ElMessage.warning(channelForm.type === 'voice' ? '请填写被叫号码' : '请填写接收号码')
    return
  }
  savingChannel.value = true
  try {
    if (editingChannel.value) {
      await notifyApi.update(editingChannel.value.id, { ...channelForm })
      ElMessage.success('渠道已更新')
    } else {
      await notifyApi.create({ ...channelForm })
      ElMessage.success('渠道已创建')
    }
    channelDialog.value = false
    await loadChannels()
  } finally {
    savingChannel.value = false
  }
}

async function toggleChannel(row) {
  await notifyApi.update(row.id, { ...row })
  ElMessage.success(row.enabled ? '渠道已启用' : '渠道已停用')
}

async function removeChannel(row) {
  await ElMessageBox.confirm(`确定删除渠道「${row.name}」？`, '删除确认', { type: 'warning' })
  await notifyApi.remove(row.id)
  ElMessage.success('已删除')
  await loadChannels()
}

async function testChannel(row) {
  try {
    const res = await notifyApi.test(row.id, {
      title: 'AIOps 测试告警',
      content: '这是一条测试消息，用于验证通知渠道配置是否正确。'
    })
    if (res.ok) {
      ElMessage.success(res.message)
    } else {
      ElMessage.error(res.message + (res.response ? ` | ${res.response}` : ''))
    }
  } catch (e) {
    ElMessage.error('测试发送失败: ' + (e?.message || e))
  }
  await loadLogs()
}

async function previewChannel(row) {
  if (!row || !row.id) {
    ElMessage.warning('请先保存渠道后再预览')
    return
  }
  const r = await notifyApi.preview(row.id)
  ElMessageBox.alert(
    `<pre style="white-space:pre-wrap;word-break:break-all;margin:0">${
      String(r.rendered || '(空)').replace(/</g, '&lt;')
    }</pre>`,
    `渲染预览 · ${r.method || ''} ${r.url || ''}`,
    { dangerouslyUseHTMLString: true, confirmButtonText: '知道了' }
  )
}

async function clearLogs() {
  await ElMessageBox.confirm('确定清空所有发送记录？', '确认', { type: 'warning' })
  await notifyApi.clearLogs()
  ElMessage.success('已清空')
  await loadLogs()
}

onMounted(loadAll)
</script>

<style scoped>
.alert-config {
  padding: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.toolbar-left {
  display: flex;
  gap: 8px;
}
.tip {
  flex: 1;
  min-width: 300px;
}
.tip :deep(.el-alert__title) {
  font-size: 12px;
  line-height: 1.6;
}
.cond {
  display: inline-flex;
  gap: 6px;
  flex-wrap: wrap;
}
.ch-tag {
  margin-right: 4px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.url {
  font-size: 12px;
  color: var(--el-text-color-regular);
  word-break: break-all;
}
.empty-hint {
  padding: 24px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.full {
  width: 100%;
}
.unit {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.form-hint {
  margin: -6px 0 14px 110px;
  font-size: 12px;
  color: var(--el-color-primary);
}
.type-tip {
  margin-bottom: 16px;
}
.type-tip :deep(.el-alert__title) {
  font-size: 12px;
  line-height: 1.7;
}
.type-tip-extra {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}
.tmpl-btns {
  margin-top: 4px;
}
.field-tip {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}
.logs {
  margin-top: 24px;
}
.logs-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}
</style>
