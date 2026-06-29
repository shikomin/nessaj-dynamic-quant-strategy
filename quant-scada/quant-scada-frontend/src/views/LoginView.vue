<template>
  <div class="auth-page">
    <div class="auth-card">
      <img class="auth-logo" src="/stock-up.png" alt="logo" />
      <h1 class="auth-title">Quant SCADA</h1>
      <p class="auth-subtitle">登录到您的账户</p>
      <form @submit.prevent="handleLogin" class="auth-form">
        <div class="form-group">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="form.username"
            type="text"
            placeholder="请输入用户名"
            autocomplete="username"
          />
        </div>
        <div class="form-group">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
          />
        </div>
        <p v-if="error" class="error-msg">{{ error }}</p>
        <button type="submit" class="btn-primary" :disabled="loading">
          {{ loading ? '登录中...' : '登录' }}
        </button>
      </form>
      <p class="auth-switch">
        还没有账号？<router-link to="/register">立即注册</router-link>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const form = reactive({
  username: '',
  password: ''
})
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!form.username || !form.password) {
    error.value = '请输入用户名和密码'
    return
  }
  error.value = ''
  loading.value = true
  try {
    await authStore.login(form)
    router.push('/dashboard')
  } catch (e: any) {
    error.value = e?.response?.data?.msg || '登录失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--bg-primary);
}

.auth-card {
  width: 380px;
  padding: 40px;
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.auth-logo {
  display: block;
  width: 64px;
  height: 64px;
  margin: 0 auto 16px;
}

.auth-title {
  font-size: 24px;
  font-weight: 600;
  text-align: center;
  margin-bottom: 16px;
  color: var(--text-primary);
}

.auth-subtitle {
  margin-top: 4px;
  font-size: 13px;
  text-align: center;
  margin-bottom: 32px;
  color: var(--color-text-secondary);
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-group label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.form-group input {
  padding: 8px 12px;
  background-color: var(--input-bg);
  border: 1px solid var(--input-border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: 14px;
  transition: border-color 0.2s;
}

.form-group input:focus {
  border-color: var(--input-focus-border);
}

.form-group input::placeholder {
  color: var(--text-muted);
}

.error-msg {
  font-size: 13px;
  color: var(--accent-danger);
}

.btn-primary {
  padding: 8px 16px;
  background-color: var(--btn-primary-bg);
  color: var(--btn-primary-text);
  border-radius: var(--radius);
  font-size: 14px;
  font-weight: 500;
  transition: background-color 0.2s;
  margin-top: 8px;
}

.btn-primary:hover {
  background-color: var(--btn-primary-hover);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.auth-switch {
  text-align: center;
  margin-top: 20px;
  font-size: 13px;
  color: var(--text-secondary);
}
</style>
