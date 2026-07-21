const jsonHeaders = { 'Content-Type': 'application/json' }

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    ...init,
    cache: 'no-store',
    headers: { ...(init.body ? jsonHeaders : {}), ...(init.headers || {}) },
  })
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('session-expired'))
    let message = `HTTP ${response.status}`
    try {
      const body = await response.json()
      message = body.detail || body.message || message
    } catch { /* response is not JSON */ }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function download(path: string, body: unknown, fallbackName: string) {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: jsonHeaders,
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`下载失败 HTTP ${response.status}`)
  const disposition = response.headers.get('content-disposition') || ''
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] || fallbackName
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(url)
}