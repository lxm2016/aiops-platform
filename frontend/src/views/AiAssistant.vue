<template>
  <div class="ai-page">
    <el-card class="tech-card chat-card" shadow="never">
      <template #header>
        <div class="chat-header">
          <div class="chat-title">
            <el-icon color="#00d4ff" :size="20"><ChatDotRound /></el-icon>
            AI 运维助手
            <el-tag size="small" type="primary" effect="plain">基于实时监控上下文</el-tag>
            <el-tag v-if="activeProvider" size="small" type="success" effect="dark">
              <el-icon style="vertical-align:middle"><Connection /></el-icon>&nbsp;{{ activeProvider }}
            </el-tag>
            <el-tag v-else size="small" type="warning" effect="plain">未配置模型</el-tag>
          </div>
          <div class="chat-actions">
            <el-button link type="primary" @click="openLlmDialog">
              <el-icon><Setting /></el-icon>&nbsp;模型配置
            </el-button>
            <el-button link type="danger" @click="handleClear">
              <el-icon><Delete /></el-icon>&nbsp;清空会话
            </el-button>
          </div>
        </div>
      </template>

      <div class="chat-body" ref="bodyRef">
        <div v-if="!messages.length" class="chat-empty">
          <el-icon :size="48" color="#22375c"><ChatDotRound /></el-icon>
          <p>你好，我是 AI 运维助手，可以回答服务器、告警、机房环境等运维问题。</p>
          <div class="quick-questions">
            <el-tag
              v-for="q in quickQuestions"
              :key="q"
              class="quick-tag"
              effect="plain"
              @click="sendQuick(q)"
            >
              {{ q }}
            </el-tag>
          </div>
        </div>

        <div
          v-for="(m, i) in messages"
          :key="i"
          class="msg-row"
          :class="{ 'msg-row-user': m.role === 'user' }"
        >
          <div class="avatar" :class="m.role === 'user' ? 'avatar-user' : 'avatar-ai'">
            <el-icon :size="18">
              <component :is="m.role === 'user' ? 'User' : 'MagicStick'" />
            </el-icon>
          </div>
          <div class="bubble" :class="m.role === 'user' ? 'bubble-user' : 'bubble-ai'">
            <div class="bubble-content">{{ m.content }}</div>
            <div class="bubble-time" v-if="m.time">{{ formatTime(m.time) }}</div>
          </div>
        </div>

        <div v-if="sending" class="msg-row">
          <div class="avatar avatar-ai">
            <el-icon :size="18"><MagicStick /></el-icon>
          </div>
          <div class="bubble bubble-ai typing">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          placeholder="输入运维问题，Enter 发送，Shift+Enter 换行"
          @keydown="handleKeydown"
        />
        <el-button
          type="primary"
          class="send-btn"
          :loading="sending"
          :disabled="!input.trim()"
          @click="send"
        >
          <el-icon><Promotion /></el-icon>&nbsp;发送
        </el-button>
      </div>
    </el-card>

    <!-- 模型配置对话框（多提供商） -->
    <el-dialog v-model="llmVisible" title="AI 模型配置" width="580px">
      <el-form :model="llmForm" label-width="100px">
        <el-form-item label="提供商" required>
          <el-select
            v-model="llmForm.provider"
            placeholder="选择模型提供商"
            style="width: 100%"
            @change="onProviderChange"
          >
            <el-option-group
              v-for="grp in providerGroups"
              :key="grp"
              :label="grp"
            >
              <el-option
                v-for="p in providersByGroup(grp)"
                :key="p.key"
                :label="p.label"
                :value="p.key"
              >
                <span>{{ p.label }}</span>
                <span style="float: right; color: #8aa; font-size: 12px">
                  {{ p.needs_key ? '需 Key' : '免 Key' }}
                </span>
              </el-option>
            </el-option-group>
          </el-select>
          <div class="form-tip">{{ currentProv.doc }}</div>
        </el-form-item>

        <el-form-item label="服务地址" required>
          <el-input
            v-model="llmForm.base_url"
            :placeholder="currentProv.default_base_url"
          />
          <div class="form-tip">OpenAI 兼容接口地址（/v1）。本地如 Ollama/vLLM，在线如各厂商云地址。</div>
        </el-form-item>

        <el-form-item label="API Key" :required="currentProv.needs_key">
          <el-input
            v-model="llmForm.api_key"
            :placeholder="currentProv.key_placeholder"
          />
          <div v-if="!currentProv.needs_key" class="form-tip">
            该提供商无需密钥，留空或保持 EMPTY 即可。
          </div>
        </el-form-item>

        <el-form-item label="模型名称" required>
          <el-select
            v-model="llmForm.model"
            filterable
            allow-create
            default-first-option
            :placeholder="currentProv.model_example"
            style="width: 100%"
            :loading="modelsLoading"
          >
            <el-option
              v-for="m in modelOptions"
              :key="m"
              :label="m"
              :value="m"
            />
          </el-select>
          <div class="form-tip">
            <el-button
              link
              type="primary"
              size="small"
              :loading="modelsLoading"
              @click="handleFetchModels"
            >
              <el-icon><Refresh /></el-icon>&nbsp;获取模型列表
            </el-button>
            从服务拉取可用模型，也可直接输入自定义名称。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="llmTesting" @click="handleTestLlm">测试连接</el-button>
        <el-button @click="llmVisible = false">取消</el-button>
        <el-button type="primary" :loading="llmSaving" @click="handleSaveLlm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { chatApi, settingsApi } from '@/api'
import { formatTime } from '@/utils/format'

const sessionId = 'web-' + (localStorage.getItem('username') || 'default')
const messages = ref([])
const input = ref('')
const sending = ref(false)
const bodyRef = ref(null)

const quickQuestions = [
  '当前有哪些未处理的告警？',
  '服务器整体运行状况如何？',
  '机房温湿度是否正常？',
  'CPU使用率过高该如何排查？'
]

function scrollToBottom() {
  nextTick(() => {
    if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight
  })
}

async function loadHistory() {
  try {
    const history = await chatApi.history(sessionId)
    messages.value = history.map((h) => ({
      role: h.role,
      content: h.content,
      time: h.time
    }))
    scrollToBottom()
  } catch (e) {
    /* 忽略 */
  }
}

function sendQuick(q) {
  input.value = q
  send()
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  messages.value.push({ role: 'user', content: text, time: new Date().toISOString() })
  input.value = ''
  sending.value = true
  scrollToBottom()
  try {
    const res = await chatApi.send(text, sessionId)
    messages.value.push({ role: 'assistant', content: res.reply, time: new Date().toISOString() })
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      content: '抱歉，AI 服务暂时不可用，请稍后重试。',
      time: new Date().toISOString()
    })
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

async function handleClear() {
  await ElMessageBox.confirm('确定清空当前会话记录？', '提示', { type: 'warning' })
  messages.value = []
  ElMessage.success('会话已清空')
}

// ---------- 模型配置（多提供商） ----------
const llmVisible = ref(false)
const llmSaving = ref(false)
const llmTesting = ref(false)
const activeProvider = ref('')          // 当前激活的提供商显示名
const providers = ref([])               // 提供商注册表 (来自后端)
const modelOptions = ref([])            // "获取模型列表" 拉到的模型
const modelsLoading = ref(false)
const appliedDefaultUrl = ref('')       // 上次随提供商自动填入的 base_url, 用于判断是否可被覆盖
const llmForm = reactive({
  provider: 'openai_compatible',
  base_url: '',
  model: '',
  api_key: 'EMPTY'
})

// 提供商分组 (保持注册表顺序)
const providerGroups = computed(() => {
  const seen = []
  for (const p of providers.value) {
    if (!seen.includes(p.group)) seen.push(p.group)
  }
  return seen
})
function providersByGroup(grp) {
  return providers.value.filter((p) => p.group === grp)
}
// 当前所选提供商元数据
const currentProv = computed(() => {
  const p = providers.value.find((x) => x.key === llmForm.provider)
  return p || {
    label: '',
    doc: '',
    needs_key: false,
    key_placeholder: '无鉴权填 EMPTY',
    default_base_url: 'http://localhost:8000/v1',
    model_example: 'model-name'
  }
})

// 切换提供商: 仅当用户未手动改过地址时, 自动回填该提供商的默认地址
function onProviderChange() {
  const def = currentProv.value.default_base_url
  if (!llmForm.base_url || llmForm.base_url === appliedDefaultUrl.value) {
    llmForm.base_url = def
  }
  appliedDefaultUrl.value = def
  // 免 Key 的本地服务, 清空密钥占位
  if (!currentProv.value.needs_key) {
    llmForm.api_key = 'EMPTY'
  }
  modelOptions.value = []
}

async function handleFetchModels() {
  if (!llmForm.base_url) {
    ElMessage.warning('请先填写服务地址')
    return
  }
  modelsLoading.value = true
  try {
    const res = await settingsApi.models({
      provider: llmForm.provider,
      base_url: llmForm.base_url,
      api_key: llmForm.api_key || 'EMPTY'
    })
    if (res.ok) {
      modelOptions.value = res.models || []
      ElMessage.success(res.detail || `已拉取 ${modelOptions.value.length} 个模型`)
    } else {
      ElMessage.error(res.detail || '拉取模型列表失败')
    }
  } catch (e) {
    ElMessage.error('拉取模型列表失败，请检查地址与网络')
  } finally {
    modelsLoading.value = false
  }
}

async function openLlmDialog() {
  try {
    const [cfg, provs] = await Promise.all([
      settingsApi.getLlm(),
      settingsApi.providers()
    ])
    providers.value = provs || []
    Object.assign(llmForm, cfg)
    if (!llmForm.api_key) llmForm.api_key = 'EMPTY'
    // 记录当前默认地址, 供切换时判断
    const p = providers.value.find((x) => x.key === llmForm.provider)
    appliedDefaultUrl.value = p ? p.default_base_url : ''
    const label = p ? p.label : (cfg.provider || '')
    activeProvider.value = label
  } catch (e) {
    /* 忽略, 保持默认 */
  }
  llmVisible.value = true
}

async function handleTestLlm() {
  if (!llmForm.base_url || !llmForm.model) {
    ElMessage.warning('请先填写服务地址和模型名称')
    return
  }
  llmTesting.value = true
  try {
    const res = await settingsApi.testLlm(llmForm)
    if (res.ok) ElMessage.success(res.detail)
    else ElMessage.error(res.detail)
  } finally {
    llmTesting.value = false
  }
}

async function handleSaveLlm() {
  if (!llmForm.provider) {
    ElMessage.warning('请选择提供商')
    return
  }
  if (!llmForm.base_url || !llmForm.model) {
    ElMessage.warning('服务地址和模型名称不能为空')
    return
  }
  llmSaving.value = true
  try {
    await settingsApi.saveLlm(llmForm)
    const p = providers.value.find((x) => x.key === llmForm.provider)
    activeProvider.value = p ? p.label : llmForm.provider
    ElMessage.success('模型配置已保存，立即生效')
    llmVisible.value = false
  } finally {
    llmSaving.value = false
  }
}

onMounted(loadHistory)
</script>

<style scoped>
.ai-page {
  height: calc(100vh - 92px);
  display: flex;
}

.chat-card {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.chat-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.chat-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.form-tip {
  font-size: 12px;
  color: var(--text-sub);
  line-height: 1.5;
  margin-top: 4px;
}

.chat-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.chat-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-sub);
}

.quick-questions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  max-width: 520px;
}

.quick-tag {
  cursor: pointer;
}

.quick-tag:hover {
  color: #00d4ff;
  border-color: #00d4ff;
}

.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.msg-row-user {
  flex-direction: row-reverse;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.avatar-ai {
  background: rgba(0, 212, 255, 0.15);
  color: #00d4ff;
}

.avatar-user {
  background: rgba(47, 123, 255, 0.2);
  color: #2f7bff;
}

.bubble {
  max-width: 68%;
  padding: 10px 14px;
  border-radius: 10px;
  line-height: 1.7;
  font-size: 14px;
}

.bubble-ai {
  background: rgba(18, 33, 60, 0.8);
  border: 1px solid var(--border-tech);
  border-top-left-radius: 2px;
}

.bubble-user {
  background: linear-gradient(135deg, rgba(47, 123, 255, 0.35), rgba(0, 212, 255, 0.25));
  border: 1px solid rgba(47, 123, 255, 0.4);
  border-top-right-radius: 2px;
}

.bubble-content {
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble-time {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 6px;
}

.typing span {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 5px;
  border-radius: 50%;
  background: #00d4ff;
  animation: blink 1.2s infinite;
}

.typing span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes blink {
  0%, 80%, 100% {
    opacity: 0.2;
  }
  40% {
    opacity: 1;
  }
}

.chat-input {
  display: flex;
  gap: 10px;
  padding: 14px 16px;
  border-top: 1px solid var(--border-tech);
  align-items: flex-end;
}

.chat-input :deep(.el-textarea) {
  flex: 1;
}

.send-btn {
  height: 52px;
  padding: 0 20px;
}
</style>
