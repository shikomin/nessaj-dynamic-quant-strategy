import http from './request'

export interface LoginParams {
  username: string
  password: string
}

export interface RegisterParams {
  username: string
  password: string
  email?: string
  nickname?: string
  phone?: string
}

export interface UserInfo {
  id: number
  username: string
  nickname: string
  email: string
  phone: string
  createdAt: string
}

export function loginApi(params: LoginParams) {
  return http.post('/api/auth/login', params)
}

export function registerApi(params: RegisterParams) {
  return http.post('/api/auth/register', params)
}

export function getUserInfoApi() {
  return http.get('/api/user/info')
}

export function logoutApi() {
  return http.post('/api/auth/logout')
}
