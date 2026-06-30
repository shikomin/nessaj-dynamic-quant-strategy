/**
 * Axios HTTP 客户端
 * - 自动附加 JWT Token
 * - 401/403 时清除登录态并跳转登录页
 * - 响应拦截器自动解包 res.data
 */
import axios from 'axios'

const http = axios.create({
  baseURL: '/proxy-api',
  timeout: 10000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    if (err.response?.status === 401 || err.response?.status === 403) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      setTimeout(() => {
        window.location.href = '/#/login'
      }, 50)
    }
    return Promise.reject(err)
  }
)

export default http
