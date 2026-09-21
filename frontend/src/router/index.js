import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true }
  },
  {
    path: '/',
    component: () => import('@/components/Layout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '仪表盘', icon: 'Odometer' }
      },
      {
        path: 'servers',
        name: 'Servers',
        component: () => import('@/views/Servers.vue'),
        meta: { title: '服务器管理', icon: 'Monitor' }
      },
      {
        path: 'agent-install',
        name: 'AgentInstall',
        component: () => import('@/views/AgentInstall.vue'),
        meta: { title: 'Agent安装', icon: 'Download' }
      },
      {
        path: 'servers/:id',
        name: 'ServerDetail',
        component: () => import('@/views/ServerDetail.vue'),
        meta: { title: '服务器详情', hidden: true }
      },
      {
        path: 'vmware',
        name: 'Vmware',
        component: () => import('@/views/Vmware.vue'),
        meta: { title: 'VMware管理', icon: 'Cpu' }
      },
      {
        path: 'network',
        name: 'Network',
        component: () => import('@/views/Network.vue'),
        meta: { title: '网络设备', icon: 'Connection' }
      },
      {
        path: 'network/:id',
        name: 'NetworkDetail',
        component: () => import('@/views/NetworkDetail.vue'),
        meta: { title: '网络设备详情', hidden: true }
      },
      {
        path: 'storage',
        name: 'Storage',
        component: () => import('@/views/Storage.vue'),
        meta: { title: '存储设备', icon: 'Box' }
      },
      {
        path: 'racks',
        name: 'Racks',
        component: () => import('@/views/Racks.vue'),
        meta: { title: '机柜管理', icon: 'Grid' }
      },
      {
        path: 'env-devices',
        name: 'EnvDevices',
        component: () => import('@/views/EnvDevices.vue'),
        meta: { title: '动环设备', icon: 'Odometer' }
      },
      {
        path: 'alerts',
        name: 'Alerts',
        component: () => import('@/views/Alerts.vue'),
        meta: { title: '告警中心', icon: 'Bell' }
      },
      {
        path: 'alerts/config',
        name: 'AlertConfig',
        component: () => import('@/views/AlertConfig.vue'),
        meta: { title: '告警配置', icon: 'Setting' }
      },
      {
        path: 'ai',
        name: 'AiAssistant',
        component: () => import('@/views/AiAssistant.vue'),
        meta: { title: 'AI助手', icon: 'ChatDotRound' }
      }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (!to.meta.public && !token) {
    return '/login'
  }
  if (to.path === '/login' && token) {
    return '/dashboard'
  }
})

export default router
