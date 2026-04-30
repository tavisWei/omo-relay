<template>
  <div class="notify-view">
    <div class="section">
      <div class="section-title">通知配置</div>
      <div class="field">
        <label class="switch-label">
          <input type="checkbox" v-model="config.enabled" />
          <span>启用邮件通知</span>
        </label>
      </div>
      <div class="field-row">
        <div class="field">
          <label>SMTP 主机</label>
          <input v-model="config.smtp_host" type="text" placeholder="smtp.example.com" />
        </div>
        <div class="field">
          <label>端口</label>
          <input v-model.number="config.smtp_port" type="number" />
        </div>
      </div>
      <div class="field">
        <label>用户名</label>
        <input v-model="config.smtp_user" type="text" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="config.smtp_password" type="password" />
      </div>
      <div class="field">
        <label class="switch-label">
          <input type="checkbox" v-model="config.smtp_use_tls" />
          <span>使用 TLS</span>
        </label>
      </div>
      <div class="field">
        <label class="switch-label">
          <input type="checkbox" v-model="config.smtp_use_ssl" />
          <span>使用 SSL 直连（优先于 TLS）</span>
        </label>
      </div>
      <div class="field-row">
        <div class="field">
          <label>发件人</label>
          <input v-model="config.sender" type="email" placeholder="sender@example.com" />
        </div>
        <div class="field">
          <label>收件人</label>
          <input v-model="config.recipient" type="email" placeholder="recipient@example.com" />
        </div>
      </div>
      <div class="actions">
        <button class="btn btn-primary" @click="save">保存配置</button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">连接测试</div>
      <div class="field">
        <label>测试收件人（可选）</label>
        <input v-model="testRecipient" type="email" placeholder="默认使用配置中的收件人" />
      </div>
      <div class="actions">
        <button class="btn btn-primary" @click="test" :disabled="testing">
          {{ testing ? '测试中...' : '发送测试' }}
        </button>
      </div>
      <div v-if="testResult" class="result" :class="testResult.ok ? 'ok' : 'err'">
        {{ testResult.message }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { api } from '../api/client.js'

const config = reactive({
  enabled: false,
  smtp_host: 'localhost',
  smtp_port: 587,
  smtp_user: '',
  smtp_password: '',
  smtp_use_tls: true,
  smtp_use_ssl: false,
  sender: '',
  recipient: ''
})

const testRecipient = ref('')
const testing = ref(false)
const testResult = ref(null)

async function save() {
  try {
    const res = await api.saveNotifyConfig({ ...config })
    if (res.success) {
      alert('配置已保存')
    } else {
      alert('保存失败: ' + (res.error || '未知错误'))
    }
  } catch (e) {
    alert('保存失败: ' + e.message)
  }
}

async function test() {
  testing.value = true
  testResult.value = null
  try {
    const res = await api.testNotification(testRecipient.value || null)
    testResult.value = {
      ok: res.success,
      message: res.success ? '测试发送成功' : '测试发送失败: ' + (res.error || '未知错误')
    }
  } catch (e) {
    testResult.value = { ok: false, message: '测试发送失败: ' + e.message }
  } finally {
    testing.value = false
  }
}

async function load() {
  try {
    const res = await api.getNotifyConfig()
    if (res.success && res.data) {
      Object.assign(config, res.data)
    }
  } catch (e) {
    console.error('加载通知配置失败', e)
  }
}

load()
</script>

<style scoped>
.notify-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
.section {
  background: var(--canvas-card);
  border: 1px solid var(--canvas-card-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  box-shadow: var(--shadow-sm);
}
.section-title {
  font-weight: 700;
  font-size: 14px;
  color: var(--canvas-text);
  margin-bottom: var(--space-4);
  display: flex;
  align-items: center;
  gap: var(--space-2);
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
.field input {
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
.field input::placeholder {
  color: var(--canvas-text-muted);
}
.field input:focus {
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
.switch-label {
  display: flex !important;
  align-items: center;
  gap: var(--space-3);
  cursor: pointer;
  flex-direction: row !important;
  padding: var(--space-2) 0;
}
.switch-label input[type="checkbox"] {
  width: 18px;
  height: 18px;
  accent-color: var(--accent-orange);
  cursor: pointer;
}
.switch-label span {
  font-size: 13px;
  font-weight: 500;
  color: var(--canvas-text);
}
.actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-1);
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
.result {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
}
.result.ok {
  background: var(--accent-green-soft);
  color: #047857;
  border: 1px solid #a7f3d0;
}
.result.err {
  background: var(--accent-red-soft);
  color: #BE123C;
  border: 1px solid #fecdd3;
}
</style>
