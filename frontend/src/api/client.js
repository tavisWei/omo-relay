const DEFAULT_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

let activeBaseUrl = DEFAULT_BASE_URL

export function setActiveBaseUrl(url) {
  activeBaseUrl = url || DEFAULT_BASE_URL
}

export function getActiveBaseUrl() {
  return activeBaseUrl
}

async function request(method, path, body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' }
  }
  if (body !== null) {
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${activeBaseUrl}${path}`, opts)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

export const api = {
  listProjects() {
    const saved = activeBaseUrl
    activeBaseUrl = DEFAULT_BASE_URL
    return request('GET', '/api/projects').finally(() => {
      activeBaseUrl = saved
    })
  },

  /** List all tasks in queue order */
  listQueue() {
    return request('GET', '/api/queue')
  },

  /** Get currently running task */
  getRunning() {
    return request('GET', '/api/queue/running')
  },

  getStatus() {
    return request('GET', '/api/status')
  },

  listSessions() {
    return request('GET', '/api/sessions')
  },

  setSession(sessionId) {
    return request('POST', '/api/sessions/select', { session_id: sessionId })
  },

  /** Add a new task */
  addTask(task) {
    return request('POST', '/api/queue', task)
  },

  /** Reorder a task */
  reorder(taskId, newOrder) {
    return request('POST', '/api/queue/reorder', { task_id: taskId, new_order: newOrder })
  },

  /** Skip a task */
  skipTask(taskId) {
    return request('POST', `/api/queue/${taskId}/skip`)
  },

  /** Mark a task as done */
  doneTask(taskId) {
    return request('POST', `/api/queue/${taskId}/done`)
  },

  retryTask(taskId) {
    return request('POST', `/api/queue/${taskId}/retry`)
  },

  /** Delete a task */
  deleteTask(taskId) {
    return request('DELETE', `/api/queue/${taskId}`)
  },

  /** Test notification */
  testNotification(recipient = null) {
    return request('POST', '/api/notify/test', { recipient })
  },

  /** Get notification config */
  getNotifyConfig() {
    return request('GET', '/api/notify/config')
  },

  /** Save notification config */
  saveNotifyConfig(config) {
    return request('POST', '/api/notify/config', config)
  },

  startProject(projectPath) {
    const saved = activeBaseUrl
    activeBaseUrl = DEFAULT_BASE_URL
    return request('POST', '/api/projects/start', { project_path: projectPath }).finally(() => {
      activeBaseUrl = saved
    })
  }
}
