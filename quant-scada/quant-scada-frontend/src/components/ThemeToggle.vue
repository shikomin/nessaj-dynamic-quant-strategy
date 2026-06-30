<template>
  <button class="theme-toggle" :title="store.theme === 'dark' ? '切换亮色主题' : '切换深色主题'" @click="store.toggleTheme()">
    <!-- 太阳图标 (亮色模式下显示) -->
    <svg v-if="store.theme === 'dark'" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 1a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 018 1zm4.95 2.3a.75.75 0 00-1.06 0l-1.06 1.06a.75.75 0 001.06 1.06l1.06-1.06a.75.75 0 000-1.06zM14.25 8a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5a.75.75 0 01.75.75zm-2.3 4.95a.75.75 0 01-1.06 1.06l-1.06-1.06a.75.75 0 011.06-1.06l1.06 1.06zM8 12.5a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 018 12.5zm-4.95-2.3a.75.75 0 001.06-1.06l-1.06-1.06a.75.75 0 00-1.06 1.06l1.06 1.06zM2.5 8A.75.75 0 013.25 7.25h1.5a.75.75 0 010 1.5h-1.5A.75.75 0 012.5 8zm2.3-4.95a.75.75 0 01-1.06 1.06l-1.06-1.06a.75.75 0 011.06-1.06l1.06 1.06zM8 5a3 3 0 100 6 3 3 0 000-6z"/>
    </svg>
    <!-- 月亮图标 (深色模式下显示) -->
    <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M7.91 0a.75.75 0 01-.05 1.5 6 6 0 106.64 6.64.75.75 0 011.5-.05A7.5 7.5 0 007.91 0zM.75 8c0-.414.336-.75.75-.75h.5a.75.75 0 010 1.5h-.5A.75.75 0 01.75 8z"/>
    </svg>
  </button>
</template>

<script setup lang="ts">
import { useSettingsStore } from '@/stores/settings'

const store = useSettingsStore()
</script>

<style scoped>
.theme-toggle {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.theme-toggle:hover {
  background-color: var(--bg-overlay);
  color: var(--text-primary);
}
</style>
