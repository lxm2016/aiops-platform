<template>
  <div class="stat-card tech-card">
    <div class="stat-icon" :style="{ background: iconBg, color: color }">
      <el-icon :size="24"><component :is="icon" /></el-icon>
    </div>
    <div class="stat-body">
      <div class="stat-value num-highlight" :style="{ color }">
        {{ value }}<span class="stat-unit">{{ unit }}</span>
      </div>
      <div class="stat-label">{{ label }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, default: '' },
  value: { type: [Number, String], default: 0 },
  unit: { type: String, default: '' },
  icon: { type: String, default: 'DataLine' },
  color: { type: String, default: '#00d4ff' }
})

const iconBg = computed(() => props.color + '1f')
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
}
</style>
