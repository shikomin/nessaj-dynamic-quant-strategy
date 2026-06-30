<template>
  <div class="title-bar" @dblclick="handleMaximize">
    <div class="title-bar-drag">
      <img class="title-logo" src="/stock-up.png" alt="" />
      <span class="title-bar-text">Quant Scada</span>
    </div>
    <div class="title-bar-actions">
      <button class="title-btn" @click="handleMinimize" title="最小化">
        <svg width="12" height="12" viewBox="0 0 12 12"><rect y="5" width="12" height="1.5" fill="currentColor"/></svg>
      </button>
      <button class="title-btn" @click="handleMaximize" title="最大化">
        <svg width="12" height="12" viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>
      </button>
      <button class="title-btn title-btn-close" @click="handleClose" title="关闭">
        <svg width="12" height="12" viewBox="0 0 12 12"><line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" stroke-width="1.5"/><line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" stroke-width="1.5"/></svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
function handleMinimize() {
  window.electronAPI?.minimize()
}

function handleMaximize() {
  window.electronAPI?.maximize()
}

function handleClose() {
  window.electronAPI?.close()
}
</script>

<style scoped>
.title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  background-color: var(--bg-secondary);
  border-bottom: 1px solid var(--border-primary);
  -webkit-app-region: drag;
  user-select: none;
  flex-shrink: 0;
}

.title-bar-drag {
  padding-left: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-logo {
  width: 16px;
  height: 16px;
}

.title-bar-text {
  font-size: 12px;
  color: var(--text-secondary);
}

.title-bar-actions {
  display: flex;
  -webkit-app-region: no-drag;
}

.title-btn {
  width: 40px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--text-secondary);
  border: none;
  cursor: pointer;
  transition: background-color 0.15s;
}

.title-btn:hover {
  background-color: var(--bg-overlay);
  color: var(--text-primary);
}

.title-btn-close:hover {
  background-color: var(--accent-danger);
  color: #fff;
}
</style>
