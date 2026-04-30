<template>
  <div class="task-form">
    <div class="form-header">新建任务</div>
    <div class="form-body">
      <div class="field">
        <label>Prompt / 指令</label>
        <textarea
          v-model="form.prompt"
          rows="4"
          placeholder="输入任务指令内容"
        ></textarea>
      </div>
      <div class="field-row">
        <div class="field">
          <label>执行模式</label>
          <select v-model="form.mode">
            <option value="one_shot">单次执行</option>
            <option value="ulw_loop">ULW 循环</option>
            <option value="ralph_loop">Ralph 循环</option>
          </select>
        </div>
        <div class="field">
          <label>最大重试</label>
          <input v-model.number="form.max_retries" type="number" min="0" max="10" />
        </div>
      </div>
      <div class="form-actions-bar">
        <div class="form-actions">
          <button class="btn btn-primary" @click="submit" :disabled="!canSubmit">添加任务</button>
          <button class="btn btn-ghost" @click="reset">重置</button>
        </div>
        <div class="selectors">
          <div v-if="projects.length > 0" class="selector-group">
            <label class="selector-label">项目</label>
            <select
              class="selector-select"
              :value="selectedProjectPath"
              @change="$emit('update:selectedProjectPath', $event.target.value)"
            >
              <option
                v-for="p in projects"
                :key="p.project_path"
                :value="p.project_path"
              >
                {{ p.project_name || p.project_path }}
              </option>
            </select>
          </div>
          <div v-if="sessions.length > 0" class="selector-group">
            <label class="selector-label">会话</label>
            <select
              class="selector-select"
              :value="modelValue"
              @change="$emit('update:modelValue', $event.target.value)"
            >
              <option
                v-for="s in sessions"
                :key="s.id"
                :value="s.id"
              >
                {{ s.name || s.id }}
              </option>
            </select>
            <span class="selector-hint">{{ selectedSessionLabel }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, computed } from 'vue'

const props = defineProps({
  projects: { type: Array, default: () => [] },
  selectedProjectPath: { type: String, default: '' },
  sessions: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' }
})

const emit = defineEmits(['add', 'update:selectedProjectPath', 'update:modelValue'])

const form = reactive({
  prompt: '',
  mode: 'one_shot',
  max_retries: 3
})

const canSubmit = computed(() => form.prompt.trim().length > 0)

const selectedSessionLabel = computed(() => {
  const selected = props.sessions.find(s => s.id === props.modelValue)
  if (!selected) return ''
  return selected.current ? '主会话' : '已选会话'
})

function submit() {
  if (!canSubmit.value) return
  emit('add', {
    prompt: form.prompt.trim(),
    mode: form.mode,
    max_retries: form.max_retries
  })
  reset()
}

function reset() {
  form.prompt = ''
  form.mode = 'one_shot'
  form.max_retries = 3
}
</script>

<style scoped>
.task-form {
  background: var(--canvas-card);
  border: 1px solid var(--canvas-card-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-bottom: var(--space-5);
  box-shadow: var(--shadow-sm);
}
.form-header {
  padding: var(--space-4) var(--space-5);
  background: var(--canvas-bg);
  border-bottom: 1px solid var(--canvas-border);
  font-weight: 700;
  font-size: 14px;
  color: var(--canvas-text);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.form-header::before {
  content: "✨";
  font-size: 15px;
}
.form-body {
  padding: var(--space-5);
}
.field {
  margin-bottom: var(--space-4);
}
.field label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--canvas-text-secondary);
  margin-bottom: var(--space-2);
}
.field input,
.field textarea,
.field select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--canvas-border);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--canvas-text);
  background: var(--canvas-bg);
  box-sizing: border-box;
  font-family: inherit;
  transition: all 0.2s ease;
}
.field input::placeholder,
.field textarea::placeholder {
  color: var(--canvas-text-muted);
}
.field input:focus,
.field textarea:focus,
.field select:focus {
  outline: none;
  border-color: var(--accent-orange);
  background: var(--canvas-card);
  box-shadow: 0 0 0 3px rgba(249, 115, 22, 0.12);
}
.field-row {
  display: flex;
  gap: var(--space-4);
}
.field-row .field {
  flex: 1;
}

.form-actions-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
  margin-top: var(--space-1);
}

.form-actions {
  display: flex;
  gap: var(--space-3);
  flex-shrink: 0;
}

.selectors {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.selector-group {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.selector-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--canvas-text-muted);
  white-space: nowrap;
}

.selector-select {
  padding: 7px 10px;
  border: 1px solid var(--canvas-border);
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--canvas-text);
  background: var(--canvas-bg);
  font-family: inherit;
  transition: all 0.2s ease;
  cursor: pointer;
  min-width: 120px;
  max-width: 220px;
}

.selector-select:focus {
  outline: none;
  border-color: var(--accent-orange);
  background: var(--canvas-card);
  box-shadow: 0 0 0 3px rgba(249, 115, 22, 0.12);
}

.selector-hint {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-orange);
  white-space: nowrap;
}

.btn {
  padding: 9px 18px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}
.btn:hover:not(:disabled) {
  transform: translateY(-1px);
}
.btn:active:not(:disabled) {
  transform: translateY(0);
}
.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.btn-primary {
  background: linear-gradient(135deg, #F97316 0%, #FB923C 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(249, 115, 22, 0.25);
}
.btn-primary:hover:not(:disabled) {
  box-shadow: 0 4px 14px rgba(249, 115, 22, 0.35);
}
.btn-ghost {
  background: var(--canvas-card);
  color: var(--canvas-text-secondary);
  border-color: var(--canvas-border);
}
.btn-ghost:hover:not(:disabled) {
  background: var(--canvas-bg);
  border-color: var(--canvas-text-muted);
  color: var(--canvas-text);
}

@media (max-width: 720px) {
  .form-actions-bar {
    flex-direction: column;
    align-items: flex-start;
  }

  .selectors {
    width: 100%;
  }

  .selector-group {
    flex: 1;
    min-width: 0;
  }

  .selector-select {
    flex: 1;
    max-width: none;
    min-width: 0;
  }
}
</style>
