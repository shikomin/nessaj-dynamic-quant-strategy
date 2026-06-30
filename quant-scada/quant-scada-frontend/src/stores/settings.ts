// ============================================================
// 设置 Store — 主题偏好 + 持久化
// ============================================================

import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

export type Theme = 'dark' | 'light'

const THEME_KEY = 'app-theme'

/** 读取 localStorage 中的主题, 无记录默认 dark */
function loadTheme(): Theme {
  const v = localStorage.getItem(THEME_KEY)
  return v === 'light' ? 'light' : 'dark'
}

/** 应用主题到 <html> data-theme 属性 */
function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

export const useSettingsStore = defineStore('settings', () => {
  const theme = ref<Theme>(loadTheme())

  /** 切换主题并持久化 */
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem(THEME_KEY, theme.value)
    applyTheme(theme.value)
  }

  /** 初始化: 页面加载时立即应用已存储主题 */
  function initTheme() {
    applyTheme(theme.value)
  }

  return { theme, toggleTheme, initTheme }
})
