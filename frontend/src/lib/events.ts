import { useEffect, useState } from 'react'
import { api } from './api'

export type LogRow = { id: number; level: string; message: string; created_at: string }
type LogState = { streamKey?: string; rows: LogRow[] }

export function useEventLog(streamId?: string, resetKey = 0) {
  const [state, setState] = useState<LogState>({ rows: [] })
  const streamKey = streamId ? `${streamId}:${resetKey}` : undefined
  const rows = state.streamKey === streamKey ? state.rows : []

  useEffect(() => {
    if (!streamId) return

    let cursor = 0
    let source: EventSource | undefined
    let pollTimer: number | undefined
    let stopped = false

    const append = (incoming: LogRow[]) => {
      const fresh = incoming.filter((row) => row.id > cursor)
      if (!fresh.length) return
      cursor = Math.max(cursor, ...fresh.map((row) => row.id))
      setState((current) => {
        const currentRows = current.streamKey === streamKey ? current.rows : []
        return { streamKey, rows: [...currentRows, ...fresh].slice(-500) }
      })
    }

    const poll = async () => {
      if (stopped) return
      try {
        append(await api<LogRow[]>(`/api/logs/${encodeURIComponent(streamId)}?after=${cursor}`))
      } finally {
        if (!stopped) pollTimer = window.setTimeout(poll, 1500)
      }
    }

    source = new EventSource(`/api/events/${encodeURIComponent(streamId)}?after=${cursor}`)
    source.addEventListener('log', (event) => {
      try {
        append([JSON.parse((event as MessageEvent).data) as LogRow])
      } catch {
        // 忽略不完整事件，轮询降级会补回持久化日志。
      }
    })
    source.onerror = () => {
      source?.close()
      source = undefined
      if (!stopped && pollTimer === undefined) void poll()
    }

    return () => {
      stopped = true
      source?.close()
      if (pollTimer !== undefined) window.clearTimeout(pollTimer)
    }
  }, [resetKey, streamId, streamKey])

  return rows
}