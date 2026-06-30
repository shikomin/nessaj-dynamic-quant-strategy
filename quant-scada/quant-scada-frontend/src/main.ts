import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './assets/styles/global.css'
import { useSettingsStore } from './stores/settings'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.mount('#app')

// 页面挂载后立即应用主题, 避免 FOUC (flash of unstyled content)
const settings = useSettingsStore()
settings.initTheme()
