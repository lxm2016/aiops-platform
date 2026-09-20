<template>
  <div ref="containerRef" class="rack-3d-scene-container" :class="{ 'bigscreen': bigScreen }">
    <!-- 3D Canvas由Three.js自动挂载 -->
    <div class="scene-toolbar">
      <el-tooltip content="重置视角" placement="bottom">
        <el-button :icon="Refresh" circle size="small" @click="resetCamera" />
      </el-tooltip>
      <el-tooltip content="俯视模式" placement="bottom">
        <el-button :icon="Grid" circle size="small" @click="topView" />
      </el-tooltip>
      <el-tooltip :content="autoRotate ? '停止旋转' : '自动旋转'" placement="bottom">
        <el-button :icon="VideoPlay" circle size="small" :type="autoRotate ? 'primary' : 'default'" @click="autoRotate = !autoRotate" />
      </el-tooltip>
      <el-tooltip content="全屏" placement="bottom">
        <el-button :icon="FullScreen" circle size="small" @click="toggleFullscreen" />
      </el-tooltip>
    </div>

    <!-- 悬浮信息卡 -->
    <transition name="fade">
      <div v-if="hoverInfo.visible" class="hover-card" :style="{ left: hoverInfo.x + 'px', top: hoverInfo.y + 'px' }">
        <div class="hc-title">{{ hoverInfo.name }}</div>
        <div class="hc-row" v-if="hoverInfo.row">列: <b>{{ hoverInfo.row }}</b></div>
        <div class="hc-row">高度: <b>{{ hoverInfo.uHeight }}U</b></div>
        <div class="hc-row">设备: <b>{{ hoverInfo.deviceCount }}台</b></div>
        <div class="hc-tip">点击查看3D详情</div>
      </div>
    </transition>

    <!-- 搜索结果高亮提示 -->
    <transition name="fade">
      <div v-if="searchResult" class="search-banner">
        <el-icon color="#00d4ff"><Aim /></el-icon>
        <span>已定位: {{ searchResult.name }} ({{ searchResult.row }})</span>
        <el-button link size="small" @click="$emit('clear-search')">清除</el-button>
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { Refresh, Grid, VideoPlay, FullScreen, Aim } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

const props = defineProps({
  racks: { type: Array, default: () => [] },
  devicesMap: { type: Object, default: () => ({}) },
  bigScreen: { type: Boolean, default: false },
  highlightRackId: { type: Number, default: null }
})
const emit = defineEmits(['rack-click', 'clear-search'])

const containerRef = ref(null)
const autoRotate = ref(false)
const searchResult = ref(null)

let scene, camera, renderer, controls, raycaster, mouse
let rackGroup, floor, gridHelper
let rackMeshes = [] // {rack, group, ledMeshes[]}
let animationId = null
let highlightRing = null

const hoverInfo = reactive({ visible: false, x: 0, y: 0, name: '', row: '', uHeight: 0, deviceCount: 0 })

// 设备类型颜色
const TYPE_COLOR = {
  server: 0x2f7bff,
  switch: 0x00c48f,
  storage: 0xff9f43,
  security: 0xa55eea,
  other: 0x5d7092
}

// ========== Three.js 场景初始化 ==========
function initScene() {
  const container = containerRef.value
  const w = container.clientWidth
  const h = container.clientHeight

  // 场景
  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x03060e)
  scene.fog = new THREE.Fog(0x03060e, 800, 2500)

  // 相机
  camera = new THREE.PerspectiveCamera(50, w / h, 1, 5000)
  camera.position.set(600, 500, 800)
  camera.lookAt(0, 100, 0)

  // 渲染器
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setSize(w, h)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  container.appendChild(renderer.domElement)

  // 控制器
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 200
  controls.maxDistance = 2000
  controls.maxPolarAngle = Math.PI / 2 - 0.05
  controls.target.set(0, 100, 0)

  // 光照
  const ambient = new THREE.AmbientLight(0x404868, 0.6)
  scene.add(ambient)

  const dirLight = new THREE.DirectionalLight(0xffffff, 0.8)
  dirLight.position.set(500, 800, 300)
  dirLight.castShadow = true
  dirLight.shadow.mapSize.width = 2048
  dirLight.shadow.mapSize.height = 2048
  dirLight.shadow.camera.left = -1000
  dirLight.shadow.camera.right = 1000
  dirLight.shadow.camera.top = 1000
  dirLight.shadow.camera.bottom = -1000
  dirLight.shadow.camera.near = 100
  dirLight.shadow.camera.far = 2000
  scene.add(dirLight)

  // 补光(青色，模拟机房冷光)
  const pointLight1 = new THREE.PointLight(0x00d4ff, 0.5, 1500)
  pointLight1.position.set(-300, 400, 200)
  scene.add(pointLight1)

  const pointLight2 = new THREE.PointLight(0x2f7bff, 0.3, 1500)
  pointLight2.position.set(300, 400, -200)
  scene.add(pointLight2)

  // 地面
  createFloor()

  // 机柜组
  rackGroup = new THREE.Group()
  scene.add(rackGroup)

  // 射线检测
  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  // 事件
  renderer.domElement.addEventListener('mousemove', onMouseMove)
  renderer.domElement.addEventListener('click', onClick)
  window.addEventListener('resize', onResize)

  buildRacks()
  animate()
}

// ========== 地面 ==========
function createFloor() {
  // 大地面
  const floorGeo = new THREE.PlaneGeometry(3000, 3000)
  const floorMat = new THREE.MeshStandardMaterial({
    color: 0x0a1424,
    metalness: 0.3,
    roughness: 0.8
  })
  floor = new THREE.Mesh(floorGeo, floorMat)
  floor.rotation.x = -Math.PI / 2
  floor.receiveShadow = true
  scene.add(floor)

  // 网格
  gridHelper = new THREE.GridHelper(3000, 60, 0x00d4ff, 0x1a2a44)
  gridHelper.position.y = 0.5
  gridHelper.material.opacity = 0.3
  gridHelper.material.transparent = true
  scene.add(gridHelper)
}

// ========== 机柜3D模型 ==========
function createRackModel(rack, position) {
  const group = new THREE.Group()
  const uHeight = rack.u_height || 42
  const rackW = 60 // 机柜宽
  const rackD = 80 // 机柜深
  const uSize = 4 // 每U高度
  const rackH = uHeight * uSize + 16 // 总高(含顶底)
  const frameThick = 3

  // 机柜材质
  const frameMat = new THREE.MeshStandardMaterial({
    color: 0x1a2a44,
    metalness: 0.7,
    roughness: 0.3
  })
  const bodyMat = new THREE.MeshStandardMaterial({
    color: 0x0d1830,
    metalness: 0.5,
    roughness: 0.5
  })

  // 顶盖
  const topGeo = new THREE.BoxGeometry(rackW + 4, 6, rackD + 4)
  const top = new THREE.Mesh(topGeo, frameMat)
  top.position.y = rackH - 3
  top.castShadow = true
  top.receiveShadow = true
  group.add(top)

  // 底座
  const bottomGeo = new THREE.BoxGeometry(rackW + 4, 8, rackD + 4)
  const bottom = new THREE.Mesh(bottomGeo, frameMat)
  bottom.position.y = 4
  bottom.castShadow = true
  bottom.receiveShadow = true
  group.add(bottom)

  // 左侧板
  const sideGeo = new THREE.BoxGeometry(frameThick, rackH - 14, rackD)
  const left = new THREE.Mesh(sideGeo, bodyMat)
  left.position.set(-rackW / 2, rackH / 2, 0)
  left.castShadow = true
  group.add(left)

  // 右侧板
  const right = new THREE.Mesh(sideGeo, bodyMat)
  right.position.set(rackW / 2, rackH / 2, 0)
  right.castShadow = true
  group.add(right)

  // 后板
  const backGeo = new THREE.BoxGeometry(rackW, rackH - 14, frameThick)
  const back = new THREE.Mesh(backGeo, bodyMat)
  back.position.set(0, rackH / 2, -rackD / 2)
  back.castShadow = true
  group.add(back)

  // 内部空间(深色背景)
  const interiorGeo = new THREE.PlaneGeometry(rackW - 6, rackH - 14)
  const interiorMat = new THREE.MeshBasicMaterial({ color: 0x05080f })
  const interior = new THREE.Mesh(interiorGeo, interiorMat)
  interior.position.set(0, rackH / 2, -rackD / 2 + 2)
  group.add(interior)

  // 玻璃前门(半透明)
  const doorGeo = new THREE.PlaneGeometry(rackW, rackH - 14)
  const doorMat = new THREE.MeshStandardMaterial({
    color: 0x00d4ff,
    transparent: true,
    opacity: 0.08,
    metalness: 0.1,
    roughness: 0.1,
    side: THREE.DoubleSide
  })
  const door = new THREE.Mesh(doorGeo, doorMat)
  door.position.set(0, rackH / 2, rackD / 2 - 1)
  group.add(door)

  // 机柜名称标牌(顶部蓝条)
  const nameBarGeo = new THREE.PlaneGeometry(rackW - 4, 5)
  const nameBarMat = new THREE.MeshBasicMaterial({ color: 0x00d4ff })
  const nameBar = new THREE.Mesh(nameBarGeo, nameBarMat)
  nameBar.position.set(0, rackH - 10, rackD / 2 - 0.5)
  group.add(nameBar)

  // 设备面板
  const devices = (props.devicesMap[rack.id] || []).filter(d => d.side === 'front')
  const ledMeshes = []

  devices.forEach(dev => {
    const devU = dev.u_size || 1
    const devH = devU * uSize - 1
    const devW = rackW - 8
    const devY = (dev.u_start - 1) * uSize + devH / 2 + 8

    const color = TYPE_COLOR[dev.device_type] || 0x5d7092
    const devMat = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.6,
      roughness: 0.4,
      emissive: color,
      emissiveIntensity: 0.15
    })
    const devGeo = new THREE.BoxGeometry(devW, devH, 3)
    const devMesh = new THREE.Mesh(devGeo, devMat)
    devMesh.position.set(0, devY, rackD / 2 - 2)
    devMesh.castShadow = true
    devMesh.userData = { type: 'device', dev, rackId: rack.id }
    group.add(devMesh)

    // LED灯
    const ledGeo = new THREE.SphereGeometry(1.2, 8, 8)
    const ledColor = dev.status === 'critical' ? 0xff4d5e :
                      dev.status === 'warning' ? 0xffb020 :
                      dev.status === 'offline' ? 0x4a5a70 : 0x00e396
    const ledMat = new THREE.MeshBasicMaterial({ color: ledColor })
    const led = new THREE.Mesh(ledGeo, ledMat)
    led.position.set(-devW / 2 + 4, devY, rackD / 2 - 0.5)
    group.add(led)
    ledMeshes.push({ mesh: led, dev, baseColor: ledColor })
  })

  // 设置位置
  group.position.set(position.x, 0, position.z)
  group.userData = { type: 'rack', rack, ledMeshes }
  group.castShadow = true

  return { group, ledMeshes }
}

// ========== 构建所有机柜 ==========
function buildRacks() {
  // 清除旧机柜
  if (rackGroup) {
    while (rackGroup.children.length > 0) {
      const child = rackGroup.children[0]
      rackGroup.remove(child)
      disposeObject(child)
    }
  }
  rackMeshes = []

  if (!props.racks.length) return

  // 按列分组
  const rows = {}
  for (const r of props.racks) {
    const key = r.row_name || 'A'
    ;(rows[key] = rows[key] || []).push(r)
  }

  const rowKeys = Object.keys(rows).sort()
  const rackSpacing = 100 // 机柜间距
  const rowSpacing = 200 // 列间距
  const rackD = 80

  let rowIdx = 0
  for (const rowName of rowKeys) {
    const racksInRow = rows[rowName].sort((a, b) => a.name.localeCompare(b.name))
    const rowWidth = racksInRow.length * rackSpacing
    const startX = -rowWidth / 2 + rackSpacing / 2
    const z = (rowIdx - (rowKeys.length - 1) / 2) * rowSpacing

    racksInRow.forEach((rack, i) => {
      const x = startX + i * rackSpacing
      const { group, ledMeshes } = createRackModel(rack, { x, z })
      rackGroup.add(group)
      rackMeshes.push({ rack, group, ledMeshes })
    })

    rowIdx++
  }

  // 调整相机到合适位置
  if (props.racks.length > 0) {
    const totalRacks = props.racks.length
    const dist = Math.max(400, totalRacks * 50)
    camera.position.set(dist * 0.7, dist * 0.6, dist)
    controls.target.set(0, 100, 0)
    controls.update()
  }
}

// ========== 高亮机柜 ==========
function highlightRack(rackId) {
  // 清除旧高亮
  if (highlightRing) {
    scene.remove(highlightRing)
    disposeObject(highlightRing)
    highlightRing = null
  }

  if (!rackId) {
    searchResult.value = null
    return
  }

  const target = rackMeshes.find(rm => rm.rack.id === rackId)
  if (!target) return

  // 添加高亮环
  const ringGeo = new THREE.RingGeometry(70, 80, 32)
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0x00d4ff,
    transparent: true,
    opacity: 0.6,
    side: THREE.DoubleSide
  })
  highlightRing = new THREE.Mesh(ringGeo, ringMat)
  highlightRing.rotation.x = -Math.PI / 2
  highlightRing.position.set(target.group.position.x, 1, target.group.position.z)
  scene.add(highlightRing)

  // 相机移动到机柜
  const targetPos = target.group.position
  const camPos = new THREE.Vector3(targetPos.x + 200, 200, targetPos.z + 250)
  animateCamera(camPos, new THREE.Vector3(targetPos.x, 100, targetPos.z))

  const rack = target.rack
  searchResult.value = { name: rack.name, row: rack.row_name }
}

// ========== 相机动画 ==========
function animateCamera(targetPos, targetLook) {
  const startPos = camera.position.clone()
  const startTarget = controls.target.clone()
  const duration = 800
  const startTime = Date.now()

  function step() {
    const elapsed = Date.now() - startTime
    const t = Math.min(elapsed / duration, 1)
    const ease = 1 - Math.pow(1 - t, 3) // easeOutCubic

    camera.position.lerpVectors(startPos, targetPos, ease)
    controls.target.lerpVectors(startTarget, targetLook, ease)
    controls.update()

    if (t < 1) requestAnimationFrame(step)
  }
  step()
}

// ========== 鼠标事件 ==========
function onMouseMove(event) {
  if (!containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const groups = rackMeshes.map(rm => rm.group)
  const intersects = raycaster.intersectObjects(groups, true)

  if (intersects.length > 0) {
    // 找到所属机柜
    let obj = intersects[0].object
    while (obj && !obj.userData?.type?.includes('rack') && obj.parent) {
      obj = obj.parent
    }
    const rackData = obj?.userData?.rack || obj?.parent?.userData?.rack
    // 直接从rackMeshes找
    const rackMesh = rackMeshes.find(rm => rm.group === obj || rm.group.children.includes(obj))
    if (rackMesh) {
      const rack = rackMesh.rack
      const devCount = (props.devicesMap[rack.id] || []).length
      hoverInfo.visible = true
      hoverInfo.x = event.clientX - rect.left + 15
      hoverInfo.y = event.clientY - rect.top + 15
      hoverInfo.name = rack.name
      hoverInfo.row = rack.row_name
      hoverInfo.uHeight = rack.u_height
      hoverInfo.deviceCount = devCount
      renderer.domElement.style.cursor = 'pointer'
      return
    }
  }

  hoverInfo.visible = false
  renderer.domElement.style.cursor = 'default'
}

function onClick(event) {
  if (!containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const groups = rackMeshes.map(rm => rm.group)
  const intersects = raycaster.intersectObjects(groups, true)

  if (intersects.length > 0) {
    let obj = intersects[0].object
    const rackMesh = rackMeshes.find(rm =>
      rm.group === obj || rm.group.children.includes(obj) || isDescendant(rm.group, obj)
    )
    if (rackMesh) {
      emit('rack-click', rackMesh.rack)
    }
  }
}

function isDescendant(parent, child) {
  let node = child
  while (node) {
    if (node === parent) return true
    node = node.parent
  }
  return false
}

// ========== 渲染循环 ==========
function animate() {
  animationId = requestAnimationFrame(animate)

  if (autoRotate.value) {
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.5
  } else {
    controls.autoRotate = false
  }

  // LED呼吸效果
  const time = Date.now() * 0.001
  rackMeshes.forEach(rm => {
    rm.ledMeshes.forEach(lm => {
      if (lm.dev.status !== 'offline' && lm.dev.status !== 'critical') {
        const intensity = 0.5 + 0.5 * Math.sin(time * 2 + lm.dev.id * 0.5)
        lm.mesh.material.color.setHex(lm.baseColor)
        lm.mesh.material.color.multiplyScalar(0.5 + intensity * 0.5)
      }
    })
  })

  // 高亮环旋转
  if (highlightRing) {
    highlightRing.rotation.z += 0.02
    const scale = 1 + 0.1 * Math.sin(time * 3)
    highlightRing.scale.set(scale, scale, scale)
  }

  controls.update()
  renderer.render(scene, camera)
}

// ========== 工具方法 ==========
function onResize() {
  if (!containerRef.value) return
  const w = containerRef.value.clientWidth
  const h = containerRef.value.clientHeight
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}

function resetCamera() {
  animateCamera(new THREE.Vector3(600, 500, 800), new THREE.Vector3(0, 100, 0))
}

function topView() {
  animateCamera(new THREE.Vector3(0, 1500, 1), new THREE.Vector3(0, 0, 0))
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    containerRef.value.requestFullscreen()
  } else {
    document.exitFullscreen()
  }
}

function disposeObject(obj) {
  obj.traverse(child => {
    if (child.geometry) child.geometry.dispose()
    if (child.material) {
      if (Array.isArray(child.material)) child.material.forEach(m => m.dispose())
      else child.material.dispose()
    }
  })
}

// ========== Watch ==========
watch(() => props.racks, () => {
  if (scene) buildRacks()
}, { deep: true })

watch(() => props.devicesMap, () => {
  if (scene) buildRacks()
}, { deep: true })

watch(() => props.highlightRackId, (newId) => {
  if (scene) highlightRack(newId)
})

watch(() => props.bigScreen, (val) => {
  if (val && containerRef.value) {
    setTimeout(onResize, 100)
  }
})

// ========== 生命周期 ==========
onMounted(() => {
  initScene()
})

onBeforeUnmount(() => {
  if (animationId) cancelAnimationFrame(animationId)
  window.removeEventListener('resize', onResize)
  if (renderer) {
    renderer.domElement.removeEventListener('mousemove', onMouseMove)
    renderer.domElement.removeEventListener('click', onClick)
    renderer.dispose()
    if (containerRef.value && renderer.domElement.parentNode) {
      containerRef.value.removeChild(renderer.domElement)
    }
  }
  scene = null
  camera = null
  renderer = null
  controls = null
})
</script>

<style scoped>
.rack-3d-scene-container {
  position: relative;
  width: 100%;
  height: 600px;
  min-height: 400px;
  border: 1px solid var(--border-tech, #1a2a44);
  border-radius: 8px;
  overflow: hidden;
  background: #03060e;
}

.rack-3d-scene-container.bigscreen {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 3000;
  border: none;
  border-radius: 0;
  height: 100vh;
}

.scene-toolbar {
  position: absolute;
  top: 12px;
  right: 12px;
  display: flex;
  gap: 8px;
  z-index: 10;
}

.hover-card {
  position: absolute;
  z-index: 20;
  background: rgba(6, 13, 26, 0.95);
  border: 1px solid rgba(0, 212, 255, 0.4);
  border-radius: 6px;
  padding: 8px 14px;
  min-width: 140px;
  pointer-events: none;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5), 0 0 12px rgba(0, 212, 255, 0.15);
  backdrop-filter: blur(10px);
}

.hc-title {
  font-size: 14px;
  font-weight: 600;
  color: #00d4ff;
  margin-bottom: 4px;
}

.hc-row {
  font-size: 12px;
  color: rgba(220, 232, 248, 0.7);
}

.hc-row b {
  color: #dce8f8;
}

.hc-tip {
  margin-top: 4px;
  font-size: 11px;
  color: rgba(0, 212, 255, 0.6);
}

.search-banner {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: rgba(0, 212, 255, 0.1);
  border: 1px solid rgba(0, 212, 255, 0.3);
  border-radius: 20px;
  font-size: 13px;
  color: #00d4ff;
  z-index: 10;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
