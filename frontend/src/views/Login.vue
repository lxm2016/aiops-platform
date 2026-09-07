<template>
  <div class="login-page">
    <div class="login-box tech-card">
      <div class="login-header">
        <el-icon :size="40" color="#00d4ff"><Platform /></el-icon>
        <h1>AIOps 智能运维平台</h1>
        <p>统一监控 · 智能告警 · AI 运维助手</p>
      </div>
      <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="handleLogin">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" clearable />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            class="login-btn"
            :loading="loading"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <div class="login-tip">默认账号：admin / admin123</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const formRef = ref(null)
const loading = ref(false)

const form = reactive({ username: 'admin', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

async function handleLogin() {
  await formRef.value.validate()
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (e) {
    // 错误已由拦截器提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(800px 400px at 70% 10%, rgba(0, 212, 255, 0.12), transparent),
    radial-gradient(700px 400px at 20% 90%, rgba(47, 123, 255, 0.14), transparent),
    var(--bg-deep);
}

.login-box {
  width: 400px;
  padding: 40px 36px 24px;
}

.login-header {
  text-align: center;
  margin-bottom: 28px;
}

.login-header h1 {
  margin: 12px 0 6px;
  font-size: 22px;
  background: linear-gradient(90deg, #00d4ff, #2f7bff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.login-header p {
  margin: 0;
  color: var(--text-sub);
  font-size: 13px;
}

.login-btn {
  width: 100%;
  letter-spacing: 8px;
  font-weight: 600;
}

.login-tip {
  text-align: center;
  color: var(--text-sub);
  font-size: 12px;
  margin-top: 8px;
}
</style>
