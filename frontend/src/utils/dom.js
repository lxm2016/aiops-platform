/**
 * 轻量 DOM 工具
 * ------------------------------------------------------------------
 * hasOpenModal(): 判断当前是否有"模态层"处于可见状态。
 *
 * 用途: 后端恢复时, Layout 会强制重建当前路由组件以刷新数据。
 * 但如果用户正在"添加资产 / 编辑服务器"等对话框里填写内容,
 * 重建组件会清空未提交的表单、关闭对话框 —— 这正是用户反馈的
 * "资产还没添加完成就自动刷新退出了"。
 *
 * 因此恢复刷新前要先确认没有模态层打开, 有的话就跳过这次刷新,
 * 等用户自己关闭对话框后再说。
 *
 * 覆盖的 Element Plus 组件: el-dialog / el-drawer / ElMessageBox 确认框,
 * 它们都通过 .el-overlay 渲染, 关闭时由 v-show 置为 display:none。
 */
export function hasOpenModal() {
  try {
    const overlays = document.querySelectorAll('.el-overlay')
    for (const el of overlays) {
      // v-show 关闭时为 'none'; 打开时为空或 'flex' 等
      if (el.style.display !== 'none') return true
    }
    return false
  } catch {
    return false
  }
}
