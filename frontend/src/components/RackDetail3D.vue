<template>
  <div ref="wrapRef" class="rd3-wrap">
    <div ref="containerRef" class="rd3-canvas"></div>

    <!-- 工具栏 -->
    <div class="rd3-toolbar">
      <el-tooltip content="正视图" placement="bottom">
        <el-button :icon="Aim" circle size="small" @click="frontView" />
      </el-tooltip>
      <el-tooltip content="侧视（看设备纵深）" placement="bottom">
        <el-button :icon="View" circle size="small" @click="sideView" />
      </el-tooltip>
      <el-tooltip content="俯视" placement="bottom">
        <el-button :icon="Grid" circle size="small" @click="topView" />
      </el-tooltip>
      <el-tooltip :content="autoRotate ? '停止旋转' : '自动旋转'" placement="bottom">
        <el-button
          :icon="VideoPlay" circle size="small"
          :type="autoRotate ? 'primary' : 'default'"
          @click="autoRotate = !autoRotate"
        />
      </el-tooltip>
      <el-tooltip content="全屏" placement="bottom">
        <el-button :icon="FullScreen" circle size="small" @click="toggleFullscreen" />
      </el-tooltip>
    </div>

    <!-- 机柜信息角标 -->
    <div class="rd3-badge">
      <div class="rd3-name">{{ rack?.name || '-' }}</div>
      <div class="rd3-meta">
        {{ rack?.u_height || 0 }}U · {{ sideLabel }} · {{ devices.length }}台
      </div>
      <div class="rd3-util">
        已用 {{ usedU }}U / {{ rack?.u_height || 0 }}U
        <span class="rd3-util-bar"><i :style="{ width: utilPercent + '%' }"></i></span>
      </div>
    </div>

    <!-- 图例 -->
    <div class="rd3-legend">
      <span v-for="s in LEGEND" :key="s.k">
        <i :style="{ background: s.c }"></i>{{ s.t }}
      </span>
    </div>

    <!-- 悬停卡 -->
    <transition name="rd3-fade">
      <div
        v-if="hover.visible"
        class="rd3-hover"
        :style="{ left: hover.x + 'px', top: hover.y + 'px' }"
      >
        <div class="rd3-h-title">
          <i class="rd3-h-dot" :style="{ background: statusColor(hover.dev) }"></i>
          {{ hover.dev.name }}
        </div>
        <div class="rd3-h-row">类型 {{ typeLabel(hover.dev.device_type) }}</div>
        <div class="rd3-h-row">U位 {{ hover.dev.u_start }}–{{ hover.dev.u_start + hover.dev.u_size - 1 }}U（{{ hover.dev.u_size }}U）</div>
        <div class="rd3-h-row" v-if="hover.dev.ip">IP {{ hover.dev.ip }}</div>
        <div class="rd3-h-row" v-if="hover.dev.remark">备注 {{ hover.dev.remark }}</div>
        <div class="rd3-h-status" :style="{ color: statusColor(hover.dev) }">
          {{ statusLabel(hover.dev) }}
          <template v-if="hover.dev.alert_count"> · {{ hover.dev.alert_count }}条未恢复告警</template>
        </div>
      </div>
    </transition>

    <div v-if="!devices.length" class="rd3-empty">该侧暂无设备</div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { Aim, View, Grid, VideoPlay, FullScreen } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

const props = defineProps({
  rack: { type: Object, default: null },
  devices: { type: Array, default: () => [] },
  side: { type: String, default: 'front' }
})
const emit = defineEmits(['device-click'])

const wrapRef = ref(null)
const containerRef = ref(null)
const autoRotate = ref(false)

const LEGEND = [
  { k: 'online', t: '在线', c: '#00e396' },
  { k: 'warning', t: '告警', c: '#ffb020' },
  { k: 'critical', t: '严重', c: '#ff4d5e' },
  { k: 'offline', t: '离线', c: '#4a5a70' },
  { k: 'unknown', t: '未纳管', c: '#5d7092' }
]

const TYPE_LABEL = { server: '服务器', switch: '交换机', storage: '存储', security: '安全设备', other: '其他' }
const TYPE_COLOR = {
  server: 0x2f7bff, switch: 0x00c48f, storage: 0xff9f43,
  security: 0xa55eea, other: 0x5d7092
}
const STATUS_HEX = {
  online: '#00e396', offline: '#4a5a70', warning: '#ffb020',
  critical: '#ff4d5e', unknown: '#5d7092'
}
const STATUS_COLOR = {
  online: 0x00e396, offline: 0x4a5a70, warning: 0xffb020,
  critical: 0xff4d5e, unknown: 0x5d7092
}

const sideLabel = computed(() => (props.side === 'front' ? '前侧' : '后侧'))
const usedU = computed(() =>
  props.devices.filter(d => d.side === props.side).reduce((s, d) => s + (d.u_size || 1), 0)
)
const utilPercent = computed(() => {
  const total = props.rack?.u_height || 1
  return Math.min(100, Math.round((usedU.value / total) * 100))
})

function typeLabel(t) { return TYPE_LABEL[t] || t || '其他' }
function statusColor(d) { return STATUS_HEX[d?.status] || STATUS_HEX.unknown }
function statusLabel(d) {
  return { online: '在线', offline: '离线', warning: '告警', critical: '严重', unknown: '未纳管' }[d?.status] || '未纳管'
}

// ================= Three.js =================
let scene, camera, renderer, controls, raycaster, mouse
let rackRoot, deviceRoot
let devMeshes = []      // { mesh, dev }
let animationId = null
const texCache = new Map()

const hover = reactive({ visible: false, x: 0, y: 0, dev: null })

const U_H = 4.0          // 每U高度
const RACK_W = 62        // 机柜外宽
const RACK_D = 92        // 机柜深
const FRAME = 3          // 立柱厚度
const BASE_H = 6         // 底座高
const TOP_H = 6          // 顶盖高

function makeTexture(key, w, h, draw) {
  if (texCache.has(key)) return texCache.get(key)
  const c = document.createElement('canvas')
  c.width = w; c.height = h
  draw(c.getContext('2d'), w, h)
  const tex = new THREE.CanvasTexture(c)
  tex.anisotropy = 4
  tex.needsUpdate = true
  texCache.set(key, tex)
  return tex
}

/** U位刻度尺纹理 */
function uScaleTexture(uH) {
  const unitPx = 12
  return makeTexture(`uscale-${uH}`, 96, uH * unitPx, (ctx, w, h) => {
    ctx.fillStyle = '#080e1a'
    ctx.fillRect(0, 0, w, h)
    ctx.strokeStyle = 'rgba(0,212,255,0.35)'
    ctx.lineWidth = 1
    for (let u = 1; u <= uH; u++) {
      const top = h - u * unitPx
      ctx.beginPath()
      ctx.moveTo(w - 18, top)
      ctx.lineTo(w, top)
      ctx.stroke()
      const show = u % 2 === 0 || u === 1 || u === uH
      if (show) {
        ctx.fillStyle = 'rgba(170,205,240,0.85)'
        ctx.font = 'bold 9px monospace'
        ctx.textAlign = 'right'
        ctx.fillText(String(u), w - 22, top + unitPx * 0.72)
      }
    }
  })
}

/** 安装导轨纹理（每U三个方孔，接近真实机柜） */
function railTexture(uH) {
  const unitPx = 12
  return makeTexture(`rail-${uH}`, 32, uH * unitPx, (ctx, w, h) => {
    ctx.fillStyle = '#16233a'
    ctx.fillRect(0, 0, w, h)
    ctx.fillStyle = '#04080f'
    for (let i = 0; i < uH; i++) {
      for (let k = 0; k < 3; k++) {
        const y = h - (i + 1) * unitPx + unitPx * (0.18 + k * 0.28)
        ctx.fillRect(8, y, 16, unitPx * 0.17)
      }
    }
  })
}

/** 设备前面板纹理：按设备类型画出不同的真实面板细节 */
function panelTexture(type, uSize, status) {
  const key = `panel-${type}-${uSize}`
  const w = 512
  const h = Math.max(48, uSize * 56)
  return makeTexture(key, w, h, (ctx) => {
    // 金属底 + 高光
    const g = ctx.createLinearGradient(0, 0, 0, h)
    g.addColorStop(0, '#3c4a63')
    g.addColorStop(0.35, '#2a3550')
    g.addColorStop(1, '#1b2438')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, w, h)
    ctx.strokeStyle = 'rgba(255,255,255,0.10)'
    ctx.lineWidth = 1
    ctx.strokeRect(0.5, 0.5, w - 1, h - 1)

    // 左侧固定：把手 + 电源灯
    ctx.fillStyle = 'rgba(10,16,28,0.9)'
    ctx.fillRect(10, h * 0.28, 14, h * 0.44)
    ctx.fillStyle = STATUS_HEX[status] || STATUS_HEX.unknown
    ctx.beginPath()
    ctx.arc(36, h * 0.5, Math.max(2.5, h * 0.06), 0, Math.PI * 2)
    ctx.fill()

    if (type === 'server') {
      // 服务器：大面积通风孔 + 右侧硬盘位
      ctx.fillStyle = 'rgba(0,0,0,0.55)'
      const cols = 26, rows = Math.max(2, Math.floor(h / 9))
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          ctx.fillRect(58 + c * 11, 6 + r * (h - 12) / rows, 6, Math.max(2, (h - 12) / rows - 3))
        }
      }
      ctx.fillStyle = 'rgba(120,150,185,0.35)'
      for (let i = 0; i < 4; i++) ctx.fillRect(w - 120 + i * 28, h * 0.3, 22, h * 0.4)
    } else if (type === 'switch') {
      // 交换机：密集网口阵列
      const rows = Math.max(1, Math.min(2, uSize))
      ctx.fillStyle = 'rgba(0,0,0,0.7)'
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < 24; c++) {
          const y = h * (0.22 + r * 0.36)
          ctx.fillRect(58 + c * 17, y, 12, h * 0.22)
        }
      }
      ctx.fillStyle = 'rgba(0,230,150,0.55)'
      for (let c = 0; c < 24; c++) ctx.fillRect(58 + c * 17 + 13, h * 0.16, 2, 3)
    } else if (type === 'storage') {
      // 存储：前面板盘位阵列
      const rows = Math.max(2, Math.min(4, uSize))
      const cols = 12
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          ctx.fillStyle = '#151d2e'
          ctx.fillRect(56 + c * 35, 5 + r * (h - 10) / rows, 31, (h - 10) / rows - 4)
          ctx.fillStyle = 'rgba(0,212,255,0.28)'
          ctx.fillRect(56 + c * 35, 5 + r * (h - 10) / rows, 31, 2)
        }
      }
    } else if (type === 'security') {
      ctx.fillStyle = 'rgba(0,0,0,0.7)'
      for (let c = 0; c < 8; c++) ctx.fillRect(58 + c * 22, h * 0.32, 15, h * 0.3)
      ctx.fillStyle = 'rgba(165,94,234,0.5)'
      ctx.fillRect(w - 90, h * 0.3, 70, h * 0.4)
    } else {
      ctx.fillStyle = 'rgba(255,255,255,0.06)'
      ctx.fillRect(56, h * 0.25, w - 120, h * 0.5)
    }
  })
}

function initScene() {
  const el = containerRef.value
  const w = el.clientWidth || 600
  const h = el.clientHeight || 480

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x04070f)
  scene.fog = new THREE.Fog(0x04070f, 500, 1600)

  camera = new THREE.PerspectiveCamera(42, w / h, 1, 4000)

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setSize(w, h)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  el.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 120
  controls.maxDistance = 900
  controls.maxPolarAngle = Math.PI * 0.495

  scene.add(new THREE.AmbientLight(0x5a6a8a, 0.75))

  const key = new THREE.DirectionalLight(0xffffff, 0.9)
  key.position.set(220, 320, 260)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  scene.add(key)

  const fill = new THREE.PointLight(0x00d4ff, 0.55, 900)
  fill.position.set(-180, 200, 160)
  scene.add(fill)

  const rim = new THREE.PointLight(0x2f7bff, 0.4, 900)
  rim.position.set(180, 160, -180)
  scene.add(rim)

  // 地面（弱化，突出机柜本体）
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(1400, 1400),
    new THREE.MeshStandardMaterial({ color: 0x070c16, metalness: 0.4, roughness: 0.85 })
  )
  floor.rotation.x = -Math.PI / 2
  floor.receiveShadow = true
  scene.add(floor)

  const grid = new THREE.GridHelper(1400, 40, 0x0d2136, 0x0a1626)
  grid.position.y = 0.6
  scene.add(grid)

  rackRoot = new THREE.Group()
  scene.add(rackRoot)
  deviceRoot = new THREE.Group()
  rackRoot.add(deviceRoot)

  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  renderer.domElement.addEventListener('mousemove', onMouseMove)
  renderer.domElement.addEventListener('click', onClick)
  renderer.domElement.addEventListener('mouseleave', () => { hover.visible = false })
  window.addEventListener('resize', onResize)

  build()
  frontView(true)
  animate()
}

function build() {
  // 清理
  while (rackRoot.children.length) {
    const c = rackRoot.children[0]
    rackRoot.remove(c)
    disposeObject(c)
  }
  devMeshes = []
  deviceRoot = new THREE.Group()
  rackRoot.add(deviceRoot)

  const rack = props.rack
  if (!rack) return
  const uH = rack.u_height || 42
  const innerH = uH * U_H
  const totalH = innerH + BASE_H + TOP_H
  const y0 = BASE_H            // 1U 起始高度

  const frameMat = new THREE.MeshStandardMaterial({ color: 0x22304a, metalness: 0.75, roughness: 0.32 })
  const panelMat = new THREE.MeshStandardMaterial({ color: 0x101c30, metalness: 0.55, roughness: 0.55 })

  // 四立柱
  const colGeo = new THREE.BoxGeometry(FRAME, totalH, FRAME)
  const dx = RACK_W / 2 - FRAME / 2
  const dz = RACK_D / 2 - FRAME / 2
  ;[[-dx, -dz], [dx, -dz], [-dx, dz], [dx, dz]].forEach(([x, z]) => {
    const col = new THREE.Mesh(colGeo, frameMat)
    col.position.set(x, totalH / 2, z)
    col.castShadow = true
    rackRoot.add(col)
  })

  // 顶盖 / 底座
  const capGeo = new THREE.BoxGeometry(RACK_W, TOP_H, RACK_D)
  const top = new THREE.Mesh(capGeo, frameMat)
  top.position.set(0, totalH - TOP_H / 2, 0)
  top.castShadow = true
  rackRoot.add(top)

  const base = new THREE.Mesh(capGeo, frameMat)
  base.position.set(0, BASE_H / 2, 0)
  base.castShadow = true
  rackRoot.add(base)

  // 后板（后侧视图时作为背板）
  const back = new THREE.Mesh(
    new THREE.PlaneGeometry(RACK_W - FRAME * 2, innerH),
    new THREE.MeshStandardMaterial({ color: 0x0a1220, metalness: 0.4, roughness: 0.7, side: THREE.DoubleSide })
  )
  back.position.set(0, y0 + innerH / 2, -RACK_D / 2 + FRAME)
  rackRoot.add(back)

  // 两侧板
  const sideGeo = new THREE.PlaneGeometry(RACK_D - FRAME * 2, innerH)
  const sideMat = panelMat
  const left = new THREE.Mesh(sideGeo, sideMat)
  left.rotation.y = Math.PI / 2
  left.position.set(-RACK_W / 2 + FRAME, y0 + innerH / 2, 0)
  rackRoot.add(left)
  const right = new THREE.Mesh(sideGeo, sideMat)
  right.rotation.y = -Math.PI / 2
  right.position.set(RACK_W / 2 - FRAME, y0 + innerH / 2, 0)
  rackRoot.add(right)

  // 安装导轨（左右各一条，带方孔）
  const railTex = railTexture(uH)
  const railGeo = new THREE.PlaneGeometry(RACK_D - FRAME * 2 - 6, innerH)
  const railMat = new THREE.MeshStandardMaterial({ map: railTex, metalness: 0.6, roughness: 0.5, side: THREE.DoubleSide })
  const railL = new THREE.Mesh(railGeo, railMat)
  railL.rotation.y = Math.PI / 2
  railL.position.set(-RACK_W / 2 + FRAME + 1.5, y0 + innerH / 2, 0)
  rackRoot.add(railL)
  const railR = new THREE.Mesh(railGeo, railMat)
  railR.rotation.y = -Math.PI / 2
  railR.position.set(RACK_W / 2 - FRAME - 1.5, y0 + innerH / 2, 0)
  rackRoot.add(railR)

  // U位刻度尺（贴在左前立柱外侧，始终可见）
  const scaleTex = uScaleTexture(uH)
  const scaleMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(16, innerH),
    new THREE.MeshBasicMaterial({ map: scaleTex, transparent: true })
  )
  scaleMesh.position.set(-RACK_W / 2 - 9, y0 + innerH / 2, RACK_D / 2 - 2)
  rackRoot.add(scaleMesh)

  // 机柜名标牌
  const nameTex = makeTexture(`name-${rack.name}`, 256, 64, (ctx, w2, h2) => {
    ctx.fillStyle = '#0d2036'
    ctx.fillRect(0, 0, w2, h2)
    ctx.strokeStyle = '#00d4ff'
    ctx.lineWidth = 3
    ctx.strokeRect(1.5, 1.5, w2 - 3, h2 - 3)
    ctx.fillStyle = '#00d4ff'
    ctx.font = 'bold 30px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(String(rack.name || ''), w2 / 2, h2 / 2 + 1)
  })
  const nameMesh = new THREE.Mesh(
    new THREE.PlaneGeometry(RACK_W * 0.72, RACK_W * 0.18),
    new THREE.MeshBasicMaterial({ map: nameTex, transparent: true })
  )
  nameMesh.position.set(0, totalH + 8, 0)
  nameMesh.rotation.x = -Math.PI / 9
  rackRoot.add(nameMesh)

  // 设备
  const devs = props.devices.filter(d => d.side === props.side)
  const innerW = RACK_W - FRAME * 2 - 10
  const devD = RACK_D * 0.62

  devs.forEach(dev => {
    const size = dev.u_size || 1
    const devH = size * U_H - 0.7
    const y = y0 + (dev.u_start - 1) * U_H + devH / 2 + 0.35
    const zFront = RACK_D / 2 - FRAME - devD / 2 - 1
    const z = props.side === 'front' ? zFront : -zFront

    const g = new THREE.Group()

    // 机箱本体
    const bodyColor = TYPE_COLOR[dev.device_type] || TYPE_COLOR.other
    const body = new THREE.Mesh(
      new THREE.BoxGeometry(innerW, devH, devD),
      new THREE.MeshStandardMaterial({
        color: bodyColor, metalness: 0.55, roughness: 0.42,
        emissive: bodyColor, emissiveIntensity: 0.06
      })
    )
    body.castShadow = true
    body.receiveShadow = true
    g.add(body)

    // 面板（前后各贴一张，正面朝外）
    const faceTex = panelTexture(dev.device_type, size, dev.status)
    const faceMat = new THREE.MeshStandardMaterial({ map: faceTex, metalness: 0.5, roughness: 0.5 })
    const faceGeo = new THREE.PlaneGeometry(innerW - 2, devH - 0.8)
    const faceFront = new THREE.Mesh(faceGeo, faceMat)
    faceFront.position.set(0, 0, props.side === 'front' ? devD / 2 + 0.3 : -devD / 2 - 0.3)
    if (props.side === 'back') faceFront.rotation.y = Math.PI
    g.add(faceFront)

    // 状态灯（面板左侧）
    const ledColor = STATUS_COLOR[dev.status] || STATUS_COLOR.unknown
    const led = new THREE.Mesh(
      new THREE.SphereGeometry(Math.max(1.1, devH * 0.09), 10, 10),
      new THREE.MeshBasicMaterial({ color: ledColor })
    )
    led.position.set(
      props.side === 'front' ? -innerW / 2 + 5 : innerW / 2 - 5,
      0,
      props.side === 'front' ? devD / 2 + 1.2 : -devD / 2 - 1.2
    )
    g.add(led)

    // 告警外框：文章强调"克制表达"，用细边框而非大面积染色
    if (dev.status === 'critical' || dev.status === 'warning') {
      const ec = dev.status === 'critical' ? 0xff4d5e : 0xffb020
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(innerW + 0.8, devH + 0.8, devD + 0.8)),
        new THREE.LineBasicMaterial({ color: ec, transparent: true, opacity: 0.9 })
      )
      g.add(edge)
    }

    g.position.set(0, y, z)
    g.userData = { type: 'device', dev }
    deviceRoot.add(g)
    devMeshes.push({ mesh: g, dev, led, baseColor: ledColor, baseZ: z })
  })

  // 相机适配机柜高度
  const dist = Math.max(230, totalH * 2.1)
  controls.target.set(0, totalH * 0.52, 0)
  camera.position.set(0, totalH * 0.62, dist)
  controls.update()
}

// ---------- 视角 ----------
function frontView(instant) {
  const rack = props.rack
  const totalH = (rack?.u_height || 42) * U_H + BASE_H + TOP_H
  const dist = Math.max(230, totalH * 2.1)
  const z = props.side === 'front' ? dist : -dist
  goto(new THREE.Vector3(0, totalH * 0.62, z), new THREE.Vector3(0, totalH * 0.52, 0), instant)
}
function sideView() {
  const rack = props.rack
  const totalH = (rack?.u_height || 42) * U_H + BASE_H + TOP_H
  const dist = Math.max(260, totalH * 2.3)
  goto(new THREE.Vector3(dist, totalH * 0.7, 0), new THREE.Vector3(0, totalH * 0.52, 0))
}
function topView() {
  const rack = props.rack
  const totalH = (rack?.u_height || 42) * U_H + BASE_H + TOP_H
  goto(new THREE.Vector3(0, totalH * 2.6, 1), new THREE.Vector3(0, 0, 0))
}

function goto(pos, look, instant) {
  if (!camera) return
  if (instant) {
    camera.position.copy(pos)
    controls.target.copy(look)
    controls.update()
    return
  }
  const startPos = camera.position.clone()
  const startLook = controls.target.clone()
  const t0 = Date.now()
  const dur = 650
  function step() {
    const t = Math.min((Date.now() - t0) / dur, 1)
    const e = 1 - Math.pow(1 - t, 3)
    camera.position.lerpVectors(startPos, pos, e)
    controls.target.lerpVectors(startLook, look, e)
    controls.update()
    if (t < 1) requestAnimationFrame(step)
  }
  step()
}

// ---------- 交互 ----------
function pick(event) {
  const el = containerRef.value
  const rect = el.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
  raycaster.setFromCamera(mouse, camera)
  const hits = raycaster.intersectObjects(deviceRoot.children, true)
  if (!hits.length) return { hit: null, dev: null, x: 0, y: 0 }
  let obj = hits[0].object
  while (obj && !obj.userData?.dev && obj.parent) obj = obj.parent
  return {
    hit: obj,
    dev: obj?.userData?.dev || null,
    x: event.clientX - rect.left,
    y: event.clientY - rect.top
  }
}

function onMouseMove(e) {
  if (!camera) return
  const { dev, x, y, hit } = pick(e)
  // 悬停设备时整体轻微外移，制造"抽出设备"的视觉反馈
  devMeshes.forEach(dm => {
    const on = !!hit && dm.dev === dev
    const target = dm.baseZ + (on ? 4 : 0)
    dm.mesh.position.z += (target - dm.mesh.position.z) * 0.25
  })
  if (dev) {
    hover.visible = true
    hover.dev = dev
    hover.x = x + 16
    hover.y = y + 16
    renderer.domElement.style.cursor = 'pointer'
  } else {
    hover.visible = false
    renderer.domElement.style.cursor = 'default'
  }
}

function onClick(e) {
  if (!camera) return
  const { dev } = pick(e)
  if (dev) emit('device-click', dev)
}

function onResize() {
  const el = containerRef.value
  if (!el || !camera) return
  const w = el.clientWidth, h = el.clientHeight
  if (!w || !h) return
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}

function animate() {
  animationId = requestAnimationFrame(animate)
  if (!renderer) return
  controls.autoRotate = autoRotate.value
  controls.autoRotateSpeed = 0.6

  // 告警灯呼吸
  const t = Date.now() * 0.001
  devMeshes.forEach(dm => {
    if (dm.dev.status === 'critical') {
      const k = 0.35 + 0.65 * Math.abs(Math.sin(t * 3))
      dm.led.material.color.setHex(dm.baseColor)
      dm.led.material.color.multiplyScalar(k)
      dm.led.scale.setScalar(1 + 0.25 * Math.abs(Math.sin(t * 3)))
    } else if (dm.dev.status === 'online') {
      const k = 0.55 + 0.45 * Math.abs(Math.sin(t * 1.4))
      dm.led.material.color.setHex(dm.baseColor)
      dm.led.material.color.multiplyScalar(k)
    }
  })

  controls.update()
  renderer.render(scene, camera)
}

function disposeObject(obj) {
  obj.traverse(c => {
    if (c.geometry) c.geometry.dispose()
    if (c.material) {
      const mats = Array.isArray(c.material) ? c.material : [c.material]
      // 纹理走缓存复用，此处只释放材质本身
      mats.forEach(m => m.dispose())
    }
  })
}

function toggleFullscreen() {
  if (!document.fullscreenElement) wrapRef.value?.requestFullscreen()
  else document.exitFullscreen()
}

watch(() => [props.rack?.id, props.devices, props.side], () => {
  if (scene) { build(); frontView(true) }
}, { deep: true })

onMounted(() => {
  // 容器高度就绪后再初始化，避免拿到 0 高度
  requestAnimationFrame(() => initScene())
})

onBeforeUnmount(() => {
  if (animationId) cancelAnimationFrame(animationId)
  window.removeEventListener('resize', onResize)
  if (renderer) {
    renderer.domElement.removeEventListener('mousemove', onMouseMove)
    renderer.domElement.removeEventListener('click', onClick)
    renderer.dispose()
    if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement)
  }
  texCache.forEach(t => t.dispose())
  texCache.clear()
  scene = camera = renderer = controls = null
})
</script>

<style scoped>
.rd3-wrap {
  position: relative;
  width: 100%;
  height: 520px;
  border: 1px solid var(--border-tech, #1a2a44);
  border-radius: 8px;
  overflow: hidden;
  background: #04070f;
}
.rd3-canvas { width: 100%; height: 100%; }

.rd3-toolbar {
  position: absolute; top: 10px; right: 10px;
  display: flex; gap: 6px; z-index: 10;
}

.rd3-badge {
  position: absolute; top: 10px; left: 12px; z-index: 10;
  padding: 8px 12px;
  background: rgba(6, 13, 26, 0.82);
  border: 1px solid rgba(0, 212, 255, 0.28);
  border-radius: 6px;
  backdrop-filter: blur(8px);
}
.rd3-name { font-size: 15px; font-weight: 600; color: #00d4ff; letter-spacing: 1px; }
.rd3-meta { font-size: 11px; color: rgba(220, 232, 248, 0.65); margin-top: 2px; }
.rd3-util { font-size: 11px; color: rgba(220, 232, 248, 0.65); margin-top: 4px; display: flex; align-items: center; gap: 6px; }
.rd3-util-bar { display: inline-block; width: 80px; height: 4px; background: rgba(255, 255, 255, 0.1); border-radius: 2px; overflow: hidden; }
.rd3-util-bar i { display: block; height: 100%; background: linear-gradient(90deg, #00d4ff, #2f7bff); }

.rd3-legend {
  position: absolute; bottom: 10px; left: 12px; z-index: 10;
  display: flex; gap: 12px; padding: 6px 10px;
  background: rgba(6, 13, 26, 0.72);
  border: 1px solid rgba(47, 123, 255, 0.2);
  border-radius: 6px;
  font-size: 11px; color: rgba(220, 232, 248, 0.7);
}
.rd3-legend span { display: inline-flex; align-items: center; gap: 5px; }
.rd3-legend i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; box-shadow: 0 0 5px currentColor; }

.rd3-hover {
  position: absolute; z-index: 20; pointer-events: none;
  min-width: 190px; max-width: 280px;
  padding: 9px 12px;
  background: rgba(6, 13, 26, 0.95);
  border: 1px solid rgba(0, 212, 255, 0.4);
  border-radius: 6px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(10px);
}
.rd3-h-title { font-size: 13px; font-weight: 600; color: #fff; display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.rd3-h-dot { width: 8px; height: 8px; border-radius: 50%; box-shadow: 0 0 6px currentColor; }
.rd3-h-row { font-size: 11px; color: rgba(220, 232, 248, 0.72); line-height: 1.7; }
.rd3-h-status { margin-top: 5px; font-size: 11px; font-weight: 600; }

.rd3-empty {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  font-size: 13px; color: rgba(220, 232, 248, 0.45);
  pointer-events: none;
}

.rd3-fade-enter-active, .rd3-fade-leave-active { transition: opacity 0.15s; }
.rd3-fade-enter-from, .rd3-fade-leave-to { opacity: 0; }
</style>
