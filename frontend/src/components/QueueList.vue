<template>
  <div class="queue-list">
    <div class="list-header">
      <div class="list-title-wrap">
        <span class="list-title">{{ title }}</span>
        <span class="list-count">{{ tasks.length }}</span>
      </div>
      <div class="list-header-actions">
        <span class="list-hint" v-if="tasks.length > 0 && !readonly">点击箭头调整顺序</span>
        <button
          v-if="readonly && tasks.length > 0"
          class="header-action"
          @click="toggleBulkMode"
        >
          {{ bulkMode ? '退出批量' : '批量操作' }}
        </button>
      </div>
    </div>

    <!-- bulk bar for completed list -->
    <div v-if="readonly && bulkMode && tasks.length > 0" class="bulk-bar">
      <label class="bulk-check">
        <input type="checkbox" :checked="isAllSelected" @change="toggleSelectAll" />
        <span>全选</span>
      </label>
      <button
        v-if="selectedIds.length > 0"
        class="btn btn-danger"
        @click="$emit('bulkDelete', selectedIds)"
      >
        批量删除 ({{ selectedIds.length }})
      </button>
    </div>

    <div v-if="tasks.length === 0" class="empty">
      <div class="empty-icon">📋</div>
      <div class="empty-text">队列为空</div>
      <div class="empty-hint">添加一个新任务开始吧</div>
    </div>

    <div
      v-for="(task, idx) in pagedTasks"
      :key="task.id"
      class="queue-item"
      :class="{ running: task.status === 'running', terminal: isTerminal(task.status), expanded: expandedId === task.id }"
    >
      <div class="item-main">
        <div v-if="readonly && bulkMode" class="item-check">
          <input type="checkbox" :checked="selectedIds.includes(task.id)" @change="toggleSelect(task.id)" />
        </div>
        <div class="item-order">{{ globalIndex(idx) + 1 }}</div>
        <div class="item-body">
          <div class="item-title-row">
            <span class="item-title">{{ task.title }}</span>
            <span class="item-badge" :class="task.status">{{ statusLabel(task.status) }}</span>
          </div>
          <div class="item-meta">
            <span class="meta-chip">{{ modeLabel(task.mode) }}</span>
            <span class="meta-chip">重试 {{ task.retry_count }}/{{ task.max_retries }}</span>
            <button
              v-if="task.prompt"
              class="meta-chip meta-btn"
              :class="{ active: expandedId === task.id }"
              @click="toggleExpand(task.id)"
            >
              {{ expandedId === task.id ? '收起' : '查看 Prompt' }}
            </button>
          </div>
          <div v-if="task.error_message" class="item-error">{{ task.error_message }}</div>
          <div v-if="expandedId === task.id && task.prompt" class="item-prompt">
            <div class="prompt-label">Prompt</div>
            <pre class="prompt-body">{{ task.prompt }}</pre>
          </div>
          <div v-if="task.tmux_attach_command" class="item-tmux">
            <code class="tmux-code">{{ task.tmux_attach_command }}</code>
            <button class="tmux-copy" @click="copyText(task.tmux_attach_command, task.id)">
              {{ copiedId === task.id ? '已复制' : '复制' }}
            </button>
          </div>
        </div>
      </div>
      <div class="item-actions">
        <button
          v-if="!readonly"
          class="icon-btn"
          title="上移"
          @click="$emit('reorder', task.id, 'up')"
          :disabled="globalIndex(idx) === 0"
        >
          ↑
        </button>
        <button
          v-if="!readonly"
          class="icon-btn"
          title="下移"
          @click="$emit('reorder', task.id, 'down')"
          :disabled="globalIndex(idx) === tasks.length - 1"
        >
          ↓
        </button>
        <button
          v-if="task.status === 'pending'"
          class="icon-btn skip"
          title="跳过"
          @click="$emit('skip', task.id)"
        >
          ⏭
        </button>
        <button
          v-if="!isTerminal(task.status)"
          class="icon-btn done"
          title="完成"
          @click="$emit('done', task.id)"
        >
          ✓
        </button>
        <button
          v-if="canRetry(task.status)"
          class="icon-btn retry"
          title="重试"
          @click="$emit('retry', task.id)"
        >
          ↻
        </button>
        <button class="icon-btn danger" title="删除" @click="$emit('delete', task.id)">
          ✕
        </button>
      </div>
    </div>

    <!-- pagination -->
    <div v-if="totalPages > 1" class="pagination">
      <button class="page-btn" :disabled="page <= 1" @click="$emit('update:page', page - 1)"></button>
      <button
        v-for="p in visiblePages"
        :key="p"
        class="page-btn"
        :class="{ active: p === page }"
        @click="$emit('update:page', p)"
      >
        {{ p }}
      </button>
      <button class="page-btn" :disabled="page >= totalPages" @click="$emit('update:page', page + 1)">></button>
      <span class="page-info">{{ page }} / {{ totalPages }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  tasks: { type: Array, default: () => [] },
  title: { type: String, default: '任务队列' },
  readonly: { type: Boolean, default: false },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 }
})

const emit = defineEmits(['reorder', 'skip', 'done', 'delete', 'retry', 'bulkDelete', 'update:page'])

const expandedId = ref(null)
const copiedId = ref(null)
const selectedIds = ref([])
const bulkMode = ref(false)

const totalPages = computed(() => Math.max(1, Math.ceil(props.tasks.length / props.pageSize)))

const pagedTasks = computed(() => {
  const start = (props.page - 1) * props.pageSize
  return props.tasks.slice(start, start + props.pageSize)
})

function globalIndex(pageIdx) {
  return (props.page - 1) * props.pageSize + pageIdx
}

function toggleBulkMode() {
  bulkMode.value = !bulkMode.value
  if (!bulkMode.value) {
    selectedIds.value = []
  }
}

const isAllSelected = computed(() => {
  if (props.tasks.length === 0) return false
  return props.tasks.every(t => selectedIds.value.includes(t.id))
})

function toggleSelect(id) {
  const idx = selectedIds.value.indexOf(id)
  if (idx >= 0) {
    selectedIds.value.splice(idx, 1)
  } else {
    selectedIds.value.push(id)
  }
}

function toggleSelectAll() {
  if (isAllSelected.value) {
    selectedIds.value = []
  } else {
    selectedIds.value = props.tasks.map(t => t.id)
  }
}

const visiblePages = computed(() => {
  const pages = []
  const max = totalPages.value
  const cur = props.page
  const window = 2
  let start = Math.max(1, cur - window)
  let end = Math.min(max, cur + window)
  if (end - start < window * 2) {
    if (start === 1) {
      end = Math.min(max, start + window * 2)
    } else if (end === max) {
      start = Math.max(1, end - window * 2)
    }
  }
  for (let i = start; i <= end; i++) {
    pages.push(i)
  }
  return pages
})

function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? null : id
}

async function copyText(text, taskId) {
  const copied = await tryCopyText(text)
  if (!copied) return
  copiedId.value = taskId
  setTimeout(() => {
    if (copiedId.value === taskId) {
      copiedId.value = null
    }
  }, 1500)
}

async function tryCopyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch (error) {
      return fallbackCopyText(text)
    }
  }
  return fallbackCopyText(text)
}

function fallbackCopyText(text) {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'absolute'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  document.body.removeChild(textarea)
  return copied
}

function statusLabel(status) {
  const map = {
    pending: '待执行',
    running: '运行中',
    retry_wait: '等待重试',
    done: '已完成',
    skipped: '已跳过'
  }
  return map[status] || status
}

function modeLabel(mode) {
  const map = {
    one_shot: '单次',
    ulw_loop: 'ULW',
    ralph_loop: 'Ralph'
  }
  return map[mode] || mode
}

function isTerminal(status) {
  return status === 'done' || status === 'skipped'
}

function canRetry(status) {
  return status === 'done' || status === 'skipped' || status === 'retry_wait'
}
</script>

<style scoped>
.queue-list {
  background: var(--canvas-card);
  border: 1px solid var(--canvas-card-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--canvas-border);
  background: var(--canvas-bg);
}
.list-title-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.list-title {
  font-weight: 700;
  font-size: 14px;
  color: var(--canvas-text);
}
.list-count {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent-orange);
  background: var(--accent-orange-soft);
  padding: 2px 8px;
  border-radius: 10px;
  border: 1px solid #fed7aa;
}
.list-header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.list-hint {
  font-size: 11px;
  color: var(--canvas-text-muted);
}

.header-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--canvas-border);
  border-radius: 999px;
  background: var(--canvas-card);
  color: var(--canvas-text-secondary);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35);
}

.header-action:hover {
  background: var(--canvas-card);
  color: var(--canvas-text);
  border-color: var(--canvas-text-muted);
  transform: translateY(-1px);
}

.header-action:active {
  transform: translateY(0);
}

.header-action::before {
  content: '≡';
  font-size: 11px;
  opacity: 0.7;
}

.header-action:hover::before {
  opacity: 1;
}

.bulk-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid var(--canvas-border);
  background: var(--canvas-bg);
}
.bulk-check {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
  font-weight: 600;
  color: var(--canvas-text);
  cursor: pointer;
}
.bulk-check input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent-orange);
  cursor: pointer;
}
.btn-danger {
  padding: 6px 14px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  background: var(--accent-red-soft);
  color: #be123c;
  border-color: #fecdd3;
  transition: all 0.2s ease;
}
.btn-danger:hover {
  background: #fecdd3;
}

.empty {
  padding: var(--space-10) var(--space-5);
  text-align: center;
}
.empty-icon {
  font-size: 36px;
  margin-bottom: var(--space-3);
  opacity: 0.7;
}
.empty-text {
  color: var(--canvas-text-secondary);
  font-size: 14px;
  font-weight: 600;
  margin-bottom: var(--space-1);
}
.empty-hint {
  color: var(--canvas-text-muted);
  font-size: 12px;
}
.queue-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--canvas-border);
  gap: var(--space-3);
  transition: background 0.15s ease;
}
.queue-item:hover {
  background: var(--canvas-bg);
}
.queue-item:last-child {
  border-bottom: none;
}
.queue-item.running {
  background: var(--accent-green-soft);
  border-left: 3px solid #34D399;
}
.queue-item.terminal {
  opacity: 0.65;
}
.item-main {
  display: flex;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
  align-items: flex-start;
}
.item-check {
  display: flex;
  align-items: center;
  padding-top: 2px;
}
.item-check input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent-orange);
  cursor: pointer;
}
.item-order {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--canvas-bg);
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 700;
  color: var(--canvas-text-muted);
  flex-shrink: 0;
  margin-top: 1px;
  border: 1px solid var(--canvas-border);
}
.queue-item.running .item-order {
  background: linear-gradient(135deg, #34D399 0%, #10B981 100%);
  color: #fff;
  border-color: transparent;
}
.item-body {
  flex: 1;
  min-width: 0;
}
.item-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.item-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--canvas-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}
.item-badge.pending {
  background: var(--accent-yellow-soft);
  color: #B45309;
}
.item-badge.running {
  background: var(--accent-green-soft);
  color: #047857;
}
.item-badge.retry_wait {
  background: var(--accent-red-soft);
  color: #BE123C;
}
.item-badge.done {
  background: var(--accent-blue-soft);
  color: #4338CA;
}
.item-badge.skipped {
  background: var(--canvas-bg);
  color: var(--canvas-text-muted);
  border: 1px solid var(--canvas-border);
}
.item-meta {
  display: flex;
  gap: var(--space-2);
  font-size: 11px;
  color: var(--canvas-text-muted);
  flex-wrap: wrap;
}
.meta-chip {
  background: var(--canvas-bg);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
  border: 1px solid var(--canvas-border);
}
.item-error {
  margin-top: var(--space-2);
  font-size: 12px;
  color: #BE123C;
  background: var(--accent-red-soft);
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid #fecdd3;
  font-weight: 500;
}
.item-actions {
  display: flex;
  gap: 5px;
  flex-shrink: 0;
}
.icon-btn {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--canvas-border);
  background: var(--canvas-card);
  border-radius: var(--radius-sm);
  font-size: 13px;
  cursor: pointer;
  color: var(--canvas-text-secondary);
  transition: all 0.2s ease;
}
.icon-btn:hover:not(:disabled) {
  background: var(--canvas-bg);
  border-color: var(--canvas-text-muted);
  transform: translateY(-1px);
}
.icon-btn:active:not(:disabled) {
  transform: translateY(0);
}
.icon-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.icon-btn.skip {
  color: #F59E0B;
  border-color: #fde68a;
}
.icon-btn.skip:hover:not(:disabled) {
  background: var(--accent-yellow-soft);
}
.icon-btn.done {
  color: #10B981;
  border-color: #a7f3d0;
}
.icon-btn.done:hover:not(:disabled) {
  background: var(--accent-green-soft);
}
.icon-btn.danger {
  color: #F43F5E;
  border-color: #fecdd3;
}
.icon-btn.danger:hover:not(:disabled) {
  background: var(--accent-red-soft);
}
.icon-btn.retry {
  color: #3B82F6;
  border-color: #bfdbfe;
}
.icon-btn.retry:hover:not(:disabled) {
  background: var(--accent-blue-soft);
}
.meta-btn {
  cursor: pointer;
  border: 1px solid var(--canvas-border);
  background: var(--canvas-card);
  transition: all 0.2s ease;
}
.meta-btn:hover {
  border-color: var(--canvas-text-muted);
  background: var(--canvas-bg);
}
.meta-btn.active {
  background: var(--accent-orange-soft);
  border-color: #fdba74;
  color: #9A3412;
}
.item-prompt {
  margin-top: var(--space-3);
  background: var(--canvas-bg);
  border: 1px solid var(--canvas-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}
.prompt-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--canvas-text-muted);
  margin-bottom: var(--space-2);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.prompt-body {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--canvas-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-mono);
  background: transparent;
  padding: 0;
}
.item-tmux {
  margin-top: var(--space-2);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.tmux-code {
  font-size: 12px;
  color: #047857;
  background: var(--accent-green-soft);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid #a7f3d0;
  font-family: var(--font-mono);
  word-break: break-all;
}
.tmux-copy {
  font-size: 11px;
  font-weight: 600;
  color: #059669;
  background: var(--canvas-card);
  border: 1px solid #a7f3d0;
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.tmux-copy:hover {
  background: var(--accent-green-soft);
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--canvas-border);
  background: var(--canvas-bg);
}
.page-btn {
  min-width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 8px;
  border: 1px solid var(--canvas-border);
  background: var(--canvas-card);
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  color: var(--canvas-text-secondary);
  cursor: pointer;
  transition: all 0.2s ease;
}
.page-btn:hover:not(:disabled):not(.active) {
  background: var(--canvas-bg);
  border-color: var(--canvas-text-muted);
}
.page-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.page-btn.active {
  background: var(--accent-orange-soft);
  color: var(--accent-orange);
  border-color: #fdba74;
  font-weight: 700;
}
.page-info {
  font-size: 12px;
  font-weight: 600;
  color: var(--canvas-text-muted);
  margin-left: var(--space-2);
}
</style>
