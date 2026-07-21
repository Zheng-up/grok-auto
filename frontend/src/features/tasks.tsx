import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { useEventLog } from '../lib/events'
import type { Batch, Operation } from '../lib/types'
import { operationLabel } from '../lib/labels'
import { Badge, Button, Card, Empty, LogViewer, PageHeader, PaginationBar } from '../components/ui'

type TaskKind = 'batches' | 'operations'
const PAGE_SIZES = [20, 50, 100] as const

export function TasksPage() {
  const [kind, setKind] = useState<TaskKind>('batches')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [stream, setStream] = useState<string>()
  const [logResetKey, setLogResetKey] = useState(0)
  const [clearing, setClearing] = useState(false)
  const offset = (page - 1) * pageSize
  const batches = useQuery({
    queryKey: ['batches', page, pageSize],
    queryFn: () => api<Batch[]>(`/api/registration/batches?limit=${pageSize}&offset=${offset}`),
    enabled: kind === 'batches',
    refetchInterval: 3000,
  })
  const batchCount = useQuery({ queryKey: ['batches-count'], queryFn: () => api<{ total: number }>('/api/registration/batches/count'), enabled: kind === 'batches', refetchInterval: 3000 })
  const operations = useQuery({
    queryKey: ['operations', page, pageSize],
    queryFn: () => api<Operation[]>(`/api/operations?limit=${pageSize}&offset=${offset}`),
    enabled: kind === 'operations',
    refetchInterval: 3000,
  })
  const operationCount = useQuery({ queryKey: ['operations-count'], queryFn: () => api<{ total: number }>('/api/operations/count'), enabled: kind === 'operations', refetchInterval: 3000 })
  const activeRows = kind === 'batches' ? batches.data : operations.data
  const selectedStream = stream ?? activeRows?.[0]?.id
  const logs = useEventLog(selectedStream, logResetKey)
  const total = kind === 'batches' ? batchCount.data?.total ?? 0 : operationCount.data?.total ?? 0

  const switchKind = (nextKind: TaskKind) => {
    setKind(nextKind)
    setPage(1)
    setStream(undefined)
    setLogResetKey((value) => value + 1)
  }
  const clearAllLogs = async () => {
    if (clearing || !window.confirm('确认清空全部任务历史？日志、注册批次和账号操作列表都会被删除，账号数据不会删除。')) return
    setClearing(true)
    try {
      await api('/api/logs', { method: 'DELETE' })
      setStream(undefined)
      setPage(1)
      setLogResetKey((value) => value + 1)
      await Promise.all([batches.refetch(), batchCount.refetch(), operations.refetch(), operationCount.refetch()])
      toast.success('全部任务历史已清空')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '清空日志失败')
    } finally {
      setClearing(false)
    }
  }

  return <>
    <PageHeader title="任务日志" />
    <Card className="grid h-[calc(100vh-160px)] min-h-[580px] overflow-hidden xl:grid-cols-[minmax(580px,1.15fr)_minmax(380px,0.85fr)]">
      <section className="flex min-h-0 flex-col border-b xl:border-b-0 xl:border-r">
        <div className="flex shrink-0 items-center gap-1 border-b p-3">
          <button type="button" className={`rounded-lg px-4 py-2 text-sm ${kind === 'batches' ? 'bg-neutral-900 text-white dark:bg-white dark:text-black' : 'muted hover:bg-[var(--soft)]'}`} onClick={() => switchKind('batches')}>注册批次</button>
          <button type="button" className={`rounded-lg px-4 py-2 text-sm ${kind === 'operations' ? 'bg-neutral-900 text-white dark:bg-white dark:text-black' : 'muted hover:bg-[var(--soft)]'}`} onClick={() => switchKind('operations')}>账号操作</button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto scrollbar">
          {kind === 'batches' ? <BatchTable rows={batches.data ?? []} stream={selectedStream} onSelect={setStream} /> : <OperationTable rows={operations.data ?? []} stream={selectedStream} onSelect={setStream} />}
        </div>
        <div className="shrink-0"><PaginationBar total={total} page={page} pageSize={pageSize} pageSizes={PAGE_SIZES} onPageChange={(next) => { setPage(next); setStream(undefined) }} onPageSizeChange={(next) => { setPageSize(next); setPage(1); setStream(undefined) }} /></div>
      </section>

      <section className="flex min-h-0 flex-col bg-neutral-950">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-neutral-800 px-4 py-3 text-white">
          <div className="min-w-0"><h2 className="truncate text-sm font-medium">{selectedStream ? `日志 · ${selectedStream}` : '任务日志'}</h2></div>
          <Button variant="ghost" className="shrink-0 text-neutral-300 hover:bg-neutral-800" disabled={clearing} onClick={() => void clearAllLogs()}><Trash2 size={14} />{clearing ? '清空中' : '清空全部日志'}</Button>
        </div>
        <LogViewer rows={selectedStream ? logs : []} className="min-h-0 flex-1" emptyText={selectedStream ? '暂无日志或正在连接…' : '请选择一个任务'} />
      </section>
    </Card>
  </>
}

function BatchTable({ rows, stream, onSelect }: { rows: Batch[]; stream?: string; onSelect: (id: string) => void }) {
  if (!rows.length) return <Empty />
  return <table className="w-full min-w-[660px] text-left text-sm"><thead className="sticky top-0 z-10 bg-[var(--panel)] text-xs muted"><tr><th className="px-4 py-3 font-medium">批次</th><th className="px-3 py-3 font-medium">进度</th><th className="px-3 py-3 font-medium">结果</th><th className="px-3 py-3 font-medium">创建时间</th><th className="px-4 py-3 font-medium">状态</th></tr></thead><tbody className="divide-y">{rows.map((batch) => <tr key={batch.id} className={`cursor-pointer ${stream === batch.id ? 'bg-[var(--soft)]' : 'hover:bg-[var(--soft)]'}`} onClick={() => onSelect(batch.id)}><td className="px-4 py-3 font-mono text-xs">{batch.id}</td><td className="px-3 py-3">{batch.completed}/{batch.target_count}</td><td className="px-3 py-3 text-xs">成功 {batch.success} · 失败 {batch.failed}</td><td className="muted px-3 py-3 text-xs">{new Date(`${batch.created_at}Z`).toLocaleString()}</td><td className="px-4 py-3"><Badge value={batch.status} /></td></tr>)}</tbody></table>
}

function OperationTable({ rows, stream, onSelect }: { rows: Operation[]; stream?: string; onSelect: (id: string) => void }) {
  if (!rows.length) return <Empty />
  return <table className="w-full min-w-[660px] text-left text-sm"><thead className="sticky top-0 z-10 bg-[var(--panel)] text-xs muted"><tr><th className="px-4 py-3 font-medium">操作类型</th><th className="px-3 py-3 font-medium">任务 ID</th><th className="px-3 py-3 font-medium">进度</th><th className="px-3 py-3 font-medium">创建时间</th><th className="px-4 py-3 font-medium">状态</th></tr></thead><tbody className="divide-y">{rows.map((operation) => <tr key={operation.id} className={`cursor-pointer ${stream === operation.id ? 'bg-[var(--soft)]' : 'hover:bg-[var(--soft)]'}`} onClick={() => onSelect(operation.id)}><td className="px-4 py-3 font-medium">{operationLabel(operation.kind)}</td><td className="px-3 py-3 font-mono text-xs">{operation.id}</td><td className="px-3 py-3">{operation.completed}/{operation.total}</td><td className="muted px-3 py-3 text-xs">{new Date(`${operation.created_at}Z`).toLocaleString()}</td><td className="px-4 py-3"><Badge value={operation.status} /></td></tr>)}</tbody></table>
}