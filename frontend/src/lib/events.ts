import { useEffect, useState } from 'react'
import { api } from './api'

export type LogRow = { id: number; level: string; message: string; created_at: string; stream_id?: string }
type LogState = { streamKey?: string; rows: LogRow[] }

export type LogFilters = {
  kind?: 'all' | 'batch' | 'operation'
  level?: '' | 'info' | 'success' | 'warning' | 'error'
  streamId?: string
  q?: string
  limit?: number
}

function filtersKey(filters: LogFilters, resetKey: number) {
  return [
    filters.kind || 'all',
    filters.level || '',
    filters.streamId || '',
    filters.q || '',
    String(filters.limit || 400),
    String(resetKey),
  ].join('|')
}

export function useFilteredLogs(filters: LogFilters, resetKey = 0, live = true) {
  const [state, setState] = useState<LogState>({ rows: [] })
  const key = filtersKey(filters, resetKey)
  const rows = state.streamKey === key ? state.rows : []

  useEffect(() => {
    let cursor = 0
    let stopped = false
    let timer: number | undefined
    const seen = new Set<number>()
    setState({ streamKey: key, rows: [] })

    const buildUrl = (after: number) => {
      const params = new URLSearchParams()
      params.set('after', String(after))
      params.set('limit', String(filters.limit || 400))
      if (filters.kind && filters.kind !== 'all') params.set('kind', filters.kind)
      if (filters.level) params.set('level', filters.level)
      if (filters.streamId) params.set('stream_id', filters.streamId)
      if (filters.q?.trim()) params.set('q', filters.q.trim())
      return `/api/logs?${params.toString()}`
    }

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
        const currentRows = current.streamKey === key ? current.rows : []
        // Keep a larger tail for full-page viewer.
        return { streamKey: key, rows: [...currentRows, ...fresh].slice(-(filters.limit || 400)) }
      })
    }

    const tick = async () => {
      if (stopped) return
      try {
        append(await api<LogRow[]>(buildUrl(cursor || 0)))
        // First load uses after=0 (latest N). Subsequent polls use cursor.
        if (cursor === 0) {
          // If bootstrap returned rows, cursor already advanced in append.
          // If empty, keep after=0 until something appears.
        }
      } catch {
        // keep polling
      } finally {
        if (!stopped && live) timer = window.setTimeout(tick, 1500)
      }
    }

    void tick()
    return () => {
      stopped = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [key, live, filters.kind, filters.level, filters.streamId, filters.q, filters.limit])

  return rows
}

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

    void (async () => {
      if (stopped) return
      try {
        append(await api<LogRow[]>(`/api/logs/${encodeURIComponent(streamId)}?after=0`))
      } catch {
        // fall through
      }
      if (stopped) return
      source = new EventSource(`/api/events/${encodeURIComponent(streamId)}?after=${cursor}`)
      source.addEventListener('log', (event) => {
        try {
          append([JSON.parse((event as MessageEvent).data) as LogRow])
        } catch {
          // ignore
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
