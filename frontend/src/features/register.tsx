import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Activity, CalendarCheck2, CircleCheckBig, CircleStop, Gauge, Play, Timer } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { useEventLog } from '../lib/events'
import type { Batch, Dashboard, Settings } from '../lib/types'
import { Badge, Button, Card, Field, Input, LogViewer, PageHeader } from '../components/ui'

const ACTIVE_BATCH_KEY = 'active-registration-batch'
const FINISHED = new Set(['completed', 'partial', 'failed', 'cancelled', 'interrupted', 'retried'])
const TERMINAL_JOB = new Set(['success', 'failed', 'cancelled', 'interrupted'])
const timestamp = (value?: string) => value ? new Date(value.endsWith('Z') ? value : `${value}Z`).getTime() : 0
const durationLabel = (milliseconds: number) => {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '—'
  const totalSeconds = Math.floor(milliseconds / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours) return `${hours}时 ${minutes}分 ${seconds}秒`
  if (minutes) return `${minutes}分 ${seconds}秒`
  return `${seconds}秒`
}

export function RegisterPage() {
  const [count, setCount] = useState<number>()
  const [concurrency, setConcurrency] = useState<number>()
  const [preferredBatchId, setPreferredBatchId] = useState<string | undefined>(() => localStorage.getItem(ACTIVE_BATCH_KEY) || undefined)
  const preferences = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/api/settings'), staleTime: 0, refetchOnMount: 'always' })
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/api/dashboard'), refetchInterval: 5000 })
  const preferredBatch = useQuery({ queryKey: ['batch', preferredBatchId], queryFn: () => api<Batch>(`/api/registration/batches/${preferredBatchId}`), enabled: Boolean(preferredBatchId) })
  const recentBatches = useQuery({ queryKey: ['registration-batches', 'latest'], queryFn: () => api<Batch[]>('/api/registration/batches?limit=1') })
  const missingPreferredBatch = preferredBatch.error instanceof ApiError && preferredBatch.error.status === 404
  const batchId = preferredBatchId && !missingPreferredBatch ? preferredBatchId : recentBatches.data?.[0]?.id
  const requestedCount = count ?? preferredBatch.data?.target_count ?? recentBatches.data?.[0]?.target_count ?? Number(preferences.data?.registration_count ?? 1)
  const requestedConcurrency = concurrency ?? preferredBatch.data?.concurrency ?? recentBatches.data?.[0]?.concurrency ?? Number(preferences.data?.registration_concurrency ?? 2)
  const batch = useQuery({
    queryKey: ['batch', batchId],
    queryFn: () => api<Batch>(`/api/registration/batches/${batchId}`),
    enabled: Boolean(batchId),
    refetchInterval: (query) => FINISHED.has(query.state.data?.status || '') ? false : 1200,
  })
  const logs = useEventLog(batchId)
  const current = batch.data
  const running = Boolean(current && ['queued', 'running', 'stopping', 'pausing', 'paused'].includes(current.status))

  useEffect(() => {
    if (batchId) localStorage.setItem(ACTIVE_BATCH_KEY, batchId)
    else if (!preferredBatch.isLoading && !recentBatches.isLoading) localStorage.removeItem(ACTIVE_BATCH_KEY)
  }, [batchId, preferredBatch.isLoading, recentBatches.isLoading])

  const start = useMutation({
    mutationFn: () => api<Batch>('/api/registration/batches', { method: 'POST', body: JSON.stringify({ count: requestedCount, concurrency: requestedConcurrency }) }),
    onSuccess: (value) => { localStorage.setItem(ACTIVE_BATCH_KEY, value.id); setPreferredBatchId(value.id); toast.success('注册批次已启动') },
    onError: (error) => toast.error(error.message),
  })
  const stop = async () => {
    if (!batchId) return
    try { await api(`/api/registration/batches/${batchId}/stop`, { method: 'POST' }); toast.success('已请求停止') }
    catch (error) { toast.error(error instanceof Error ? error.message : '停止失败') }
  }

  const progress = current ? Math.round((current.completed / Math.max(1, current.target_count)) * 100) : 0
  const batchEnd = current ? timestamp(current.updated_at) : 0
  const batchDuration = current ? Math.max(0, batchEnd - timestamp(current.created_at)) : 0
  const completedDurations = (current?.jobs ?? [])
    .filter((job) => TERMINAL_JOB.has(job.status) && job.started_at && job.updated_at)
    .map((job) => timestamp(job.updated_at) - timestamp(job.started_at))
    .filter((value) => value > 0)
  const averageDuration = completedDurations.length
    ? completedDurations.reduce((sum, value) => sum + value, 0) / completedDurations.length
    : 0

  return <div className="flex h-full min-h-0 flex-col">
    <PageHeader title="开始注册" />
    <Card className="grid min-h-0 flex-1 grid-rows-[auto_minmax(280px,1fr)] overflow-hidden xl:grid-cols-[minmax(580px,1.15fr)_minmax(380px,0.85fr)] xl:grid-rows-1">
      <section className="flex max-h-[46vh] min-h-0 flex-col border-b xl:max-h-none xl:border-b-0 xl:border-r">
        <div className="shrink-0 border-b px-7 py-4">
          <h2 className="font-medium">注册控制台</h2>
          <p className="muted mt-1 text-sm">设置批次参数并查看注册概览</p>
        </div>
        <div className="scrollbar min-h-0 flex-1 overflow-auto p-5">
          <div className="grid grid-cols-2 gap-3">
            <Field label="注册数量"><Input type="number" min={1} max={25000} value={requestedCount} onChange={(event) => setCount(Number(event.target.value))} /></Field>
            <Field label="并发数"><Input type="number" min={1} max={50} value={requestedConcurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></Field>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Button disabled={!preferences.data || start.isPending || running} onClick={() => start.mutate()}><Play size={16} />{preferences.isError ? '配置读取失败' : preferences.isLoading ? '读取中…' : start.isPending ? '创建中…' : '开始注册'}</Button>
            <Button variant="secondary" disabled={!running} onClick={stop}><CircleStop size={16} />停止</Button>
          </div>

          <div className="mt-6 flex items-center justify-between gap-3 border-t pt-5">
            <div><h3 className="text-sm font-medium">当前批次</h3><p className="muted mt-1 max-w-60 truncate font-mono text-xs">{current?.id ?? '尚未创建批次'}</p></div>
            {current && <Badge value={current.status} />}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3">
            <SplitSummary icon={Timer} label="批次耗时" firstLabel="平均" firstValue={durationLabel(averageDuration)} secondLabel="累计" secondValue={durationLabel(batchDuration)} firstTone="text-sky-600 dark:text-sky-400" secondTone="text-violet-600 dark:text-violet-400" />
            <SplitSummary icon={CircleCheckBig} label="批次结果" firstLabel="成功" firstValue={String(current?.success ?? 0)} secondLabel="失败" secondValue={String(current?.failed ?? 0)} firstTone="text-emerald-600 dark:text-emerald-400" secondTone="text-red-600 dark:text-red-400" />
            <SplitSummary icon={CalendarCheck2} label="今日注册" firstLabel="成功" firstValue={String(dashboard.data?.today.success ?? 0)} secondLabel="失败" secondValue={String(dashboard.data?.today.failed ?? 0)} firstTone="text-emerald-600 dark:text-emerald-400" secondTone="text-red-600 dark:text-red-400" />
            <SplitSummary icon={Activity} label="活动任务" firstLabel="注册" firstValue={String(dashboard.data?.active.active_batches ?? 0)} secondLabel="操作" secondValue={String(dashboard.data?.active.active_operations ?? 0)} firstTone="text-amber-600 dark:text-amber-400" secondTone="text-sky-600 dark:text-sky-400" />
          </div>
          <div className="mt-3"><ProgressSummary progress={progress} /></div>
        </div>
      </section>

      <section className="flex min-h-0 flex-col bg-neutral-950">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-neutral-800 px-5 py-4 text-white">
          <div><h2 className="text-sm font-medium">实时注册日志</h2><p className="mt-1 font-mono text-xs text-neutral-500">{current?.id ?? '等待创建注册批次'}</p></div>
          {current && <Badge value={current.status} />}
        </div>
        {current && <div className="h-1 shrink-0 bg-neutral-900"><div className="h-full bg-sky-500 transition-all" style={{ width: `${progress}%` }} /></div>}
        <LogViewer rows={logs} className="min-h-0 flex-1" emptyText={current ? '等待任务日志…' : '尚未创建注册批次'} />
      </section>
    </Card>
  </div>
}

function ProgressSummary({ progress }: { progress: number }) {
  return <section className="rounded-xl border bg-[var(--soft)]/60 p-4">
    <div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-sm font-medium"><Gauge size={18} className="text-sky-600 dark:text-sky-400" />当前进度</span><strong className="text-xl text-sky-600 dark:text-sky-400">{progress}%</strong></div>
    <div className="mt-3 h-2 overflow-hidden rounded-full bg-[var(--panel)]"><div className="h-full rounded-full bg-sky-500 transition-all duration-500" style={{ width: `${progress}%` }} /></div>
  </section>
}

function SplitSummary({ icon: Icon, label, firstLabel, firstValue, secondLabel, secondValue, firstTone, secondTone }: { icon: typeof Timer; label: string; firstLabel: string; firstValue: string; secondLabel: string; secondValue: string; firstTone: string; secondTone: string }) {
  return <section className="rounded-xl border bg-[var(--soft)]/60 p-4">
    <div className="flex items-center gap-2 text-sm font-medium"><Icon size={18} className="muted" />{label}</div>
    <div className="mt-3 grid grid-cols-2 gap-3">
      <div><div className="muted text-xs">{firstLabel}</div><strong className={`mt-1 block truncate text-lg ${firstTone}`} title={firstValue}>{firstValue}</strong></div>
      <div><div className="muted text-xs">{secondLabel}</div><strong className={`mt-1 block truncate text-lg ${secondTone}`} title={secondValue}>{secondValue}</strong></div>
    </div>
  </section>
}