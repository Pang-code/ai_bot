export interface ChatResponse {
  session_id: string
  request_id: string
  answer: string
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
const CLIENT_ID_KEY = 'ai-agent-client-id'

export function getClientId(): string {
  const existing = localStorage.getItem(CLIENT_ID_KEY)
  if (existing) return existing

  const clientId = crypto.randomUUID()
  localStorage.setItem(CLIENT_ID_KEY, clientId)
  return clientId
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('X-Client-ID', getClientId())

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
