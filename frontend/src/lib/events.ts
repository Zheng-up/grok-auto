import { useEffect, useState } from 'react'
import { api } from './api'

export type LogRow = { id: number; level: string; message: string; created_at: string; stream_id?: string }
type LogState = { streamKey?: string; rows: LogRow[] }

export function useEventLog(streamId?: string, resetKey = 0) {
  const [state, setState] = useState<LogState>({ rows: [] })
  const streamKey = streamId ? `${streamId}:${resetKey}` : undefined
  const rows = state.streamKey === streamKey ? state.rows : []

  useEffect(() => {
    if (!streamId || !streamKey) return

    let cursor = 0
    let source: EventSource | undefined
    let pollTimer: number | undefined
    let stopped = false
    const seen = new Set<number>()

    // Reset viewer immediately when remounting / switching stream.
    setState({ streamKey, rows: [] })

    const append = (incoming: LogRow[]) => {
      if (!incoming.length) return
      const fresh: LogRow[] = []
      for (const row of incoming) {
        const id = Number(row.id)
        if (!Number.isFinite(id) || id <= 0 || seen.has(id) || id <= cursor) continue
        seen.add(id)
        fresh.push(row)
      }
      if (!fresh.length) return
      cursor = Math.max(cursor, ...fresh.map((row) => Number(row.id)))
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

    // Bootstrap once with latest tail (backend after=0 returns latest N), then stream only newer rows.
    void (async () => {
      if (stopped) return
      try {
        append(await api<LogRow[]>(`/api/logs/${encodeURIComponent(streamId)}?after=0`))
      } catch {
        // fall through to SSE/poll
      }
      if (stopped) return
      source = new EventSource(`/api/events/${encodeURIComponent(streamId)}?after=${cursor}`)
      source.addEventListener('log', (event) => {
        try {
          append([JSON.parse((event as MessageEvent).data) as LogRow])
        } catch {
          // ignore partial frames; poll fallback recovers
        }
      })
      source.onerror = () => {
        source?.close()
        source = undefined
        if (!stopped && pollTimer === undefined) void poll()
      }
    })()

    return () => {
      stopped = true
      source?.close()
      if (pollTimer !== undefined) window.clearTimeout(pollTimer)
    }
  }, [resetKey, streamId, streamKey])

  return rows
}
