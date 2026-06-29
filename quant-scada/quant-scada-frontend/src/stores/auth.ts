import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { loginApi, registerApi, getUserInfoApi, logoutApi } from '@/api/auth'
import type { LoginParams, RegisterParams, UserInfo } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('token') || '')
  const user = ref<UserInfo | null>(null)

  const isLoggedIn = computed(() => !!token.value)

  function setAuth(t: string, u: UserInfo) {
    token.value = t
    user.value = u
    localStorage.setItem('token', t)
    localStorage.setItem('user', JSON.stringify(u))
  }

  function clearAuth() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  async function login(params: LoginParams) {
    const res: any = await loginApi(params)
    setAuth(res.data.token, res.data.user)
    return res.data
  }

  async function register(params: RegisterParams) {
    return await registerApi(params)
  }

  async function fetchUserInfo() {
    const res: any = await getUserInfoApi()
    user.value = res.data
    return res.data
  }

  async function logout() {
    try {
      await logoutApi()
    } catch {
      // ignore
    }
    clearAuth()
  }

  const savedUser = localStorage.getItem('user')
  if (savedUser) {
    try {
      user.value = JSON.parse(savedUser)
    } catch {
      clearAuth()
    }
  }

  return {
    token,
    user,
    isLoggedIn,
    login,
    register,
    logout,
    fetchUserInfo,
    clearAuth
  }
})
