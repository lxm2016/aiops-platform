<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon :size="26" color="#00d4ff"><Platform /></el-icon>
        <span class="logo-text">AIOps 运维平台</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="transparent"
        text-color="#7d92b5"
        active-text-color="#00d4ff"
      >
        <el-menu-item
          v-for="item in menus"
          :key="item.path"
          :index="item.path"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ currentTitle }}</div>
        <div class="header-right">
          <el-icon color="#7d92b5"><User /></el-icon>
          <span class="username">{{ auth.username || 'admin' }}</span>
          <el-button link @click="openPwdDialog">
            <el-icon><Key /></el-icon>&nbsp;修改密码
          </el-button>
          <el-button type="danger" link @click="handleLogout">
            <el-icon><SwitchButton /></el-icon>&nbsp;退出
          </el-button>
        </div>

      </el-header>
      <!-- 连接状态横幅: 后端不可达/不健康时给出明确提示, 恢复后自动刷新页面数据 -->
      <el-alert
        v-if="!connState.online"
        class="conn-banner"
        type="error"
        :closable="false"
        show-icon
      >
        <template #title>
          <span>与服务器连接中断{{ downText ? `（已断开 ${downText}）` : '' }}，系统正在自动重连…</span>
          <span class="conn-hint">若长时间未恢复，请检查后端服务状态</span>
        </template>
      </el-alert>
      <el-alert
        v-else-if="connState.degraded"
        class="conn-banner"
        type="warning"
        :closable="false"
        show-icon
      >
        <template #title>
          服务状态异常：{{ connState.lastMessage || '后端自检未通过，请查看服务日志' }}
        </template>
      </el-alert>

      <el-main class="main">
        <router-view :key="viewKey" />
      </el-main>
    </el-container>

    <!-- 修改密码对话框 -->
    <el-dialog v-model="pwdVisible" title="修改密码" width="420px">
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-width="90px">
        <el-form-item label="原密码" prop="old_password">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="pwdForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input v-model="pwdForm.confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="handleChangePwd">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api'
import { connState } from '@/utils/connection'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// ---------- 后端连接自愈 ----------
// 页面隐藏时浏览器会节流定时器, 所以"长时间无操作"后回来, 数据往往是旧的
// 甚至是加载失败的空白页。这里监听连接监视器广播的恢复事件, 一旦后端恢复
// 就自动重新挂载当前路由组件, 让页面自己重新拉一次数据, 无需用户手动刷新。
const refreshTick = ref(0)
const viewKey = computed(() => `${route.fullPath}#${refreshTick.value}`)
const nowTick = ref(Date.now())
let clockTimer = null

const downText = computed(() => {
  void nowTick.value   // 依赖时钟, 让"已断开 xx 秒"实时变化
  if (!connState.downSince) return ''
  const s = Math.max(0, Math.round((nowTick.value - connState.downSince) / 1000))
  if (s < 60) return `${s} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分钟`
  return `${Math.round(s / 3600)} 小时`
})

function onBackendRestored() {
  ElMessage.success('已重新连接服务器，正在刷新数据')
  refreshTick.value += 1
}

onMounted(() => {
  window.addEventListener('aiops:backend-restored', onBackendRestored)
  // 仅在断开时跑秒级时钟, 避免无谓的渲染开销
  clockTimer = setInterval(() => {
    if (!connState.online) nowTick.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  window.removeEventListener('aiops:backend-restored', onBackendRestored)
  if (clockTimer) clearInterval(clockTimer)
})

// ---------- 修改密码 ----------
const pwdVisible = ref(false)
const pwdSaving = ref(false)
const pwdFormRef = ref(null)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })
const pwdRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '新密码至少6位', trigger: 'blur' }
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (rule, value, cb) =>
        value === pwdForm.new_password ? cb() : cb(new Error('两次输入的密码不一致')),
      trigger: 'blur'
    }
  ]
}

function openPwdDialog() {
  Object.assign(pwdForm, { old_password: '', new_password: '', confirm: '' })
  pwdVisible.value = true
}

async function handleChangePwd() {
  await pwdFormRef.value.validate()
  pwdSaving.value = true
  try {
    await authApi.changePassword({
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password
    })
    ElMessage.success('密码修改成功，请牢记新密码')
    pwdVisible.value = false
  } finally {
    pwdSaving.value = false
  }
}

const menus = [
  { path: '/dashboard', title: '仪表盘', icon: 'Odometer' },
  { path: '/servers', title: '服务器管理', icon: 'Monitor' },
  { path: '/agent-install', title: 'Agent安装', icon: 'Download' },
  { path: '/vmware', title: 'VMware管理', icon: 'Cpu' },
  { path: '/network', title: '网络设备', icon: 'Connection' },
  { path: '/storage', title: '存储设备', icon: 'Box' },
  { path: '/racks', title: '机柜管理', icon: 'Grid' },
  { path: '/env-devices', title: '动环设备', icon: 'Odometer' },
  { path: '/alerts', title: '告警中心', icon: 'Bell' },
  { path: '/alerts/config', title: '告警配置', icon: 'Setting' },
  { path: '/ai', title: 'AI助手', icon: 'ChatDotRound' }
]

const activeMenu = computed(() => {
  if (route.path.startsWith('/servers')) return '/servers'
  return route.path
})

const currentTitle = computed(() => {
  const m = menus.find((i) => activeMenu.value === i.path)
  if (route.name === 'ServerDetail') return '服务器详情'
  return m ? m.title : ''
})

function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout {
  height: 100vh;
}

.aside {
  background: rgba(8, 15, 32, 0.9);
  border-right: 1px solid var(--border-tech);
  display: flex;
  flex-direction: column;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid var(--border-tech);
}

.logo-text {
  font-size: 16px;
  font-weight: 700;
  background: linear-gradient(90deg, #00d4ff, #2f7bff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  white-space: nowrap;
}

.aside .el-menu {
  flex: 1;
  padding-top: 8px;
}

:deep(.el-menu-item.is-active) {
  background: linear-gradient(90deg, rgba(47, 123, 255, 0.18), transparent) !important;
  border-right: 3px solid #00d4ff;
}

:deep(.el-menu-item:hover) {
  background: rgba(47, 123, 255, 0.08) !important;
}

.header {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(10, 17, 34, 0.85);
  border-bottom: 1px solid var(--border-tech);
}

.header-title {
  font-size: 16px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.username {
  color: var(--text-sub);
  font-size: 14px;
}

.main {
  padding: 16px;
  overflow-y: auto;
}

.conn-banner {
  border-radius: 0;
}

.conn-hint {
  margin-left: 12px;
  opacity: 0.75;
  font-size: 12px;
}
</style>
