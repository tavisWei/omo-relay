<template>
  <section class="status-panel">
    <div class="status-grid">
      <div class="status-card peach">
        <div class="label">待执行</div>
        <div class="value">{{ counts.pending || 0 }}</div>
      </div>
      <div class="status-card mint">
        <div class="label">运行中</div>
        <div class="value">{{ counts.running || 0 }}</div>
      </div>
      <div class="status-card amber">
        <div class="label">等待重试</div>
        <div class="value">{{ counts.retry_wait || 0 }}</div>
      </div>
      <div class="status-card sky">
        <div class="label">已完成</div>
        <div class="value">{{ counts.done || 0 }}</div>
      </div>
      <div class="status-card slate">
        <div class="label">已跳过</div>
        <div class="value">{{ counts.skipped || 0 }}</div>
      </div>
      <div class="status-card lilac">
        <div class="label">等待原因</div>
        <div class="value status-text">{{ reasonLabel }}</div>
      </div>
    </div>

    <div class="status-summary">
      <div class="summary-head">
        <div class="summary-primary">
          <span class="hero-badge" :class="heroClass">{{ heroBadge }}</span>
          <span class="summary-title">{{ decisionLabel }}</span>
        </div>
        <div class="summary-badge">
          <span class="watcher-flag" :class="status.watcher_running ? 'online' : 'offline'">
            {{ status.watcher_running ? 'Watcher 监控中' : 'Watcher 未运行' }}
          </span>
        </div>
      </div>

      <div v-if="status.project_path || status.tmux_session_name" class="summary-context">
        <span v-if="status.project_path">{{ status.project_path }}</span>
        <span v-if="status.tmux_session_name">
          tmux 会话 {{ status.tmux_session_name }} · pane {{ status.tmux_pane_id }}
        </span>
      </div>

      <button v-if="hasDetailGroups" class="summary-toggle" :class="{ 'is-open': showDetails }" @click="showDetails = !showDetails">
        <span>详细信息</span>
        <span>{{ showDetails ? '收起' : '展开' }}</span>
      </button>

      <div v-show="showDetails" class="summary-groups">
        <div v-if="hasDecisionGroup" class="summary-group">
          <div class="group-label">决策</div>
          <div class="group-chips">
            <span v-if="status.watcher_decision">{{ status.watcher_decision }}</span>
            <span v-if="status.watcher_reason">{{ status.watcher_reason }}</span>
            <span v-if="status.latest_message_role">{{ status.latest_message_role }}</span>
          </div>
        </div>

        <div v-if="hasTaskGroup" class="summary-group">
          <div class="group-label">任务</div>
          <div class="group-chips">
            <span v-if="status.active_continuation_task_id">当前续写 {{ status.active_continuation_task_id }}</span>
            <span v-if="status.watcher_last_launch_task_id">上次续写 {{ status.watcher_last_launch_task_id }}</span>
            <span v-if="status.pending_task_id">下一待执行 {{ status.pending_task_id }}</span>
          </div>
        </div>

        <div v-if="hasTimeGroup" class="summary-group">
          <div class="group-label">时间</div>
          <div class="group-chips">
            <span v-if="status.latest_activity_at">最近活动 {{ status.latest_activity_at }}</span>
            <span v-if="status.latest_message_completed_at">完成时间 {{ status.latest_message_completed_at }}</span>
            <span v-if="status.watcher_last_checked_at">最近检查 {{ status.watcher_last_checked_at }}</span>
          </div>
        </div>
      </div>

      <div v-if="status.watcher_last_error" class="summary-error">
        {{ status.watcher_last_error }}
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  status: {
    type: Object,
    default: () => ({})
  }
})

const counts = computed(() => props.status.counts || {})
const showDetails = ref(false)

const decisionLabel = computed(() => {
  if (props.status.ready_for_continuation) return '已满足续写条件'
  if (props.status.soft_stalled) return '会话安静过久，准备提前续写'
  if (props.status.stalled) return '会话长时间无更新，等待兜底续写'
  if (props.status.watcher_running) return '等待当前会话稳定结束'
  return '等待 Watcher 启动'
})

const heroBadge = computed(() => {
  if (props.status.ready_for_continuation) return 'READY'
  if (props.status.soft_stalled) return 'SOFT-STALLED'
  if (props.status.stalled) return 'STALLED'
  if (props.status.watcher_running) return 'WATCHING'
  return 'OFFLINE'
})

const heroClass = computed(() => {
  if (props.status.ready_for_continuation) return 'hero-ready'
  if (props.status.soft_stalled) return 'hero-soft-stalled'
  if (props.status.stalled) return 'hero-stalled'
  if (props.status.watcher_running) return 'hero-watching'
  return 'hero-offline'
})

const reasonLabel = computed(() => {
  const map = {
    snapshot_loaded: '已读取会话快照',
    not_ready: '当前会话尚未稳定结束',
    ready: '当前会话可续写下一任务',
    soft_stalled: 'assistant 长时间安静但未显式 completed，准备提前续写',
    stalled: '当前会话长时间无更新',
    no_pending_task: '当前没有待续写任务',
    running_task_present: '已有任务处于续写处理中',
    continuation_triggered: '正在发送下一条任务',
    continuation_sent: '已送入同一会话',
    continuation_failed: '续写发送失败',
    awaiting_session_completion: '已发送，等待当前轮完成',
    message_advanced: '检测到同会话消息前进',
    task_missing_or_not_running: '运行态已清空'
  }
  return map[props.status.watcher_reason] || props.status.watcher_reason || '暂无'
})

const hasDecisionGroup = computed(() =>
  !!props.status.watcher_decision || !!props.status.watcher_reason || !!props.status.latest_message_role
)

const hasTaskGroup = computed(() =>
  !!props.status.active_continuation_task_id || !!props.status.watcher_last_launch_task_id || !!props.status.pending_task_id
)

const hasTimeGroup = computed(() =>
  !!props.status.latest_activity_at || !!props.status.latest_message_completed_at || !!props.status.watcher_last_checked_at
)

const hasDetailGroups = computed(() =>
  hasDecisionGroup.value || hasTaskGroup.value || hasTimeGroup.value
)
</script>

<style scoped>
.status-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--space-3);
}

.status-card {
  border-radius: var(--radius-lg);
  padding: var(--space-3);
  color: var(--canvas-text);
  border: 1px solid var(--canvas-card-border);
  box-shadow: var(--shadow-sm);
  background: var(--canvas-card);
}

.status-card .label {
  font-size: 11px;
  font-weight: 700;
  color: var(--canvas-text-muted);
}

.status-card .value {
  margin-top: var(--space-1);
  font-size: 22px;
  line-height: 1;
  font-weight: 800;
}

.status-card .value.status-text {
  font-size: 12px;
  line-height: 1.3;
  font-weight: 600;
}

.peach { background: var(--accent-orange-soft); }
.mint { background: var(--accent-green-soft); }
.amber { background: var(--accent-yellow-soft); }
.sky { background: var(--accent-blue-soft); }
.slate { background: var(--accent-slate-soft); }
.lilac { background: var(--accent-purple-soft); }

.status-summary {
  background: var(--canvas-card);
  border: 1px solid var(--canvas-card-border);
  border-radius: var(--radius-xl);
  padding: var(--space-4);
  box-shadow: var(--shadow-sm);
}

.summary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.summary-primary {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  min-width: 0;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.08em;
  background: var(--accent-slate-soft);
  color: var(--canvas-text-secondary);
  border: 1px solid var(--canvas-border);
}

.hero-ready {
  background: var(--accent-green-soft);
  color: #166534;
  border-color: #86efac;
}

.hero-soft-stalled {
  background: var(--accent-yellow-soft);
  color: #92400e;
  border-color: #fdba74;
}

.hero-stalled {
  background: var(--accent-red-soft);
  color: #991b1b;
  border-color: #fca5a5;
}

.hero-watching {
  background: var(--accent-blue-soft);
  color: #1d4ed8;
  border-color: #93c5fd;
}

.hero-offline {
  background: var(--accent-slate-soft);
  color: var(--canvas-text-secondary);
  border-color: #cbd5e1;
}

.summary-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--canvas-text);
}

.summary-badge {
  display: flex;
  flex-shrink: 0;
}

.watcher-flag {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.watcher-flag.online {
  background: var(--accent-green-soft);
  color: #166534;
}

.watcher-flag.offline {
  background: var(--accent-red-soft);
  color: #991b1b;
}

.summary-context {
  margin-top: var(--space-2);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: 12px;
  color: var(--canvas-text-muted);
}

.summary-context span {
  word-break: break-all;
}

.summary-groups {
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.summary-toggle {
  margin-top: var(--space-3);
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--canvas-border);
  background: var(--canvas-bg);
  color: var(--canvas-text-secondary);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
}

.summary-toggle:hover {
  border-color: var(--canvas-text-muted);
  color: var(--canvas-text);
}

.summary-toggle.is-open {
  border-color: var(--canvas-text-muted);
}

.summary-group {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.group-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--canvas-text-muted);
  white-space: nowrap;
  padding-top: 3px;
  width: 32px;
  flex-shrink: 0;
}

.group-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  flex: 1;
  min-width: 0;
}

.group-chips span {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  color: var(--canvas-text-secondary);
  background: var(--canvas-bg);
  border: 1px solid var(--canvas-border);
}

.summary-error {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--accent-red-soft);
  color: #be123c;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid #fecdd3;
}

@media (max-width: 720px) {
  .status-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .summary-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .summary-group {
    flex-direction: column;
    gap: var(--space-1);
  }

  .group-label {
    padding-top: 0;
  }
}
</style>
