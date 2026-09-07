<template>
  <div class="agent-install">
    <el-card class="card">
      <template #header>
        <div class="card-header">
          <span>Agent 安装（5秒采集一次，自动注册到平台）</span>
        </div>
      </template>

      <el-alert type="info" :closable="false" class="tip">
        平台地址: <b>{{ baseUrl }}</b>　|　采集间隔: <b>5秒</b>　|　安装后自动上线，无需手动添加服务器
      </el-alert>

      <!-- Linux -->
      <div class="section">
        <div class="section-title">
          <el-tag type="success">Linux</el-tag>
          <span class="sub">CentOS / openEuler / Rocky / Ubuntu / 龙蜥 —— 一行命令安装</span>
        </div>
        <div class="cmd-box">
          <code>{{ linuxCmd }}</code>
          <el-button size="small" @click="copy(linuxCmd)">复制</el-button>
        </div>
        <div class="note">在被监控服务器上以 root 执行，自动下载、离线装依赖、注册开机自启并启动。</div>
      </div>

      <!-- Windows -->
      <div class="section">
        <div class="section-title">
          <el-tag type="primary">Windows</el-tag>
          <span class="sub">2008 / 2012 / 2016 / 2019 —— 解压即用</span>
        </div>
        <div class="steps">
          <div class="step">
            <span class="num">1</span>
            <span>下载安装包：</span>
            <el-button type="primary" size="small" @click="downloadWin">
              <el-icon><Download /></el-icon>&nbsp;aiops-agent-windows.zip
            </el-button>
          </div>
          <div class="step">
            <span class="num">2</span>
            <span>解压到任意目录（如 C:\aiops-agent），双击运行 <b>install.bat</b></span>
          </div>
          <div class="step">
            <span class="num">3</span>
            <span>按提示输入平台地址 <b>{{ baseUrl }}</b>，回车即完成安装并开机自启</span>
          </div>
        </div>
        <div class="note">前提：目标机器已安装 Python 3.8+（Win2008 用 3.8），并勾选 Add to PATH。</div>
      </div>

      <!-- 卸载 -->
      <div class="section">
        <div class="section-title">
          <el-tag type="danger">卸载</el-tag>
        </div>
        <div class="cmd-box">
          <code>Linux: systemctl disable --now aiops-agent && rm -rf /opt/aiops-agent /etc/systemd/system/aiops-agent.service</code>
        </div>
        <div class="cmd-box">
          <code>Windows: schtasks /delete /tn "AIOpsAgent" /f （然后删除安装目录）</code>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'

const baseUrl = computed(() => window.location.origin)

const linuxCmd = computed(
  () => `curl -sSfL '${baseUrl.value}/api/agents/install.sh' | sudo bash -s -- --server '${baseUrl.value}'`
)

function copy(text) {
  navigator.clipboard.writeText(text).then(() => {
    ElMessage.success('已复制到剪贴板')
  }).catch(() => {
    ElMessage.warning('复制失败，请手动选择复制')
  })
}

function downloadWin() {
  window.open(`${baseUrl.value}/api/agents/package/windows`, '_blank')
}
</script>

<style scoped>
.agent-install {
  max-width: 980px;
}

.card-header {
  font-weight: 600;
}

.tip {
  margin-bottom: 20px;
}

.section {
  margin-bottom: 26px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  font-size: 15px;
  font-weight: 600;
}

.section-title .sub {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-sub);
}

.cmd-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(0, 212, 255, 0.06);
  border: 1px solid var(--border-tech);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 8px;
}

.cmd-box code {
  flex: 1;
  font-family: Consolas, monospace;
  font-size: 13px;
  color: #7ee0ff;
  word-break: break-all;
}

.note {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 4px;
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.step {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
}

.step .num {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: linear-gradient(135deg, #00d4ff, #2f7bff);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex-shrink: 0;
}
</style>
