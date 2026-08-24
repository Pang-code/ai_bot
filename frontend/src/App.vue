<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  checkHealth,
  clearAuth,
  deleteSession,
  getToken,
  getTenantId,
  getSessionMessages,
  listAuditEvents,
  listSessions,
  listTenants,
  login,
  register,
  resumeApproval,
  sendMessage,
  setTenantId,
  type AuditEvent,
  type SessionSummary,
  type Tenant,
} from './api/chat'
import { renderMarkdown } from './utils/markdown'

type MessageRole = 'user' | 'assistant' | 'error'

interface Message {
  id: string
  role: MessageRole
  content: string
}

const SESSION_KEY = 'ai-agent-session-id'
const MESSAGES_KEY = 'ai-agent-messages'
const starterPrompts = [
  '搜索今天的 AI 行业新闻',
  '现在几点了？',
  '根据 IP 判断我的大致位置',
]

function loadMessages(): Message[] {
  try {
    const saved = localStorage.getItem(MESSAGES_KEY)
    return saved ? (JSON.parse(saved) as Message[]) : []
  } catch {
    return []
  }
}

const messages = ref<Message[]>(loadMessages())
const sessionId = ref(localStorage.getItem(SESSION_KEY) ?? '')
const input = ref('')
const isLoading = ref(false)
const isOnline = ref(false)
const copiedMessageId = ref('')
const messageList = ref<HTMLElement | null>(null)
const sessions = ref<SessionSummary[]>([])
const isHistoryLoading = ref(false)
const historyError = ref('')
const isMobileHistoryOpen = ref(false)
const authenticated = ref(Boolean(getToken()))
const authMode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const tenantName = ref('')
const authError = ref('')
const tenants = ref<Tenant[]>([])
const currentTenantId = ref(getTenantId())
const pendingApproval = ref<{ tool_name: string; description: string } | null>(null)
const auditEvents = ref<AuditEvent[]>([])
const showingAudit = ref(false)

const currentSession = computed(() =>
  sessions.value.find((session) => session.session_id === sessionId.value),
)

watch(
  messages,
  async (value) => {
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(value))
    await nextTick()
    messageList.value?.scrollTo({
      top: messageList.value.scrollHeight,
      behavior: 'smooth',
    })
  },
  { deep: true },
)

onMounted(async () => {
  isOnline.value = await checkHealth()
  if (!authenticated.value) return
  await loadTenants()
  await refreshSessions()
  if (sessionId.value && currentSession.value) {
    await openSession(currentSession.value)
  }
  await nextTick()
  messageList.value?.scrollTo({ top: messageList.value.scrollHeight })
})

async function submitAuth() {
  authError.value = ''
  try {
    if (authMode.value === 'register') {
      await register(email.value, password.value, tenantName.value)
    } else {
      await login(email.value, password.value)
    }
    authenticated.value = true
    await loadTenants()
    await refreshSessions()
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '认证失败'
  }
}

async function loadTenants() {
  tenants.value = await listTenants()
  if (!tenants.value.some((item) => item.tenant_id === currentTenantId.value)) {
    currentTenantId.value = tenants.value[0]?.tenant_id ?? ''
    if (currentTenantId.value) setTenantId(currentTenantId.value)
  }
}

async function switchTenant() {
  setTenantId(currentTenantId.value)
  startNewSession()
  await refreshSessions()
}

function logout() {
  clearAuth()
  authenticated.value = false
  tenants.value = []
  startNewSession()
}

async function decideApproval(decision: 'approve' | 'reject') {
  if (!sessionId.value) return
  isLoading.value = true
  try {
    const response = await resumeApproval(sessionId.value, decision)
    pendingApproval.value = response.pending_approval ?? null
    if (response.answer) messages.value.push(createMessage('assistant', response.answer))
  } catch (error) {
    messages.value.push(createMessage('error', error instanceof Error ? error.message : '审批失败'))
  } finally {
    isLoading.value = false
  }
}

async function openAudit() {
  showingAudit.value = true
  auditEvents.value = await listAuditEvents()
}

function createMessage(role: MessageRole, content: string): Message {
  return {
    id: crypto.randomUUID(),
    role,
    content,
  }
}

async function submitMessage(prompt?: string) {
  const content = (prompt ?? input.value).trim()
  if (!content || isLoading.value) return

  messages.value.push(createMessage('user', content))
  input.value = ''
  isLoading.value = true

  try {
    const response = await sendMessage(content, sessionId.value)
    sessionId.value = response.session_id
    localStorage.setItem(SESSION_KEY, response.session_id)
    pendingApproval.value = response.pending_approval ?? null
    if (response.answer) messages.value.push(createMessage('assistant', response.answer))
    isOnline.value = true
    await refreshSessions()
  } catch (error) {
    const message = error instanceof Error ? error.message : '请求失败，请稍后重试。'
    messages.value.push(createMessage('error', message))
    isOnline.value = false
  } finally {
    isLoading.value = false
  }
}

function startNewSession() {
  sessionId.value = ''
  messages.value = []
  input.value = ''
  localStorage.removeItem(SESSION_KEY)
  localStorage.removeItem(MESSAGES_KEY)
  isMobileHistoryOpen.value = false
}

async function refreshSessions() {
  isHistoryLoading.value = true
  historyError.value = ''
  try {
    sessions.value = await listSessions()
  } catch (error) {
    historyError.value =
      error instanceof Error ? error.message : '无法加载历史会话'
  } finally {
    isHistoryLoading.value = false
  }
}

async function openSession(session: SessionSummary) {
  if (isLoading.value) return
  historyError.value = ''
  try {
    const detail = await getSessionMessages(session.session_id)
    sessionId.value = session.session_id
    localStorage.setItem(SESSION_KEY, session.session_id)
    messages.value = detail.messages.map((message) => ({
      id: message.message_id,
      role: message.role,
      content: message.content,
    }))
    isMobileHistoryOpen.value = false
  } catch (error) {
    historyError.value =
      error instanceof Error ? error.message : '无法打开历史会话'
  }
}

async function removeSession(session: SessionSummary) {
  const confirmed = window.confirm(`确定删除会话“${session.title}”吗？`)
  if (!confirmed) return

  historyError.value = ''
  try {
    await deleteSession(session.session_id)
    if (sessionId.value === session.session_id) {
      startNewSession()
    }
    await refreshSessions()
  } catch (error) {
    historyError.value =
      error instanceof Error ? error.message : '无法删除会话'
  }
}

function formatSessionTime(value: string): string {
  const date = new Date(value)
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  }
  return date.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  })
}

async function copyMessage(message: Message) {
  await navigator.clipboard.writeText(message.content)
  copiedMessageId.value = message.id
  window.setTimeout(() => {
    copiedMessageId.value = ''
  }, 1600)
}
</script>

<template>
  <div v-if="!authenticated" class="auth-page">
    <form class="auth-card" @submit.prevent="submitAuth">
      <div class="brand-mark">A</div>
      <h1>{{ authMode === 'login' ? '登录 Agent Console' : '创建账号与租户' }}</h1>
      <input v-model="email" type="email" autocomplete="email" placeholder="邮箱" required />
      <input v-model="password" type="password" minlength="8" autocomplete="current-password" placeholder="密码（至少 8 位）" required />
      <input v-if="authMode === 'register'" v-model="tenantName" maxlength="120" placeholder="首个租户名称" required />
      <p v-if="authError" class="auth-error">{{ authError }}</p>
      <button type="submit">{{ authMode === 'login' ? '登录' : '注册' }}</button>
      <button class="link-button" type="button" @click="authMode = authMode === 'login' ? 'register' : 'login'">
        {{ authMode === 'login' ? '没有账号？注册' : '已有账号？登录' }}
      </button>
      <small>开发阶段令牌保存在 localStorage；生产环境应改用 Secure、HttpOnly Cookie。</small>
    </form>
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">A</div>
        <div>
          <strong>Agent Console</strong>
          <span>LangGraph workspace</span>
        </div>
      </div>

      <button class="new-chat-button" type="button" @click="startNewSession">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        新建会话
      </button>
      <select v-model="currentTenantId" class="tenant-select" @change="switchTenant">
        <option v-for="tenant in tenants" :key="tenant.tenant_id" :value="tenant.tenant_id">
          {{ tenant.name }} · {{ tenant.role }}
        </option>
      </select>
      <button
        v-if="tenants.find(t => t.tenant_id === currentTenantId)?.role !== 'member'"
        class="secondary-button"
        type="button"
        @click="openAudit"
      >审计日志</button>

      <button
        class="history-toggle"
        type="button"
        @click="isMobileHistoryOpen = !isMobileHistoryOpen"
      >
        历史
      </button>

      <div
        class="sidebar-section"
        :class="{ 'mobile-open': isMobileHistoryOpen }"
      >
        <div class="history-heading">
          <p class="section-label">历史会话</p>
          <button
            type="button"
            aria-label="刷新历史会话"
            :disabled="isHistoryLoading"
            @click="refreshSessions"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 6v5h-5M4 18v-5h5" />
              <path d="M6.1 9a7 7 0 0 1 11.7-2.6L20 11M4 13l2.2 4.6A7 7 0 0 0 17.9 15" />
            </svg>
          </button>
        </div>

        <p v-if="historyError" class="history-error">{{ historyError }}</p>
        <p v-else-if="isHistoryLoading" class="history-empty">正在加载…</p>
        <p v-else-if="sessions.length === 0" class="history-empty">
          暂无历史会话
        </p>

        <div v-else class="history-list">
          <div
            v-for="session in sessions"
            :key="session.session_id"
            class="history-item"
            :class="{ active: session.session_id === sessionId }"
          >
            <button
              class="history-open"
              type="button"
              @click="openSession(session)"
            >
              <strong>{{ session.title }}</strong>
              <span>创建于 {{ formatSessionTime(session.created_at) }}</span>
            </button>
            <button
              class="history-delete"
              type="button"
              :aria-label="`删除会话 ${session.title}`"
              @click="removeSession(session)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      <div class="sidebar-footer">
        <span class="status-dot" :class="{ online: isOnline }"></span>
        <span>{{ isOnline ? 'API 服务正常' : 'API 未连接' }}</span>
        <button type="button" @click="logout">退出</button>
      </div>
    </aside>

    <main class="chat-panel">
      <header class="chat-header">
        <div>
          <h1>智能体助手</h1>
          <p>
            {{ currentSession?.title ?? '联网搜索、时间查询与 IP 定位' }}
          </p>
        </div>
        <div class="model-badge">
          <span class="model-dot"></span>
          LangGraph
        </div>
      </header>

      <section ref="messageList" class="message-list" aria-live="polite">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-mark" aria-hidden="true">A</div>
          <h2>今天想了解什么？</h2>
          <p>你可以直接提问，智能体会在需要时调用外部工具。</p>
          <div class="starter-grid">
            <button
              v-for="prompt in starterPrompts"
              :key="prompt"
              type="button"
              @click="submitMessage(prompt)"
            >
              <span>{{ prompt }}</span>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m9 18 6-6-6-6" />
              </svg>
            </button>
          </div>
        </div>

        <article
          v-for="message in messages"
          :key="message.id"
          class="message-row"
          :class="message.role"
        >
          <div class="avatar" aria-hidden="true">
            {{ message.role === 'user' ? '你' : 'A' }}
          </div>
          <div class="message-content">
            <div class="message-meta">
              <strong>{{ message.role === 'user' ? '你' : '智能体' }}</strong>
              <button
                v-if="message.role !== 'user'"
                class="copy-button"
                type="button"
                @click="copyMessage(message)"
              >
                {{ copiedMessageId === message.id ? '已复制' : '复制' }}
              </button>
            </div>
            <div
              v-if="message.role === 'assistant'"
              class="markdown-body"
              v-html="renderMarkdown(message.content)"
            ></div>
            <p v-else>{{ message.content }}</p>
          </div>
        </article>

        <article v-if="isLoading" class="message-row assistant">
          <div class="avatar" aria-hidden="true">A</div>
          <div class="message-content">
            <div class="message-meta"><strong>智能体</strong></div>
            <div class="typing-indicator" aria-label="正在思考">
              <span></span><span></span><span></span>
            </div>
          </div>
        </article>
        <article v-if="pendingApproval" class="approval-card">
          <strong>需要人工批准：{{ pendingApproval.tool_name }}</strong>
          <p>{{ pendingApproval.description }}</p>
          <div>
            <button type="button" :disabled="isLoading" @click="decideApproval('approve')">批准</button>
            <button type="button" :disabled="isLoading" @click="decideApproval('reject')">拒绝</button>
          </div>
        </article>
      </section>

      <footer class="composer-area">
        <form class="composer" @submit.prevent="submitMessage()">
          <textarea
            v-model="input"
            rows="1"
            maxlength="20000"
            placeholder="输入消息，Enter 发送，Shift + Enter 换行"
            aria-label="消息内容"
            @keydown.enter.exact.prevent="submitMessage()"
          ></textarea>
          <button
            class="send-button"
            type="submit"
            :disabled="!input.trim() || isLoading"
            aria-label="发送消息"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m5 12 14-7-4 14-3-6-7-1Z" />
              <path d="m12 13 7-8" />
            </svg>
          </button>
        </form>
        <p>智能体可能会产生错误，请核对重要信息。</p>
      </footer>
    </main>
    <div v-if="showingAudit" class="audit-drawer">
      <header><h2>租户审计日志</h2><button type="button" @click="showingAudit = false">关闭</button></header>
      <div v-for="event in auditEvents" :key="event.event_id" class="audit-event">
        <strong>{{ event.action }}</strong><span>{{ event.status }}</span>
        <time>{{ new Date(event.created_at).toLocaleString('zh-CN') }}</time>
      </div>
    </div>
  </div>
</template>
