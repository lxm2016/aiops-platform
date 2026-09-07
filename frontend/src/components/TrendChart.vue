<template>
  <div ref="chartRef" :style="{ width: '100%', height: height + 'px' }"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, default: () => ({}) },
  height: { type: Number, default: 300 }
})

const chartRef = ref(null)
let chart = null
let resizeObserver = null

const baseOption = {
  backgroundColor: 'transparent',
  textStyle: { color: '#7d92b5' },
  grid: { left: 46, right: 20, top: 40, bottom: 30 },
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(14, 26, 48, 0.95)',
    borderColor: '#1c2f4f',
    textStyle: { color: '#dce8f8' }
  },
  legend: { textStyle: { color: '#7d92b5' }, top: 5 }
}

function render() {
  if (!chart) return
  const merged = { ...baseOption, ...props.option }
  if (props.option.tooltip) merged.tooltip = { ...baseOption.tooltip, ...props.option.tooltip }
  chart.setOption(merged, true)
}

onMounted(async () => {
  await nextTick()
  chart = echarts.init(chartRef.value)
  render()
  resizeObserver = new ResizeObserver(() => chart && chart.resize())
  resizeObserver.observe(chartRef.value)
  window.addEventListener('resize', handleResize)
})

function handleResize() {
  chart && chart.resize()
}

watch(
  () => props.option,
  () => render(),
  { deep: true }
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  resizeObserver && resizeObserver.disconnect()
  chart && chart.dispose()
})
</script>
