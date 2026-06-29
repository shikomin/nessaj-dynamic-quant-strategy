<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1 class="auth-title">注册</h1>
      <form @submit.prevent="handleRegister" class="auth-form">
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
            autocomplete="new-password"
          />
        </div>
        <div class="form-group">
          <label for="confirmPassword">确认密码</label>
          <input
            id="confirmPassword"
            v-model="confirmPassword"
            type="password"
            placeholder="请再次输入密码"
            autocomplete="new-password"
          />
        </div>
        <div class="form-group">
          <label for="nickname">昵称（选填）</label>
          <input
            id="nickname"
            v-model="form.nickname"
            type="text"
            placeholder="请输入昵称"
          />
        </div>
        <div class="form-group">
          <label for="email">邮箱（选填）</label>
          <input
            id="email"
            v-model="form.email"
            type="email"
            placeholder="请输入邮箱"
          />
        </div>
        <p v-if="error" class="error-msg">{{ error }}</p>
        <p v-if="success" class="success-msg">{{ success }}</p>
        <button type="submit" class="btn-primary" :disabled="loading">
          {{ loading ? '注册中...' : '注册' }}
        </button>
      </form>
      <p class="auth-switch">
        已有账号？<router-link to="/login">立即登录</router-link>
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
  password: '',
  nickname: '',
  email: ''
})
const confirmPassword = ref('')
const error = ref('')
const success = ref('')
const loading = ref(false)

async function handleRegister() {
  error.value = ''
  success.value = ''

  if (!form.username || !form.password) {
    error.value = '用户名和密码不能为空'
    return
  }
  if (form.password !== confirmPassword.value) {
    error.value = '两次密码输入不一致'
    return
  }

  loading.value = true
  try {
    await authStore.register({
      username: form.username,
      password: form.password,
      nickname: form.nickname || undefined,
      email: form.email || undefined
    })
    success.value = '注册成功，即将跳转到登录页...'
    setTimeout(() => {
      router.push('/login')
    }, 1500)
  } catch (e: any) {
    error.value = e?.response?.data?.msg || '注册失败，请重试'
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

.auth-title {
  font-size: 24px;
  font-weight: 600;
  text-align: center;
  margin-bottom: 32px;
  color: var(--text-primary);
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

.success-msg {
  font-size: 13px;
  color: var(--accent-success);
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
