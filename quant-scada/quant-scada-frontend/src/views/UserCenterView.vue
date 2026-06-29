<template>
  <div class="page">
    <h1 class="page-title">个人中心</h1>
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="user" class="user-card">
      <div class="user-avatar">
        {{ (user.nickname || user.username).charAt(0).toUpperCase() }}
      </div>
      <div class="user-info-grid">
        <div class="info-item">
          <span class="info-label">用户名</span>
          <span class="info-value">{{ user.username }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">昵称</span>
          <span class="info-value">{{ user.nickname || '-' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">邮箱</span>
          <span class="info-value">{{ user.email || '-' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">手机号</span>
          <span class="info-value">{{ user.phone || '-' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">注册时间</span>
          <span class="info-value">{{ formatDate(user.createdAt) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import type { UserInfo } from '@/api/auth'

const authStore = useAuthStore()
const user = ref<UserInfo | null>(null)
const loading = ref(true)

function formatDate(dateStr: string) {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  return d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  try {
    user.value = await authStore.fetchUserInfo()
  } catch {
    user.value = authStore.user
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page {
  padding: 24px;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 24px;
}

.loading {
  color: var(--text-secondary);
  font-size: 14px;
}

.user-card {
  display: flex;
  gap: 24px;
  padding: 24px;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
}

.user-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background-color: var(--accent-info);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-weight: 600;
  flex-shrink: 0;
}

.user-info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px 32px;
  flex: 1;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 12px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.info-value {
  font-size: 14px;
  color: var(--text-primary);
}
</style>
