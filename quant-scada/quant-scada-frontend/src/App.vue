<template>
  <TitleBar v-if="isElectron" />
  <AppLayout v-if="!isAuthPage">
    <router-view />
  </AppLayout>
  <router-view v-else />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import TitleBar from '@/components/layout/TitleBar.vue'

const route = useRoute()
const isAuthPage = computed(() => ['Login', 'Register'].includes(route.name as string))
const isElectron = computed(() => !!window.electronAPI)
</script>
