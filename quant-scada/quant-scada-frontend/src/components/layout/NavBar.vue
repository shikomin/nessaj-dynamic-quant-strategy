<template>
  <nav class="nav-bar">
    <div class="nav-left">
      <span class="nav-brand">Quant Scada</span>
      <router-link
        v-for="item in menuItems"
        :key="item.path"
        :to="item.path"
        class="nav-link"
        :class="{ active: isActive(item.path) }"
      >
        {{ item.label }}
      </router-link>
    </div>
    <div class="nav-right">
      <UserMenu />
    </div>
  </nav>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import UserMenu from './UserMenu.vue'

const route = useRoute()

const menuItems = [
  { path: '/dashboard', label: '看板' },
  { path: '/watchlist', label: '自选股' },
  { path: '/market', label: '行情' }
]

function isActive(path: string) {
  return route.path === path
}
</script>

<style scoped>
.nav-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 16px;
  background-color: var(--bg-secondary);
  border-bottom: 1px solid var(--border-primary);
  flex-shrink: 0;
}

.nav-left {
  display: flex;
  align-items: center;
  gap: 4px;
}

.nav-brand {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-right: 16px;
  white-space: nowrap;
}

.nav-link {
  padding: 6px 12px;
  font-size: 13px;
  color: var(--text-secondary);
  border-radius: var(--radius);
  transition: all 0.15s;
  white-space: nowrap;
}

.nav-link:hover {
  color: var(--text-primary);
  background-color: var(--bg-overlay);
  text-decoration: none;
}

.nav-link.active {
  color: var(--text-primary);
  background-color: var(--bg-overlay);
}

.nav-right {
  display: flex;
  align-items: center;
}
</style>
