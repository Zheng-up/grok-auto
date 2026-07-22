import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Search, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { useEventLog } from '../lib/events'
import type { Batch, Operation } from '../lib/types'
import { formatDbTime, operationLabel } from '../lib/labels'
import { Badge, Button, Card, Empty, Input, LogViewer, PageHeader, PaginationBar } from '../components/ui'

type TaskKind = 'batches' | 'operations'
const PAGE_SIZES = [20, 50, 100] as const

export function TasksPage() {
  const [kind, setKind] = useState<TaskKind>('batches')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [stream, setStream] = useState<string>()
  const [logResetKey, setLogResetKey] = useState(0)
  const [clearing, setClearing] = useState<'logs' | 'tasks' | null>(null)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const offset = (page - 1) * pageSize
  const q = search.trim()
  const qParam = q ? `&q=${encodeURIComponent(q)}` : ''

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim())
      setPage(1)
      setStream(undefined)
      setLogResetKey((value) => value + 1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  const batches = useQuery({
    queryKey: ['batches', page, pageSize, q],
    queryFn: () => api<Batch[]>(`/api/registration/batches?limit=${pageSize}&offset=${offset}${qParam}`),
    enabled: kind === 'batches',
    refetchInterval: 3000,
  })
  const batchCount = useQuery({
    queryKey: ['batches-count', q],
    queryFn: () => api<{ total: number }>(`/api/registration/batches/count?${q ? `q=${encodeURIComponent(q)}` : ''}`),
    enabled: kind === 'batches',
    refetchInterval: 3000,
  })
  const operations = useQuery({
    queryKey: ['operations', page, pageSize, q],
    queryFn: () => api<Operation[]>(`/api/operations?limit=${pageSize}&offset=${offset}${qParam}`),
    enabled: kind === 'operations',
    refetchInterval: 3000,
  })
  const operationCount = useQuery({
    queryKey: ['operations-count', q],
    queryFn: () => api<{ total: number }>(`/api/operations/count?${q ? `q=${encodeURIComponent(q)}` : ''}`),
    enabled: kind === 'operations',
    refetchInterval: 3000,
  })
  const activeRows = kind === 'batches' ? batches.data : operations.data
  const selectedStream = stream ?? activeRows?.[0]?.id
  const logs = useEventLog(selectedStream, logResetKey)
  const total = kind === 'batches' ? batchCount.data?.total ?? 0 : operationCount.data?.total ?? 0

  const switchKind = (nextKind: TaskKind) => {
    setKind(nextKind)
    setPage(1)
    setStream(undefined)
    setSearchInput('')
    setSearch('')
    setLogResetKey((value) => value + 1)
  }

  const refreshLists = async () => {
    await Promise.all([
      batches.refetch(),
      batchCount.refetch(),
      operations.refetch(),
      operationCount.refetch(),
    ])
  }

  const clearAllLogs = async () => {
    if (clearing || !window.confirm('确认清空全部日志？仅删除日志内容，不会删除注册任务和操作任务。')) return
    setClearing('logs')
    try {
      await api('/api/logs', { method: 'DELETE' })
      setStream(undefined)
      setPage(1)
      setLogResetKey((value) => value + 1)
      await refreshLists()
      toast.success('全部日志已清空')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '清空日志失败')
    } finally {
      setClearing(null)
    }
  }

  const clearAllTasks = async () => {
    if (
      clearing
      || !window.confirm('确认清空全部已结束任务？进行中/等待中/已暂停的任务会保留，关联日志会一并删除。')
    ) return
    setClearing('tasks')
    try {
      const result = await api<{ batches: number; operations: number; logs: number }>('/api/tasks', { method: 'DELETE' })
      setStream(undefined)
      setPage(1)
      setLogResetKey((value) => value + 1)
      await refreshLists()
      toast.success(`已清空任务：注册 ${result.batches} · 操作 ${result.operations}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '清空任务失败')
    } finally {
      setClearing(null)
    }
  }

  return <>
    <PageHeader title="任务日志" />
    <Card className="grid min-w-0 max-w-full overflow-hidden xl:h-[calc(100vh-160px)] xl:min-h-[580px] xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
      <section className="flex min-h-0 min-w-0 max-w-full flex-col border-b xl:border-b-0 xl:border-r">
        <div className="flex shrink-0 min-w-0 flex-col gap-2 border-b p-3 sm:flex-row sm:flex-wrap sm:items-center">
          <div className="flex min-w-0 shrink-0 items-center gap-1 overflow-x-auto">
            <button type="button" className={`shrink-0 rounded-lg px-3 py-2 text-sm sm:px-4 ${kind === 'batches' ? 'bg-neutral-900 text-white dark:bg-white dark:text-black' : 'muted hover:bg-[var(--soft)]'}`} onClick={() => switchKind('batches')}>注册任务</button>
            <button type="button" className={`shrink-0 rounded-lg px-3 py-2 text-sm sm:px-4 ${kind === 'operations' ? 'bg-neutral-900 text-white dark:bg-white dark:text-black' : 'muted hover:bg-[var(--soft)]'}`} onClick={() => switchKind('operations')}>操作任务</button>
          </div>
          <div className="relative min-w-0 w-full flex-1 sm:ml-auto sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-[var(--muted)]" size={14} />
            <Input
              className="!pl-9 w-full min-w-0"
              style={{ paddingLeft: 36 }}
              value={searchInput}
              placeholder={kind === 'batches' ? '搜索注册任务ID' : '搜索操作任务ID'}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </div>
        </div>

        <div className="scrollbar max-h-[min(420px,52vh)] min-h-0 min-w-0 flex-1 overflow-x-auto overflow-y-auto xl:max-h-none">
          {kind === 'batches'
            ? <BatchTable rows={batches.data ?? []} stream={selectedStream} onSelect={setStream} />
            : <OperationTable rows={operations.data ?? []} stream={selectedStream} onSelect={setStream} />}
        </div>
        <div className="shrink-0">
          <PaginationBar
            total={total}
            page={page}
            pageSize={pageSize}
            pageSizes={PAGE_SIZES}
            onPageChange={(next) => { setPage(next); setStream(undefined) }}
            onPageSizeChange={(next) => { setPageSize(next); setPage(1); setStream(undefined) }}
          />
        </div>
      </section>

      <section className="flex min-h-[280px] min-w-0 max-w-full flex-col bg-neutral-950 xl:min-h-0">
        <div className="flex shrink-0 min-w-0 flex-wrap items-center justify-between gap-2 border-b border-neutral-800 px-3 py-3 text-white sm:gap-3 sm:px-4">
          <div className="min-w-0 flex-1"><h2 className="truncate text-sm font-medium">{selectedStream ? `日志 · ${selectedStream}` : '任务日志'}</h2></div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              variant="ghost"
              className="px-2.5 text-xs text-neutral-300 hover:bg-neutral-800 sm:px-3 sm:text-sm"
              disabled={Boolean(clearing)}
              onClick={() => void clearAllTasks()}
            >
              <Trash2 size={14} />
              <span className="sm:hidden">清任务</span>
              <span className="hidden sm:inline">{clearing === 'tasks' ? '清空中' : '清空全部任务'}</span>
            </Button>
            <Button
              variant="ghost"
              className="px-2.5 text-xs text-neutral-300 hover:bg-neutral-800 sm:px-3 sm:text-sm"
              disabled={Boolean(clearing)}
              onClick={() => void clearAllLogs()}
            >
              <Trash2 size={14} />
              <span className="sm:hidden">清日志</span>
              <span className="hidden sm:inline">{clearing === 'logs' ? '清空中' : '清空全部日志'}</span>
            </Button>
          </div>
        </div>
        <LogViewer rows={selectedStream ? logs : []} className="min-h-0 min-w-0 max-w-full flex-1 overflow-x-auto" emptyText={selectedStream ? '暂无日志或正在连接…' : '请选择一个任务'} />
      </section>
    </Card>
  </>
}

function BatchTable({ rows, stream, onSelect }: { rows: Batch[]; stream?: string; onSelect: (id: string) => void }) {
  if (!rows.length) return <Empty />
  return <>
    <div className="divide-y sm:hidden">{rows.map((batch) => <button
      key={batch.id}
      type="button"
      className={`block w-full px-4 py-3 text-left transition ${stream === batch.id ? 'bg-[var(--soft)]' : 'hover:bg-[var(--soft)]'}`}
      onClick={() => onSelect(batch.id)}
    >
      <div className="flex min-w-0 items-center justify-between gap-3"><span className="min-w-0 max-w-full break-all font-mono text-[11px] leading-4">{batch.id}</span><Badge value={batch.status} /></div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div><span className="muted">进度</span><strong className="ml-2 font-medium">{batch.completed}/{batch.target_count}</strong></div>
        <div className="text-right"><span className="muted">结果</span><strong className="ml-2 font-medium">{batch.success} 成功 · {batch.failed} 失败</strong></div>
        <div className="muted col-span-2 truncate">{formatDbTime(batch.created_at, true)}</div>
      </div>
    </button>)}</div>
    <div className="hidden min-w-0 overflow-x-auto sm:block">
    <table className="w-full min-w-[640px] text-left text-sm">
      <thead className="sticky top-0 z-10 bg-[var(--panel)] text-xs muted">
        <tr>
          <th className="px-4 py-3 font-medium">注册任务ID</th>
          <th className="px-3 py-3 font-medium">进度</th>
          <th className="px-3 py-3 font-medium">结果</th>
          <th className="px-3 py-3 font-medium">创建时间</th>
          <th className="px-4 py-3 font-medium">状态</th>
        </tr>
      </thead>
      <tbody className="divide-y">{rows.map((batch) => (
        <tr
          key={batch.id}
          className={`cursor-pointer ${stream === batch.id ? 'bg-[var(--soft)]' : 'hover:bg-[var(--soft)]'}`}
          onClick={() => onSelect(batch.id)}
        >
          <td className="px-4 py-3 font-mono text-xs">{batch.id}</td>
          <td className="px-3 py-3">{batch.completed}/{batch.target_count}</td>
          <td className="px-3 py-3 text-xs">成功 {batch.success} · 失败 {batch.failed}</td>
          <td className="muted px-3 py-3 text-xs">{formatDbTime(batch.created_at, true)}</td>
          <td className="px-4 py-3"><Badge value={batch.status} /></td>
        </tr>
      ))}</tbody>
    </table>
    </div>
  </>
}

function OperationTable({ rows, stream, onSelect }: { rows: Operation[]; stream?: string; onSelect: (id: string) => void }) {
  if (!rows.length) return <Empty />
  return <>
    <div className="divide-y sm:hidden">{rows.map((operation) => <button
      key={operation.id}
      type="button"
      className={`block w-full px-4 py-3 text-left transition ${stream === operation.id ? 'bg-[var(--soft)]' : 'hover:bg-[var(--soft)]'}`}
      onClick={() => onSelect(operation.id)}
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span className="min-w-0 max-w-full break-all font-mono text-[11px] leading-4">{operation.id}</span>
        <Badge value={operation.status} />
      </div>
      <div className="mt-1.5 text-sm font-medium">{operationLabel(operation.kind)}</div>
      <div className="mt-2 flex items-center justify-between gap-4 text-xs">
        <span><span className="muted">进度</span><strong className="ml-2 font-medium">{operation.completed}/{operation.total}</strong></span>
        <span className="muted truncate text-right">{formatDbTime(operation.created_at, true)}</span>
      </div>
    </button>)}</div>
    <div className="hidden min-w-0 overflow-x-auto sm:block">
    <table className="w-full min-w-[640px] text-left text-sm">
      <thead className="sticky top-0 z-10 bg-[var(--panel)] text-xs muted">
        <tr>
          <th className="px-4 py-3 font-medium">操作任务ID</th>
          <th className="px-3 py-3 font-medium">操作类型</th>
          <th className="px-3 py-3 font-medium">进度</th>
          <th className="px-3 py-3 font-medium">创建时间</th>
          <th className="px-4 py-3 font-medium">状态</th>
        </tr>
      </thead>
      <tbody className="divide-y">{rows.map((operation) => (
        <tr
          key={operation.id}
          className={`cursor-pointer ${stream === operation.id ? 'bg-[var(--soft)]' : 'hover:bg-[var(--soft)]'}`}
          onClick={() => onSelect(operation.id)}
        >
          <td className="px-4 py-3 font-mono text-xs">{operation.id}</td>
          <td className="px-3 py-3 font-medium">{operationLabel(operation.kind)}</td>
          <td className="px-3 py-3">{operation.completed}/{operation.total}</td>
          <td className="muted px-3 py-3 text-xs">{formatDbTime(operation.created_at, true)}</td>
          <td className="px-4 py-3"><Badge value={operation.status} /></td>
        </tr>
      ))}</tbody>
    </table>
    </div>
  </>
}
