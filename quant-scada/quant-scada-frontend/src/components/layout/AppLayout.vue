<template>
  <div class="app-layout">
    <NavBar />
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<script setup lang="ts">
import NavBar from './NavBar.vue'
</script>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background-color: var(--bg-primary);
}

.main-content {
  flex: 1;
  overflow-y: auto;
}
</style>
