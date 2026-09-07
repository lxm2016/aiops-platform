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
      <el-main class="main">
        <router-view />
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
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

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
  { path: '/env', title: '温湿度监控', icon: 'Sunny' },
  { path: '/alerts', title: '告警中心', icon: 'Bell' },
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
</style>
