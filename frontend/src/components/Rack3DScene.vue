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

    <!-- 区域层级导航 -->
    <div class="zone-crumb">
      <el-button link size="small" :type="focusedZone ? '' : 'primary'" @click="focusOverview">全部院区</el-button>
      <template v-if="focusedZone">
        <span class="crumb-sep">/</span>
        <span class="crumb-cur">{{ focusedZone }}</span>
        <el-button link size="small" type="primary" @click="focusOverview">返回总览</el-button>
      </template>
      <span v-else class="crumb-hint">点击院区标牌或地面区域可放大进入</span>
    </div>

    <!-- 悬浮信息卡 -->
    <transition name="fade">
      <div v-if="hoverInfo.visible" class="hover-card" :style="{ left: hoverInfo.x + 'px', top: hoverInfo.y + 'px' }">
        <div class="hc-title">{{ hoverInfo.name }}</div>
        <div class="hc-row" v-if="hoverInfo.row">区域: <b>{{ hoverInfo.row }}</b></div>
        <div class="hc-row">高度: <b>{{ hoverInfo.uHeight }}U</b></div>
        <div class="hc-row">设备: <b>{{ hoverInfo.deviceCount }}台</b> · 已用 <b>{{ hoverInfo.usedU }}U</b></div>
        <div class="hc-row">
          状态:
          <b :style="{ color: hoverInfo.statusCss }">{{ hoverInfo.statusText }}</b>
          <span v-if="hoverInfo.alertCount" style="color:#ffb020"> · {{ hoverInfo.alertCount }}条告警</span>
        </div>
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
const focusedZone = ref(null)

let scene, camera, renderer, controls, raycaster, mouse
let rackGroup, zoneGroup, floor, gridHelper
let rackMeshes = [] // {rack, group, ledMeshes[]}
let zonePads = []   // {key, name, center:{x,z}, w, d}
let hasFitCamera = false
let lastLayoutKey = ''
let animationId = null
let highlightRing = null

const STATUS_TEXT = {
  online: '正常', offline: '离线', warning: '告警', critical: '严重', unknown: '未纳管'
}

const hoverInfo = reactive({
  visible: false, x: 0, y: 0, name: '', row: '', uHeight: 0, deviceCount: 0,
  usedU: 0, statusText: '', statusCss: '#7d92b5', alertCount: 0
})

// 设备类型颜色
const TYPE_COLOR = {
  server: 0x2f7bff,
  switch: 0x00c48f,
  storage: 0xff9f43,
  security: 0xa55eea,
  other: 0x5d7092
}

// 状态色（机柜/设备共用）
const STATUS_COLOR = {
  online: 0x00e396,
  offline: 0x4a5a70,
  warning: 0xffb020,
  critical: 0xff4d5e,
  unknown: 0x5d7092
}
const STATUS_RANK = { unknown: 0, online: 1, offline: 2, warning: 3, critical: 4 }

// 纹理缓存（机柜名标牌复用，避免重复创建 canvas）
const texCache = new Map()

function makeLabelTexture(key, w, h, draw) {
  if (texCache.has(key)) return texCache.get(key)
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  draw(c.getContext('2d'), w, h)
  const tex = new THREE.CanvasTexture(c)
  tex.anisotropy = 4
  tex.needsUpdate = true
  texCache.set(key, tex)
  return tex
}

/** 计算机柜整体状态：取其中设备的最高等级 */
function rackStatus(rack) {
  const devs = props.devicesMap[rack.id] || []
  let best = 'unknown'
  for (const d of devs) {
    const s = d.status || 'unknown'
    if ((STATUS_RANK[s] ?? 0) > (STATUS_RANK[best] ?? 0)) best = s
  }
  return rack.status || best
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

  // 院区/区域标牌与地面组
  zoneGroup = new THREE.Group()
  scene.add(zoneGroup)

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

  // 机柜名称标牌(带编号文字与状态条)
  const st = rackStatus(rack)
  const stHex = STATUS_COLOR[st] ?? STATUS_COLOR.unknown
  const stCss = '#' + stHex.toString(16).padStart(6, '0')
  const labelTex = makeLabelTexture(`rk-${rack.id}-${rack.name}-${st}`, 256, 64, (ctx, w2, h2) => {
    ctx.fillStyle = '#0b182c'
    ctx.fillRect(0, 0, w2, h2)
    ctx.fillStyle = stCss
    ctx.fillRect(0, 0, w2, 6)
    ctx.strokeStyle = 'rgba(0,212,255,0.45)'
    ctx.lineWidth = 2
    ctx.strokeRect(1, 1, w2 - 2, h2 - 2)
    ctx.fillStyle = '#dce8f8'
    ctx.font = 'bold 30px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(String(rack.name || ''), w2 / 2, h2 / 2 + 5)
  })
  const namePlate = new THREE.Mesh(
    new THREE.PlaneGeometry(rackW - 6, 13),
    new THREE.MeshBasicMaterial({ map: labelTex, transparent: true })
  )
  namePlate.position.set(0, rackH - 12, rackD / 2 - 0.4)
  group.add(namePlate)

  // 异常机柜: 用细边框 + 底部光条表达, 不整体染成告警色
  // (大面积染色会让大屏长期刺眼, 这里采用克制的表达)
  if (st === 'critical' || st === 'warning') {
    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(rackW + 5, rackH, rackD + 5)),
      new THREE.LineBasicMaterial({
        color: stHex,
        transparent: true,
        opacity: st === 'critical' ? 0.8 : 0.5
      })
    )
    edge.position.set(0, rackH / 2, 0)
    group.add(edge)

    const bar = new THREE.Mesh(
      new THREE.PlaneGeometry(rackW + 2, 3),
      new THREE.MeshBasicMaterial({ color: stHex, transparent: true, opacity: 0.9 })
    )
    bar.position.set(0, 2, rackD / 2 + 0.3)
    group.add(bar)
  }

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

// ========== 构建所有机柜（按院区/区域分区布置） ==========
function buildRacks() {
  // 清除旧机柜与旧区域
  if (rackGroup) {
    while (rackGroup.children.length > 0) {
      const child = rackGroup.children[0]
      rackGroup.remove(child)
      disposeObject(child)
    }
  }
  if (zoneGroup) {
    while (zoneGroup.children.length > 0) {
      const child = zoneGroup.children[0]
      zoneGroup.remove(child)
      disposeObject(child)
    }
  }
  rackMeshes = []
  zonePads = []

  if (!props.racks.length) return

  // 按所属院区/机房位置(row_name)分区, 各区域在地面独立成块
  const zones = {}
  for (const r of props.racks) {
    const key = (r.row_name || '').trim() || '未分区'
    ;(zones[key] = zones[key] || []).push(r)
  }
  const zoneKeys = Object.keys(zones).sort()

  const rackSpacing = 100  // 机柜间距
  const rowSpacing = 170   // 区域内排距
  const perRow = 6         // 每排最多机柜数
  const zoneGap = 340      // 院区之间的间距

  const zoneInfos = zoneKeys.map(key => {
    const racksInZone = zones[key].sort((a, b) => a.name.localeCompare(b.name))
    const cols = Math.min(racksInZone.length, perRow)
    const rowsCount = Math.ceil(racksInZone.length / perRow)
    return {
      key,
      racks: racksInZone,
      cols,
      rowsCount,
      w: cols * rackSpacing + 160,
      d: rowsCount * rowSpacing + 120
    }
  })

  const totalW = zoneInfos.reduce((s, z) => s + z.w, 0) + zoneGap * (zoneInfos.length - 1)
  let cursorX = -totalW / 2

  for (const z of zoneInfos) {
    const cx = cursorX + z.w / 2
    const cz = 0

    z.racks.forEach((rack, i) => {
      const col = i % perRow
      const row = Math.floor(i / perRow)
      const x = cx - (z.cols * rackSpacing) / 2 + rackSpacing / 2 + col * rackSpacing
      const rz = (row - (z.rowsCount - 1) / 2) * rowSpacing
      const { group, ledMeshes } = createRackModel(rack, { x, z: rz })
      rackGroup.add(group)
      rackMeshes.push({ rack, group, ledMeshes, zone: z.key })
    })

    createZonePad(z, cx, cz)
    cursorX += z.w + zoneGap
  }

  // 首次构建或布局变化时才调整相机, 避免定时刷新把用户视角拉回去
  const layoutKey = zoneKeys.join('|') + '#' + props.racks.length
  if (!hasFitCamera || layoutKey !== lastLayoutKey) {
    fitOverview(true)
    hasFitCamera = true
    lastLayoutKey = layoutKey
  }
  // 当前聚焦的区域被删除时回到总览
  if (focusedZone.value && !zonePads.some(p => p.key === focusedZone.value)) {
    focusedZone.value = null
  }
}

// ========== 院区标牌与地面区域 ==========
function createZonePad(z, cx, cz) {
  const devCount = z.racks.reduce((s, r) => s + (props.devicesMap[r.id] || []).length, 0)

  const pad = new THREE.Mesh(
    new THREE.PlaneGeometry(z.w, z.d),
    new THREE.MeshBasicMaterial({ color: 0x0b2242, transparent: true, opacity: 0.32 })
  )
  pad.rotation.x = -Math.PI / 2
  pad.position.set(cx, 0.8, cz)
  pad.userData = { type: 'zone', zoneKey: z.key }
  zoneGroup.add(pad)

  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.PlaneGeometry(z.w, z.d)),
    new THREE.LineBasicMaterial({ color: 0x00d4ff, transparent: true, opacity: 0.4 })
  )
  edge.rotation.x = -Math.PI / 2
  edge.position.set(cx, 1.0, cz)
  zoneGroup.add(edge)

  // 区域标牌(Sprite, 始终面向相机): 院区名 + 机柜/设备数
  const tex = makeLabelTexture(`zone-${z.key}-${z.racks.length}-${devCount}`, 512, 128, (ctx, w, h) => {
    ctx.fillStyle = 'rgba(7,18,36,0.88)'
    ctx.fillRect(0, 0, w, h)
    ctx.strokeStyle = 'rgba(0,212,255,0.55)'
    ctx.lineWidth = 3
    ctx.strokeRect(2, 2, w - 4, h - 4)
    ctx.fillStyle = '#00d4ff'
    ctx.font = 'bold 46px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(z.key, w / 2, 44)
    ctx.fillStyle = 'rgba(220,232,248,0.75)'
    ctx.font = '28px sans-serif'
    ctx.fillText(`${z.racks.length} 台机柜 · ${devCount} 台设备`, w / 2, 92)
  })
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }))
  sprite.scale.set(300, 75, 1)
  sprite.position.set(cx, 320, cz)
  sprite.userData = { type: 'zone', zoneKey: z.key }
  zoneGroup.add(sprite)

  zonePads.push({ key: z.key, name: z.key, center: { x: cx, z: cz }, w: z.w, d: z.d })
}

// ========== 院区聚焦 / 总览 ==========
function focusZone(key) {
  const z = zonePads.find(p => p.key === key)
  if (!z) return
  focusedZone.value = key
  const dist = Math.max(z.w, z.d) * 1.35 + 260
  animateCamera(
    new THREE.Vector3(z.center.x + dist * 0.42, dist * 0.5, z.center.z + dist * 0.78),
    new THREE.Vector3(z.center.x, 90, z.center.z)
  )
}

function fitOverview(immediate) {
  focusedZone.value = null
  if (!zonePads.length) return
  const minX = Math.min(...zonePads.map(p => p.center.x - p.w / 2))
  const maxX = Math.max(...zonePads.map(p => p.center.x + p.w / 2))
  const totalW = maxX - minX
  const dist = Math.max(520, totalW * 0.85)
  const camPos = new THREE.Vector3(dist * 0.35, dist * 0.5, dist * 0.8)
  const look = new THREE.Vector3(0, 80, 0)
  if (immediate) {
    camera.position.copy(camPos)
    controls.target.copy(look)
    controls.update()
  } else {
    animateCamera(camPos, look)
  }
}

function focusOverview() {
  fitOverview(false)
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
      const devs = props.devicesMap[rack.id] || []
      const st = rackStatus(rack)
      hoverInfo.visible = true
      hoverInfo.x = event.clientX - rect.left + 15
      hoverInfo.y = event.clientY - rect.top + 15
      hoverInfo.name = rack.name
      hoverInfo.row = rack.row_name
      hoverInfo.uHeight = rack.u_height
      hoverInfo.deviceCount = devs.length
      hoverInfo.usedU = devs
        .filter(d => d.side === 'front')
        .reduce((s, d) => s + (d.u_size || 1), 0)
      hoverInfo.statusText = STATUS_TEXT[st] || '未纳管'
      hoverInfo.statusCss = '#' + (STATUS_COLOR[st] ?? STATUS_COLOR.unknown).toString(16).padStart(6, '0')
      hoverInfo.alertCount = devs.reduce((s, d) => s + (d.alert_count || 0), 0)
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
      // 相机先旋转聚焦到该机柜, 随后打开3D详情
      const p = rackMesh.group.position
      animateCamera(
        new THREE.Vector3(p.x + 170, 170, p.z + 240),
        new THREE.Vector3(p.x, 100, p.z)
      )
      setTimeout(() => emit('rack-click', rackMesh.rack), 600)
      return
    }
  }

  // 没点到机柜 → 看是否点在院区标牌/地面区域上, 是则放大进入该区域
  if (zoneGroup) {
    const zHits = raycaster.intersectObjects(zoneGroup.children, true)
    for (const h of zHits) {
      let node = h.object
      while (node && node.userData?.type !== 'zone') node = node.parent
      if (node?.userData?.zoneKey) {
        focusZone(node.userData.zoneKey)
        return
      }
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
  focusOverview()
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
  if (zoneGroup) {
    zoneGroup.traverse(o => {
      if (o.geometry) o.geometry.dispose()
      if (o.material) o.material.dispose()
    })
  }
  texCache.forEach(t => t.dispose())
  texCache.clear()
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

.zone-crumb {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  background: rgba(6, 13, 26, 0.85);
  border: 1px solid rgba(0, 212, 255, 0.3);
  border-radius: 16px;
  backdrop-filter: blur(8px);
}
.crumb-sep { color: rgba(125, 146, 181, 0.6); font-size: 12px; }
.crumb-cur { font-size: 13px; font-weight: 600; color: #00d4ff; }
.crumb-hint { font-size: 12px; color: rgba(125, 146, 181, 0.75); }

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
