<template>
  <div class="user-menu" ref="menuRef">
    <button class="user-trigger" @click="open = !open">
      <span class="user-avatar-small">{{ initial }}</span>
      <span class="user-name">{{ displayName }}</span>
      <svg class="user-arrow" :class="{ open }" width="10" height="10" viewBox="0 0 10 10">
        <path d="M2 3l3 4 3-4" stroke="currentColor" stroke-width="1.5" fill="none"/>
      </svg>
    </button>
    <div v-if="open" class="user-dropdown">
      <router-link to="/user/center" class="dropdown-item" @click="open = false">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 8a3 3 0 100-6 3 3 0 000 6zm-5 6.5c0-2 2-4 5-4s5 2 5 4v1H3v-1z"/></svg>
        个人中心
      </router-link>
      <router-link to="/user/messages" class="dropdown-item" @click="open = false">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M0 1a1 1 0 011-1h14a1 1 0 011 1v10a1 1 0 01-1 1H5l-4 3v-3a1 1 0 01-1-1V1z"/></svg>
        消息中心
      </router-link>
      <div class="dropdown-divider"></div>
      <button class="dropdown-item dropdown-item-danger" @click="handleLogout">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M6 2a1 1 0 00-1 1v2H3a1 1 0 000 2h2v2a1 1 0 002 0V6h2a1 1 0 000-2H7V3a1 1 0 00-1-1zm-4 7a1 1 0 00-1 1v3a2 2 0 002 2h10a2 2 0 002-2v-3a1 1 0 10-2 0v3H3v-3a1 1 0 00-1-1z"/></svg>
        退出系统
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const open = ref(false)
const menuRef = ref<HTMLElement | null>(null)

const displayName = computed(() => {
  return authStore.user?.nickname || authStore.user?.username || '用户'
})

const initial = computed(() => {
  return displayName.value.charAt(0).toUpperCase()
})

async function handleLogout() {
  open.value = false
  await authStore.logout()
  router.push('/login')
}

function handleClickOutside(e: MouseEvent) {
  if (menuRef.value && !menuRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.user-menu {
  position: relative;
}

.user-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  background: transparent;
  color: var(--text-primary);
  border-radius: var(--radius);
  transition: background-color 0.15s;
}

.user-trigger:hover {
  background-color: var(--bg-overlay);
}

.user-avatar-small {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background-color: var(--accent-info);
  color: var(--text-on-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.user-name {
  font-size: 13px;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-arrow {
  color: var(--text-muted);
  transition: transform 0.15s;
}

.user-arrow.open {
  transform: rotate(180deg);
}

.user-dropdown {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 180px;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  box-shadow: var(--shadow-dropdown);
  z-index: 100;
  padding: 4px;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text-primary);
  border-radius: var(--radius);
  background: transparent;
  cursor: pointer;
  width: 100%;
  text-align: left;
  transition: background-color 0.15s;
}

.dropdown-item:hover {
  background-color: var(--bg-overlay);
  text-decoration: none;
}

.dropdown-item-danger {
  color: var(--accent-danger);
}

.dropdown-divider {
  height: 1px;
  background-color: var(--border-primary);
  margin: 4px 0;
}
</style>
