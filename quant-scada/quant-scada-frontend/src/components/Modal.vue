<template>
  <Teleport to="body">
    <div
      class="modal-overlay"
      v-if="visible"
      :style="{ background: `rgba(${props.overlayColor}, ${props.overlayOpacity})` }"
      @click.self="props.closeOnOverlay ? $emit('close') : undefined"
      @keydown.esc="props.closeOnEsc ? $emit('close') : undefined"
    >
      <div
        ref="modalCard"
        class="modal-card"
        :style="cardStyle"
        :class="{ dragging: isDragging }"
      >
        <div class="modal-header" :class="{ draggable: props.draggable }" @mousedown="startDrag">
          <slot name="header">
            <h3 class="modal-title">{{ title }}</h3>
          </slot>
          <button class="modal-close" @click="$emit('close')">&times;</button>
        </div>
        <div class="modal-body" ref="modalBody">
          <slot />
        </div>
        <div class="modal-footer" v-if="$slots.footer">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = withDefaults(defineProps<{
  visible: boolean
  title?: string
  width?: string
  maxHeight?: string
  top?: string
  overlayColor?: string
  overlayOpacity?: number
  draggable?: boolean
  closeOnOverlay?: boolean
  closeOnEsc?: boolean
}>(), {
  title: '',
  width: '540px',
  maxHeight: '70vh',
  top: '80px',
  overlayColor: '0, 0, 0',
  overlayOpacity: 0.6,
  draggable: false,
  closeOnOverlay: true,
  closeOnEsc: true
})

defineEmits<{ close: [] }>()

const modalCard = ref<HTMLElement | null>(null)
const modalBody = ref<HTMLElement | null>(null)
const isDragging = ref(false)
let startX = 0
let startY = 0
let cardX = 0
let cardY = 0

const cardStyle = computed(() => ({
  width: props.width,
  maxHeight: props.maxHeight,
  marginTop: props.draggable ? '0' : undefined,
  transform: props.draggable && (cardX !== 0 || cardY !== 0)
    ? `translate(${cardX}px, ${cardY}px)` : undefined
}))

const overlayStyle = computed(() => ({
  background: `rgba(${props.overlayColor}, ${props.overlayOpacity})`,
  alignItems: props.draggable ? 'flex-start' : undefined,
  paddingTop: props.draggable ? props.top : undefined
}))

function startDrag(e: MouseEvent) {
  if (!props.draggable || !modalCard.value) return
  isDragging.value = true
  startX = e.clientX - cardX
  startY = e.clientY - cardY
  document.addEventListener('mousemove', onDrag)
  document.addEventListener('mouseup', stopDrag)
}

function onDrag(e: MouseEvent) {
  if (!isDragging.value) return
  cardX = e.clientX - startX
  cardY = e.clientY - startY
}

function stopDrag() {
  isDragging.value = false
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', stopDrag)
})
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: v-bind(top);
  z-index: 200;
}

.modal-card {
  background-color: var(--bg-secondary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  user-select: none;
}

.modal-card.dragging {
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
  transition: none;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-primary);
  cursor: default;
  flex-shrink: 0;
}

.modal-header.draggable {
  cursor: grab;
}

.modal-header.draggable:active {
  cursor: grabbing;
}

.modal-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.modal-close {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--text-secondary);
  font-size: 20px;
  border-radius: var(--radius);
  cursor: pointer;
  flex-shrink: 0;
}

.modal-close:hover {
  background-color: var(--bg-overlay);
  color: var(--text-primary);
}

.modal-body {
  padding: 16px 20px;
  overflow-y: auto;
  flex: 1;
}

.modal-footer {
  padding: 12px 20px;
  border-top: 1px solid var(--border-primary);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-shrink: 0;
}
</style>
