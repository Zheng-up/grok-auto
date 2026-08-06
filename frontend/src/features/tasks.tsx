import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { RotateCcw, Search, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { useFilteredLogs, type LogFilters } from '../lib/events'
import { Button, Card, Input, LogViewer, PageHeader } from '../components/ui'

type KindFilter = 'all' | 'batch' | 'operation'
type LevelFilter = '' | 'info' | 'success' | 'warning' | 'error'

const LEVELS: { id: LevelFilter; label: string }[] = [
  { id: '', label: '全部级别' },
  { id: 'info', label: '信息' },
  { id: 'success', label: '成功' },
  { id: 'warning', label: '警告' },
  { id: 'error', label: '错误' },
]

const KINDS: { id: KindFilter; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'batch', label: '注册' },
  { id: 'operation', label: '操作' },
]

function parseSearch(raw: string): { q?: string; streamId?: string } {
  const value = raw.trim()
  if (!value) return {}
  // Exact task stream id pins the stream for a tighter tail.
  if (/^(batch_|op_)[a-zA-Z0-9]+$/i.test(value)) {
    return { streamId: value, q: value }
  }
  return { q: value }
}

export function TasksPage() {
  const [kind, setKind] = useState<KindFilter>('all')
  const [level, setLevel] = useState<LevelFilter>('')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [resetKey, setResetKey] = useState(0)
  const [clearing, setClearing] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  const parsed = useMemo(() => parseSearch(search), [search])

  const filters: LogFilters = useMemo(() => ({
    kind,
    level,
    streamId: parsed.streamId,
    q: parsed.q,
    limit: 500,
  }), [kind, level, parsed.streamId, parsed.q])

  const logs = useFilteredLogs(filters, resetKey, true)

  // Keep streams query only for count hint / future; not primary UI.
  const streams = useQuery({
    queryKey: ['log-streams', kind],
    queryFn: () => api<{ stream_id: string; log_count: number }[]>(`/api/logs/streams?kind=${encodeURIComponent(kind)}&limit=80`),
    refetchInterval: 8000,
  })

  const counts = useMemo(() => {
    let success = 0
    let warning = 0
    let error = 0
    let info = 0
    for (const row of logs) {
      const msg = row.message || ''
      const lv = (row.level || '').toLowerCase()
      if (lv === 'success' || msg.includes('[+]')) success += 1
      else if (lv === 'warning' || msg.includes('[!]')) warning += 1
      else if (lv === 'error' || msg.includes('[-]')) error += 1
      else info += 1
    }
    return { success, warning, error, info, total: logs.length }
  }, [logs])

  const hardRefresh = () => setResetKey((value) => value + 1)

  const clearSearch = () => {
    setSearchInput('')
    setSearch('')
    setResetKey((value) => value + 1)
  }

  const clearAllLogs = async () => {
    if (clearing || !window.confirm('确认清空全部日志？仅删除日志内容，不会删除注册任务和操作任务。')) return
    setClearing(true)
    try {
      await api('/api/logs', { method: 'DELETE' })
      clearSearch()
      await streams.refetch()
      toast.success('全部日志已清空')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '清空日志失败')
    } finally {
      setClearing(false)
    }
  }


  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="任务日志"
        actions={(
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={hardRefresh}>
              <RotateCcw size={14} />
              刷新
            </Button>
            <Button variant="ghost" disabled={clearing} onClick={() => void clearAllLogs()}>
              <Trash2 size={14} />
              {clearing ? '清空中' : '清空日志'}
            </Button>
          </div>
        )}
      />

      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="shrink-0 space-y-3 border-b bg-[var(--panel)] p-3 sm:p-4">
          {/* Primary search */}
          <div className="relative min-w-0">
            <Search className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-[var(--muted)]" size={16} />
            <Input
              className="!pl-10 !pr-10 h-11 w-full min-w-0 text-sm"
              style={{ paddingLeft: 40, paddingRight: searchInput ? 40 : undefined }}
              value={searchInput}
              placeholder="搜索账号邮箱、账号ID、任务ID、批次ID、账号# …"
              onChange={(event) => setSearchInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  setSearch(searchInput.trim())
                  setResetKey((value) => value + 1)
                }
                if (event.key === 'Escape') clearSearch()
              }}
            />
            {searchInput ? (
              <button
                type="button"
                className="absolute right-2 top-1/2 z-10 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--soft)] hover:text-[var(--strong)]"
                onClick={clearSearch}
                aria-label="清除搜索"
                title="清除"
              >
                <X size={14} />
              </button>
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1">
              {KINDS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`rounded-lg px-3 py-1.5 text-sm transition ${
                    kind === item.id
                      ? 'bg-neutral-900 text-white dark:bg-white dark:text-black'
                      : 'muted hover:bg-[var(--soft)]'
                  }`}
                  onClick={() => { setKind(item.id); setResetKey((v) => v + 1) }}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="mx-1 hidden h-4 w-px bg-[var(--border)] sm:block" />
            <div className="flex flex-wrap gap-1">
              {LEVELS.map((item) => (
                <button
                  key={item.id || 'all'}
                  type="button"
                  className={`rounded-lg px-3 py-1.5 text-sm transition ${
                    level === item.id
                      ? 'bg-neutral-900 text-white dark:bg-white dark:text-black'
                      : 'muted hover:bg-[var(--soft)]'
                  }`}
                  onClick={() => { setLevel(item.id); setResetKey((v) => v + 1) }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="muted">显示 {counts.total} 条</span>
            <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-600 dark:text-emerald-300">成功 {counts.success}</span>
            <span className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-sky-600 dark:text-sky-300">信息 {counts.info}</span>
            <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-amber-700 dark:text-amber-300">警告 {counts.warning}</span>
            <span className="rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-red-600 dark:text-red-300">错误 {counts.error}</span>
            {search ? (
              <span className="inline-flex max-w-full items-center gap-1 truncate rounded-full border bg-[var(--soft)] px-2 py-0.5 font-mono text-[11px]">
                搜索 · {search}
                <button type="button" className="opacity-70 hover:opacity-100" onClick={clearSearch} aria-label="清除搜索">
                  <X size={12} />
                </button>
              </span>
            ) : null}
          </div>
        </div>

        <LogViewer
          rows={logs}
          className="min-h-0 min-w-0 flex-1 overflow-x-auto"
          emptyText={search ? `没有与「${search}」匹配的日志` : '暂无日志，等待新事件…'}
        />
      </Card>
    </div>
  )
}
