<template>
  <div class="running-card" v-if="task">
    <div class="running-header">
      <span class="badge running">
        <span class="pulse"></span>
        运行中
      </span>
      <span class="mode">{{ modeLabel(task.mode) }}</span>
    </div>
    <div class="running-title">{{ task.title }}</div>
    <div class="running-meta">
      <span class="meta-pill">ID: {{ task.id }}</span>
      <span class="meta-pill">重试 {{ task.retry_count }}/{{ task.max_retries }}</span>
    </div>
    <div class="running-actions">
      <button class="btn btn-primary" @click="$emit('done', task.id)">
        <span class="btn-icon">✓</span> 完成
      </button>
      <button class="btn btn-warn" @click="$emit('skip', task.id)">
        <span class="btn-icon">⏭</span> 跳过
      </button>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  task: { type: Object, default: null }
})
const emit = defineEmits(['done', 'skip'])

function modeLabel(mode) {
  const map = {
    one_shot: '单次执行',
    ulw_loop: 'ULW 循环',
    ralph_loop: 'Ralph 循环'
  }
  return map[mode] || mode
}
</script>

<style scoped>
.running-card {
  background: var(--canvas-card);
  border: 1px solid var(--canvas-card-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  margin-bottom: var(--space-5);
  box-shadow: var(--shadow-md);
  position: relative;
  overflow: hidden;
}
.running-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #34D399, #10B981, #059669);
}
.running-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 700;
}
.badge.running {
  background: var(--accent-green-soft);
  color: #047857;
}
.pulse {
  width: 7px;
  height: 7px;
  background: #10B981;
  border-radius: 50%;
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.85); }
}
.mode {
  font-size: 12px;
  color: var(--canvas-text-muted);
  font-weight: 500;
}
.running-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--canvas-text);
  margin-bottom: var(--space-3);
  line-height: 1.4;
}
.running-meta {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}
.meta-pill {
  font-size: 11px;
  color: var(--canvas-text-secondary);
  background: var(--canvas-bg);
  padding: 3px 10px;
  border-radius: 20px;
  font-weight: 500;
  border: 1px solid var(--canvas-border);
}
.running-actions {
  display: flex;
  gap: var(--space-3);
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 8px 16px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}
.btn:hover {
  transform: translateY(-1px);
}
.btn:active {
  transform: translateY(0);
}
.btn-icon {
  font-size: 12px;
}
.btn-primary {
  background: linear-gradient(135deg, #10B981 0%, #34D399 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.25);
}
.btn-primary:hover {
  box-shadow: 0 4px 14px rgba(16, 185, 129, 0.35);
}
.btn-warn {
  background: linear-gradient(135deg, #F59E0B 0%, #FBBF24 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.25);
}
.btn-warn:hover {
  box-shadow: 0 4px 14px rgba(245, 158, 11, 0.35);
}
</style>
