<template>
  <div class="dashboard">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="brand-logo">OMO</div>
        <div class="brand-sub">任务队列管理</div>
      </div>
      <nav class="sidebar-nav">
        <button
          v-for="item in navItems"
          :key="item.key"
          class="nav-item"
          :class="{ active: currentTab === item.key }"
          @click="currentTab = item.key"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span class="nav-label">{{ item.label }}</span>
        </button>
      </nav>
      <div class="sidebar-footer">
        <div class="footer-status" :class="runtimeStatus.watcher_running ? 'online' : 'offline'">
          <span class="status-dot"></span>
          {{ runtimeStatus.watcher_running ? 'Watcher 运行中' : 'Watcher 离线' }}
        </div>
      </div>
    </aside>

    <main class="content">
      <div v-if="error" class="global-error">{{ error }}</div>

      <div v-if="currentTab === 'queue'" class="queue-view">
        <header class="content-header">
          <h1 class="content-title">任务队列管理</h1>
          <div class="queue-tabs">
            <button
              class="queue-subtab"
              :class="{ active: queueView === 'active' }"
              @click="queueView = 'active'"
            >
              任务队列
            </button>
            <button
              class="queue-subtab"
              :class="{ active: queueView === 'completed' }"
              @click="queueView = 'completed'"
            >
              已完成
            </button>
          </div>
        </header>

        <StatusPanel :status="runtimeStatus" />
        <TaskForm
          @add="handleAdd"
          :projects="projects"
          v-model:selectedProjectPath="selectedProjectPath"
          :sessions="projectSessions"
          v-model="selectedSessionId"
        />

        <template v-if="queueView === 'active'">
          <RunningTask
            :task="runningTask"
            @done="handleDone"
            @skip="handleSkip"
          />
          <QueueList
            :tasks="activeQueueTasks"
            title="任务队列"
            v-model:page="activePage"
            :pageSize="PAGE_SIZE"
            @reorder="handleReorder"
            @skip="handleSkip"
            @done="handleDone"
            @retry="handleRetry"
            @delete="handleDelete"
          />
        </template>
        <QueueList
          v-else
          :tasks="completedQueueTasks"
          title="已完成"
          :readonly="true"
          v-model:page="completedPage"
          :pageSize="PAGE_SIZE"
          @retry="handleRetry"
          @delete="handleDelete"
          @bulkDelete="handleBulkDelete"
        />
      </div>

      <div v-if="currentTab === 'notify'" class="notify-view-wrap">
        <header class="content-header">
          <h1 class="content-title">通知管理</h1>
        </header>
        <NotifyView />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { api, setActiveBaseUrl } from './api/client.js'
import TaskForm from './components/TaskForm.vue'
import RunningTask from './components/RunningTask.vue'
import QueueList from './components/QueueList.vue'
import StatusPanel from './components/StatusPanel.vue'
import NotifyView from './views/NotifyView.vue'

const navItems = [
  { key: 'queue', label: '任务队列管理', icon: '◈' },
  { key: 'notify', label: '通知管理', icon: '◉' }
]
const currentTab = ref('queue')
const queueView = ref('active')

const activeQueueTasks = ref([])
const completedQueueTasks = ref([])
const activePage = ref(1)
const completedPage = ref(1)
const PAGE_SIZE = 10
const runningTask = ref(null)
const runtimeStatus = ref({})
const projects = ref([])
const selectedProjectPath = ref('')
const projectSessions = ref([])
const selectedSessionId = ref('')
const error = ref('')
let applyingSessionSelection = false

function normalizeQueueData(data) {
  if (Array.isArray(data)) {
    return {
      active: data.filter(task => !['done', 'skipped'].includes(task.status)),
      completed: data.filter(task => ['done', 'skipped'].includes(task.status))
    }
  }
  return {
    active: data?.active || [],
    completed: data?.completed || []
  }
}

async function refresh() {
  error.value = ''
  try {
    const [listRes, runRes, statusRes, sessionsRes] = await Promise.all([
      api.listQueue(),
      api.getRunning(),
      api.getStatus(),
      api.listSessions().catch(() => ({ success: false }))
    ])
    if (listRes.success) {
      const queueData = normalizeQueueData(listRes.data)
      activeQueueTasks.value = queueData.active
      completedQueueTasks.value = queueData.completed
    } else {
      error.value = listRes.error || '获取队列失败'
    }
    if (runRes.success) {
      runningTask.value = runRes.data || null
    }
    if (statusRes.success) {
      runtimeStatus.value = statusRes.data || {}
    }
    if (sessionsRes.success) {
      const payload = sessionsRes.data || {}
      projectSessions.value = (payload.sessions || []).map(session => ({
        ...session,
        name: session.title || session.id,
        current: session.id === payload.selected_session_id
      }))
      if (!applyingSessionSelection) {
        selectedSessionId.value = payload.selected_session_id || ''
      }
    }
  } catch (e) {
    error.value = '刷新失败: ' + e.message
  }
}

async function loadProjects() {
  try {
    const res = await api.listProjects()
    if (res.success) {
      projects.value = res.data || []
      if (projects.value.length > 0 && !selectedProjectPath.value) {
        selectedProjectPath.value = projects.value[0].project_path
      }
    }
  } catch (e) {
    console.error('加载项目失败:', e)
  }
}

async function handleSessionChange(sessionId) {
  if (!sessionId || sessionId === runtimeStatus.value.selected_session_id) {
    selectedSessionId.value = sessionId
    return
  }
  error.value = ''
  applyingSessionSelection = true
  try {
    const res = await api.setSession(sessionId)
    if (res.success) {
      selectedSessionId.value = res.data?.selected_session_id || sessionId
      await refresh()
    } else {
      error.value = res.error || '切换会话失败'
      selectedSessionId.value = runtimeStatus.value.selected_session_id || ''
    }
  } catch (e) {
    error.value = '切换会话失败: ' + e.message
    selectedSessionId.value = runtimeStatus.value.selected_session_id || ''
  } finally {
    applyingSessionSelection = false
  }
}

async function handleAdd(task) {
  error.value = ''
  try {
    const res = await api.addTask(task)
    if (res.success) {
      await refresh()
    } else {
      error.value = res.error || '添加失败'
    }
  } catch (e) {
    error.value = '添加失败: ' + e.message
  }
}

async function handleReorder(taskId, direction) {
  error.value = ''
  try {
    const index = activeQueueTasks.value.findIndex(task => task.id === taskId)
    if (index === -1) return
    const offset = direction === 'up' ? -1 : 1
    const neighbor = activeQueueTasks.value[index + offset]
    if (!neighbor) return
    const newOrder = direction === 'up' ? neighbor.order + 1 : neighbor.order - 1
    const res = await api.reorder(taskId, newOrder)
    if (res.success) {
      await refresh()
    } else {
      error.value = res.error || '重排失败'
    }
  } catch (e) {
    error.value = '重排失败: ' + e.message
  }
}

async function handleSkip(taskId) {
  error.value = ''
  try {
    const res = await api.skipTask(taskId)
    if (res.success) {
      await refresh()
    } else {
      error.value = res.error || '跳过失败'
    }
  } catch (e) {
    error.value = '跳过失败: ' + e.message
  }
}

async function handleDone(taskId) {
  error.value = ''
  try {
    const res = await api.doneTask(taskId)
    if (res.success) {
      await refresh()
    } else {
      error.value = res.error || '操作失败'
    }
  } catch (e) {
    error.value = '操作失败: ' + e.message
  }
}

async function handleRetry(taskId) {
  error.value = ''
  try {
    const res = await api.retryTask(taskId)
    if (res.success) {
      await refresh()
    } else {
      error.value = res.error || '重试失败'
    }
  } catch (e) {
    error.value = '重试失败: ' + e.message
  }
}

async function handleDelete(taskId) {
  if (!confirm('确定删除该任务？')) return
  error.value = ''
  try {
    const res = await api.deleteTask(taskId)
    if (res.success) {
      await refresh()
    } else {
      error.value = res.error || '删除失败'
    }
  } catch (e) {
    error.value = '删除失败: ' + e.message
  }
}

async function handleBulkDelete(ids) {
  if (!ids || ids.length === 0) return
  if (!confirm(`确定批量删除 ${ids.length} 个任务？`)) return
  error.value = ''
  let failed = 0
  for (const id of ids) {
    try {
      const res = await api.deleteTask(id)
      if (!res.success) failed++
    } catch (e) {
      failed++
    }
  }
  await refresh()
  if (failed > 0) {
    error.value = `批量删除完成，${failed} 个任务删除失败`
  }
}

watch(selectedProjectPath, (value, previous) => {
  if (!value || value === previous) return
  const project = projects.value.find(p => p.project_path === value)
  if (project?.api_base_url) {
    setActiveBaseUrl(project.api_base_url)
    selectedSessionId.value = ''
    projectSessions.value = []
    activePage.value = 1
    completedPage.value = 1
    refresh()
  }
})

watch(selectedSessionId, (value, previous) => {
  if (!value || value === previous || applyingSessionSelection) return
  handleSessionChange(value)
})

watch(queueView, () => {
  activePage.value = 1
  completedPage.value = 1
})

onMounted(() => {
  loadProjects().then(() => {
    const project = projects.value.find(p => p.project_path === selectedProjectPath.value)
    if (project?.api_base_url) {
      setActiveBaseUrl(project.api_base_url)
    }
    refresh()
  })
  const interval = setInterval(refresh, 3000)
  return () => clearInterval(interval)
})
</script>

<style>
@import './styles/tokens.css';

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: var(--font-sans);
  background: var(--canvas-bg);
  color: var(--canvas-text);
}

.dashboard {
  display: flex;
  min-height: 100vh;
}

/* Sidebar */
.sidebar {
  width: var(--sidebar-width);
  background: var(--sidebar-bg);
  color: var(--sidebar-text);
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 50;
  border-right: 1px solid var(--sidebar-border);
}

.sidebar-brand {
  padding: var(--space-5) var(--space-5) var(--space-4);
}

.brand-logo {
  font-size: 22px;
  font-weight: 900;
  color: var(--sidebar-accent);
  letter-spacing: -0.8px;
}

.brand-sub {
  font-size: 12px;
  color: var(--sidebar-text);
  font-weight: 600;
  margin-top: var(--space-1);
  opacity: 0.8;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: 0 var(--space-3);
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--sidebar-text);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  text-align: left;
}

.nav-item:hover:not(.active) {
  background: var(--sidebar-bg-hover);
  color: var(--sidebar-text-active);
}

.nav-item.active {
  background: var(--sidebar-bg-hover);
  color: var(--sidebar-text-active);
  box-shadow: inset 2px 0 0 var(--sidebar-accent);
}

.nav-icon {
  font-size: 14px;
  width: 20px;
  text-align: center;
  opacity: 0.9;
}

.sidebar-footer {
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--sidebar-border);
}

.footer-status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 12px;
  font-weight: 600;
}

.footer-status .status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-red);
}

.footer-status.online .status-dot {
  background: var(--accent-green);
}

/* Content */
.content {
  flex: 1;
  margin-left: var(--sidebar-width);
  padding: var(--space-6) var(--space-8);
  max-width: calc(100vw - var(--sidebar-width));
}

.content-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-5);
  gap: var(--space-4);
  flex-wrap: wrap;
}

.content-title {
  font-size: 22px;
  font-weight: 800;
  color: var(--canvas-text);
  margin: 0;
  letter-spacing: -0.3px;
}

.queue-tabs {
  display: inline-flex;
  gap: var(--space-1);
  background: var(--canvas-card);
  padding: var(--space-1);
  border-radius: var(--radius-lg);
  border: 1px solid var(--canvas-border);
}

.queue-subtab {
  padding: 7px 16px;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  font-size: 13px;
  cursor: pointer;
  color: var(--canvas-text-secondary);
  font-weight: 600;
  transition: all 0.2s ease;
}

.queue-subtab:hover:not(.active) {
  color: var(--accent-orange);
  background: var(--accent-orange-soft);
}

.queue-subtab.active {
  background: var(--accent-orange-soft);
  color: var(--accent-orange);
  box-shadow: 0 1px 3px rgba(249, 115, 22, 0.12);
}

.global-error {
  background: var(--accent-red-soft);
  color: #be123c;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  font-size: 13px;
  margin-bottom: var(--space-4);
  border: 1px solid #fecdd3;
  font-weight: 500;
}

/* Responsive */
@media (max-width: 768px) {
  .dashboard {
    flex-direction: column;
  }

  .sidebar {
    width: 100%;
    position: relative;
    flex-direction: row;
    align-items: center;
    padding: var(--space-3) var(--space-4);
    gap: var(--space-4);
  }

  .sidebar-brand {
    padding: 0;
  }

  .sidebar-nav {
    flex-direction: row;
    padding: 0;
    flex: 1;
    justify-content: flex-end;
  }

  .nav-item {
    padding: var(--space-2) var(--space-3);
  }

  .nav-label {
    display: none;
  }

  .sidebar-footer {
    display: none;
  }

  .content {
    margin-left: 0;
    padding: var(--space-4);
    max-width: 100%;
  }
}
</style>
