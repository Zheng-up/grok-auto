import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CircleCheckBig, CircleStop, Gauge, Play, Timer } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { useEventLog } from '../lib/events'
import type { Batch, Settings } from '../lib/types'
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
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden">
      <div className="grid shrink-0 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="p-4">
          <h2 className="font-medium">注册参数</h2>
          <div className="mt-4 space-y-3">
            <Field label="注册数量"><Input type="number" min={1} max={25000} value={requestedCount} onChange={(event) => setCount(Number(event.target.value))} /></Field>
            <Field label="并发数"><Input type="number" min={1} max={50} value={requestedConcurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></Field>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <Button disabled={!preferences.data || start.isPending || running} onClick={() => start.mutate()}><Play size={16} />{preferences.isError ? '配置读取失败' : preferences.isLoading ? '读取中…' : start.isPending ? '创建中…' : '开始注册'}</Button>
            <Button variant="secondary" disabled={!running} onClick={stop}><CircleStop size={16} />停止</Button>
          </div>
        </Card>

        <div className="grid gap-3">
          <SplitSummary icon={Timer} label="注册时间" firstLabel="平均" firstValue={durationLabel(averageDuration)} secondLabel="本次" secondValue={durationLabel(batchDuration)} firstTone="text-sky-600 dark:text-sky-400" secondTone="text-violet-600 dark:text-violet-400" />
          <SplitSummary icon={CircleCheckBig} label="注册结果" firstLabel="成功" firstValue={String(current?.success ?? 0)} secondLabel="失败" secondValue={String(current?.failed ?? 0)} firstTone="text-emerald-600 dark:text-emerald-400" secondTone="text-red-600 dark:text-red-400" />
          <ProgressSummary progress={progress} completed={current?.completed ?? 0} total={current?.target_count ?? 0} />
        </div>
      </div>

      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3">
          <h2 className="font-medium">实时注册日志</h2>
          {current && <><span className="muted font-mono text-xs">{current.id}</span><Badge value={current.status} /></>}
        </div>
        {current && <div className="h-1 shrink-0 bg-[var(--soft)]"><div className="h-full bg-neutral-900 transition-all dark:bg-white" style={{ width: `${progress}%` }} /></div>}
        <LogViewer rows={logs} className="min-h-0 flex-1" emptyText={current ? '等待任务日志…' : '尚未创建注册批次'} />
      </Card>
    </div>
  </div>
}

function ProgressSummary({ progress, completed, total }: { progress: number; completed: number; total: number }) {
  return <Card className="flex min-h-20 items-center gap-3 px-4 py-3"><span className="muted flex size-9 items-center justify-center rounded-xl bg-[var(--soft)]"><Gauge size={17} /></span><div className="min-w-0 flex-1"><div className="flex items-center justify-between"><span className="muted text-xs">当前进度</span><span className="text-sm font-semibold text-sky-600 dark:text-sky-400">{progress}%</span></div><div className="muted mt-0.5 text-[10px]">已完成注册 {completed} / {total}</div><div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--soft)]"><div className="h-full rounded-full bg-sky-500 transition-all duration-500" style={{ width: `${progress}%` }} /></div></div></Card>
}

function SplitSummary({ icon: Icon, label, firstLabel, firstValue, secondLabel, secondValue, firstTone, secondTone }: { icon: typeof Timer; label: string; firstLabel: string; firstValue: string; secondLabel: string; secondValue: string; firstTone: string; secondTone: string }) {
  return <Card className="flex min-h-20 items-center gap-3 px-4 py-3"><span className="muted flex size-9 items-center justify-center rounded-xl bg-[var(--soft)]"><Icon size={17} /></span><div className="min-w-0 flex-1"><div className="muted mb-1 text-xs">{label}</div><div className="flex items-center justify-between gap-3 text-xs"><span className="muted">{firstLabel}</span><strong className={firstTone}>{firstValue}</strong></div><div className="mt-0.5 flex items-center justify-between gap-3 text-xs"><span className="muted">{secondLabel}</span><strong className={secondTone}>{secondValue}</strong></div></div></Card>
}