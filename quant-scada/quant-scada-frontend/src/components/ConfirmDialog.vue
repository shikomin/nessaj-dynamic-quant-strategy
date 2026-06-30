<template>
  <Modal
    :visible="visible"
    :title="title"
    width="400px"
    max-height="auto"
    top="45vh"
    :overlay-opacity="0.5"
    :close-on-overlay="closeOnOverlay"
    @close="$emit('cancel')"
  >
    <p class="confirm-message">{{ message }}</p>
    <template #footer>
      <button class="btn-cancel" @click="$emit('cancel')">{{ cancelText }}</button>
      <button class="btn-danger" @click="$emit('confirm')">{{ confirmText }}</button>
    </template>
  </Modal>
</template>

<script setup lang="ts">
import Modal from './Modal.vue'

withDefaults(defineProps<{
  visible: boolean
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
  closeOnOverlay?: boolean
}>(), {
  title: '确认',
  message: '确定要执行此操作吗？',
  confirmText: '确认',
  cancelText: '取消',
  closeOnOverlay: true
})

defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<style scoped>
.confirm-message {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.6;
}

.btn-cancel {
  padding: 6px 16px;
  font-size: 13px;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
}

.btn-cancel:hover {
  background-color: var(--bg-overlay);
}

.btn-danger {
  padding: 6px 16px;
  font-size: 13px;
  background-color: var(--btn-danger-bg);
  color: var(--text-on-accent);
  border-radius: var(--radius);
  cursor: pointer;
}

.btn-danger:hover {
  background-color: var(--btn-danger-hover);
}
</style>
