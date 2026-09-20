<template>
  <div
    class="stat-card tech-card"
    :class="{ 'stat-card--link': to, 'stat-card--disabled': disabled }"
    @click="handleClick"
  >
    <div class="stat-icon" :style="{ background: iconBg, color: color }">
      <el-icon :size="24"><component :is="icon" /></el-icon>
    </div>
    <div class="stat-body">
      <div class="stat-value num-highlight" :style="{ color }">
        {{ value }}<span class="stat-unit">{{ unit }}</span>
      </div>
      <div class="stat-label">
        {{ label }}
        <el-icon v-if="to" class="stat-arrow"><ArrowRight /></el-icon>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  label: { type: String, default: '' },
  value: { type: [Number, String], default: 0 },
  unit: { type: String, default: '' },
  icon: { type: String, default: 'DataLine' },
  color: { type: String, default: '#00d4ff' },
  to: { type: String, default: '' },        // 点击跳转的路由路径
  query: { type: Object, default: () => ({}) }, // 路由查询参数
  disabled: { type: Boolean, default: false }  // 点击无效
})

const emit = defineEmits(['click'])

const router = useRouter()
const iconBg = computed(() => props.color + '1f')

function handleClick() {
  if (props.disabled) return
  if (props.to) {
    router.push({ path: props.to, query: props.query })
  }
  emit('click')
}
</script>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  transition: transform 0.2s, box-shadow 0.2s;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 28px rgba(0, 212, 255, 0.12);
}

.stat-card--link {
  cursor: pointer;
}

.stat-card--link:hover {
  border-color: var(--accent);
}

.stat-card--disabled {
  cursor: default;
  opacity: 0.7;
}

.stat-card--disabled:hover {
  transform: none;
  box-shadow: none;
}

.stat-arrow {
  font-size: 12px;
  margin-left: 4px;
  opacity: 0;
  transition: opacity 0.2s;
}

.stat-card:hover .stat-arrow {
  opacity: 1;
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-value {
  font-size: 26px;
  line-height: 1.2;
}

.stat-unit {
  font-size: 13px;
  margin-left: 3px;
  color: var(--text-sub);
  font-weight: 400;
}

.stat-label {
  font-size: 13px;
  color: var(--text-sub);
  margin-top: 4px;
  display: flex;
  align-items: center;
}
</style>
