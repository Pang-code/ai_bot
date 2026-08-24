export interface ChatResponse {
  session_id: string
  request_id: string
  status: 'completed' | 'pending_approval'
  answer?: string
  pending_approval?: {
    tool_name: string
    arguments: Record<string, unknown>
    description: string
  }
}

export interface SessionSummary {
  session_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface HistoryMessage {
  message_id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

interface SessionListResponse {
  sessions: SessionSummary[]
}

interface SessionMessagesResponse {
  session: SessionSummary
  messages: HistoryMessage[]
}

interface ErrorResponse {
  error?: {
    code?: string
    message?: string
    request_id?: string
  }
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')
const TOKEN_KEY = 'ai-agent-access-token'
const TENANT_KEY = 'ai-agent-tenant-id'

export interface Tenant {
  tenant_id: string
  name: string
  role: 'owner' | 'admin' | 'member' | 'auditor'
}

export function setAuth(token: string, tenantId = '') {
  localStorage.setItem(TOKEN_KEY, token)
  if (tenantId) localStorage.setItem(TENANT_KEY, tenantId)
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TENANT_KEY)
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function getTenantId() {
  return localStorage.getItem(TENANT_KEY) ?? ''
}

export function setTenantId(tenantId: string) {
  localStorage.setItem(TENANT_KEY, tenantId)
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  const token = getToken()
  const tenantId = getTenantId()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (tenantId) headers.set('X-Tenant-ID', tenantId)

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorResponse
    throw new Error(body.error?.message ?? `请求失败（HTTP ${response.status}）`)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export async function login(email: string, password: string): Promise<string> {
  const response = await apiRequest<{ access_token: string }>('/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  setAuth(response.access_token)
  return response.access_token
}

export async function register(
  email: string,
  password: string,
  tenantName: string,
): Promise<string> {
  const response = await apiRequest<{ access_token: string }>('/v1/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, tenant_name: tenantName }),
  })
  setAuth(response.access_token)
  return response.access_token
}

export async function listTenants(): Promise<Tenant[]> {
  return apiRequest<{ tenants: Tenant[] }>('/v1/tenants').then((r) => r.tenants)
}

export function resumeApproval(
  sessionId: string,
  decision: 'approve' | 'reject',
): Promise<ChatResponse> {
  return apiRequest<ChatResponse>(`/v1/sessions/${sessionId}/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
}

export interface AuditEvent {
  event_id: string
  action: string
  status: string
  created_at: string
  details: Record<string, unknown>
}

export function listAuditEvents(): Promise<AuditEvent[]> {
  return apiRequest<{ events: AuditEvent[] }>('/v1/audit-events').then((r) => r.events)
}

export async function sendMessage(
  message: string,
  sessionId?: string,
): Promise<ChatResponse> {
  return apiRequest<ChatResponse>('/v1/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      message,
      session_id: sessionId || undefined,
    }),
  })
}

export function listSessions(): Promise<SessionSummary[]> {
  return apiRequest<SessionListResponse>('/v1/sessions').then(
    (response) => response.sessions,
  )
}

export function getSessionMessages(
  sessionId: string,
): Promise<SessionMessagesResponse> {
  return apiRequest<SessionMessagesResponse>(
    `/v1/sessions/${sessionId}/messages`,
  )
}

export function deleteSession(sessionId: string): Promise<void> {
  return apiRequest<void>(`/v1/sessions/${sessionId}`, {
    method: 'DELETE',
  })
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/health/ready`)
    return response.ok
  } catch {
    return false
  }
}
