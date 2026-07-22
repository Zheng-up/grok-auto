import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, Pause, Play, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { useEventLog } from '../lib/events'
import type { Batch, Settings } from '../lib/types'
import { Badge, Button, Card, Field, Input, LogViewer, PageHeader } from '../components/ui'

const ACTIVE_BATCH_KEY = 'active-registration-batch'
const WORKSPACE_STATUSES = new Set(['queued', 'running', 'stopping', 'pausing', 'waiting', 'paused', 'failed', 'partial', 'interrupted'])
const STATUS_ORDER: Record<string, number> = {
  running: 0,
  stopping: 1,
  pausing: 1,
  waiting: 2,
  queued: 3,
  paused: 4,
  failed: 5,
  partial: 5,
  interrupted: 5,
}

const formatDurationShort = (seconds?: number | null) => {
  const total = Math.max(0, Math.floor(Number(seconds || 0)))
  if (!total) return '—'
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) return `${hours}时${minutes}分${secs}秒`
  if (minutes > 0) return `${minutes}分${secs}秒`
  return `${secs}秒`
}

const canPause = (status: string) => ['queued', 'running', 'waiting'].includes(status)
const canStart = (status: string) => status === 'paused'
const canEnd = (status: string) => ['queued', 'running', 'stopping', 'pausing', 'paused', 'waiting'].includes(status)
const canRetry = (status: string) => ['failed', 'partial', 'interrupted'].includes(status)

export function RegisterPage() {
  const client = useQueryClient()
  const [count, setCount] = useState('10')
  const [concurrency, setConcurrency] = useState('2')
  const [selectedBatchId, setSelectedBatchId] = useState<string | undefined>(() => localStorage.getItem(ACTIVE_BATCH_KEY) || undefined)
  const [controlling, setControlling] = useState<string>()

  const preferences = useQuery({
    queryKey: ['settings'],
    queryFn: () => api<Settings>('/api/settings'),
    staleTime: 0,
    refetchOnMount: 'always',
  })

  const batchesQuery = useQuery({
    queryKey: ['registration-batches', 'register-page'],
    queryFn: () => api<Batch[]>('/api/registration/batches?limit=500'),
    refetchInterval: 2000,
  })

  const tasks = useMemo(() => {
    return (batchesQuery.data ?? [])
      .filter((item) => WORKSPACE_STATUSES.has(item.status))
      .sort((left, right) =>
        (STATUS_ORDER[left.status] ?? 9) - (STATUS_ORDER[right.status] ?? 9)
        || right.created_at.localeCompare(left.created_at),
      )
  }, [batchesQuery.data])

  const globalRegActive = Boolean(
    (batchesQuery.data ?? []).some((item) => ['queued', 'running', 'stopping', 'pausing'].includes(item.status)),
  )

  useEffect(() => {
    if (selectedBatchId) localStorage.setItem(ACTIVE_BATCH_KEY, selectedBatchId)
  }, [selectedBatchId])

  const logs = useEventLog('registration')

  useEffect(() => {
    if (!preferences.data) return
    // Only seed once when still at default empty/initial and not actively cleared.
    if (preferences.data.registration_concurrency != null && concurrency === '2') {
      setConcurrency(String(preferences.data.registration_concurrency))
    }
  }, [preferences.data])

  useEffect(() => {
    if (globalRegActive) return
    const value = Number(concurrency)
    if (!Number.isInteger(value) || value < 1 || value > 50) return
    if (Number(preferences.data?.registration_concurrency) === value) return
    const timer = window.setTimeout(() => {
      void api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ values: { registration_concurrency: value } }),
      }).catch(() => undefined)
    }, 400)
    return () => window.clearTimeout(timer)
  }, [concurrency, globalRegActive, preferences.data?.registration_concurrency])

  const refreshTasks = async () => {
    await Promise.all([
      batchesQuery.refetch(),
      client.invalidateQueries({ queryKey: ['global-task-space'] }),
      selectedBatchId ? client.invalidateQueries({ queryKey: ['batch', selectedBatchId] }) : Promise.resolve(),
    ])
  }

  const parsePositiveInt = (raw: string, label: string, min: number, max: number) => {
    const trimmed = raw.trim()
    if (!trimmed) {
      toast.error(`请设置${label}`)
      return null
    }
    const value = Number(trimmed)
    if (!Number.isFinite(value) || !Number.isInteger(value)) {
      toast.error(`${label}必须是整数`)
      return null
    }
    if (value < min || value > max) {
      toast.error(`${label}需在 ${min}–${max} 之间`)
      return null
    }
    return value
  }

  const start = useMutation({
    mutationFn: () => {
      const n = parsePositiveInt(count, '注册数量', 1, 25000)
      if (n === null) return Promise.reject(new Error('请设置注册数量'))
      const c = parsePositiveInt(concurrency, '并发数', 1, 50)
      if (c === null) return Promise.reject(new Error('请设置并发数'))
      return api<Batch>('/api/registration/batches', { method: 'POST', body: JSON.stringify({ count: n, concurrency: c }) })
    },
    onSuccess: async (value) => {
      localStorage.setItem(ACTIVE_BATCH_KEY, value.id)
      setSelectedBatchId(value.id)
      toast.success(value.status === 'waiting' ? '任务已创建，等待全局槽位' : '注册任务已启动')
      await refreshTasks()
    },
    onError: (error) => {
      if (error instanceof Error && (error.message === '请设置注册数量' || error.message === '请设置并发数')) return
      toast.error(error instanceof Error ? error.message : '启动失败')
    },
  })

  const control = async (batchId: string, action: 'pause' | 'resume' | 'stop' | 'retry', silent = false) => {
    if (action === 'retry') {
      const retried = await api<{ id: string }>(`/api/registration/batches/${batchId}/retry`, { method: 'POST' })
      setSelectedBatchId(retried.id)
      localStorage.setItem(ACTIVE_BATCH_KEY, retried.id)
      if (!silent) toast.success('已在本任务重试失败账号')
      return retried
    }
    await api(`/api/registration/batches/${batchId}/${action}`, { method: 'POST' })
    if (!silent) toast.success(action === 'pause' ? '已请求暂停' : action === 'stop' ? '已请求结束' : '已请求启动')
  }

  const runBulk = async (
    action: 'pause' | 'resume' | 'stop' | 'retry',
    candidates: Batch[],
    labels: { empty: string; confirm?: string; ok: (n: number) => string; mixed: (ok: number, fail: number) => string },
  ) => {
    if (controlling) return
    if (!candidates.length) {
      toast.info(labels.empty)
      return
    }
    if (labels.confirm && !window.confirm(labels.confirm)) return
    setControlling('all')
    let succeeded = 0
    let failed = 0
    try {
      // Resume/start oldest first so shared slots fill deterministically.
      const ordered = action === 'resume'
        ? [...candidates].sort((a, b) => a.created_at.localeCompare(b.created_at))
        : candidates
      for (const task of ordered) {
        try {
          await control(task.id, action, true)
          succeeded += 1
        } catch {
          failed += 1
        }
      }
      await refreshTasks()
    } finally {
      setControlling(undefined)
    }
    if (failed) toast.warning(labels.mixed(succeeded, failed))
    else toast.success(labels.ok(succeeded))
  }

  const pauseAll = () => void runBulk(
    'pause',
    tasks.filter((task) => canPause(task.status)),
    {
      empty: '没有可暂停的注册任务',
      ok: (n) => `已依次暂停 ${n} 个注册任务`,
      mixed: (ok, fail) => `批量暂停完成：成功 ${ok}，失败 ${fail}`,
    },
  )

  const startAll = () => void runBulk(
    'resume',
    tasks.filter((task) => canStart(task.status)),
    {
      empty: '没有可启动的注册任务',
      ok: (n) => n > 1 ? `已启动 ${n} 个注册任务（共享全局并发槽位）` : `已启动 ${n} 个注册任务`,
      mixed: (ok, fail) => `批量启动完成：成功 ${ok}，失败 ${fail}`,
    },
  )

  const endAll = () => void runBulk(
    'stop',
    tasks.filter((task) => canEnd(task.status)),
    {
      empty: '没有可结束的注册任务',
      confirm: `确认结束 ${tasks.filter((task) => canEnd(task.status)).length} 个注册任务？未完成账号将取消。`,
      ok: (n) => `已结束 ${n} 个注册任务`,
      mixed: (ok, fail) => `批量结束完成：成功 ${ok}，失败 ${fail}`,
    },
  )

  const retryAll = () => void runBulk(
    'retry',
    tasks.filter((task) => canRetry(task.status)),
    {
      empty: '没有可重试的注册任务',
      ok: (n) => `已重试 ${n} 个任务的失败账号`,
      mixed: (ok, fail) => `批量重试完成：成功 ${ok}，失败 ${fail}`,
    },
  )

  const singleControl = async (batchId: string, action: 'pause' | 'resume' | 'stop' | 'retry') => {
    if (controlling) return
    setControlling(batchId)
    try {
      await control(batchId, action, false)
      await refreshTasks()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '操作失败')
    } finally {
      setControlling(undefined)
    }
  }

  return <>
    <PageHeader title="开始注册" />
    <Card className="grid min-h-0 overflow-hidden xl:h-[calc(100vh-160px)] xl:min-h-[580px] xl:grid-cols-[minmax(560px,1.15fr)_minmax(380px,0.85fr)]">
      <section className="flex min-h-0 flex-col border-b xl:border-b-0 xl:border-r">
        <div className="shrink-0 border-b px-5 py-4 sm:px-7">
          <h2 className="font-medium">注册控制台</h2>
          <p className="muted mt-1 text-sm">设置参数，开始任务，并管理注册任务列表</p>
        </div>

        <div className="scrollbar max-h-[min(720px,70vh)] min-h-0 overflow-auto p-4 sm:p-5 xl:max-h-none xl:flex-1">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)] sm:items-start">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium opacity-0 select-none">开始</span>
              <Button
                className="h-[38px] min-h-[38px] w-full sm:w-auto sm:min-w-[7.5rem]"
                disabled={!preferences.data || start.isPending}
                onClick={() => start.mutate()}
              >
                <Play size={16} />
                {preferences.isError ? '配置读取失败' : preferences.isLoading ? '读取中…' : start.isPending ? '创建中…' : '开始任务'}
              </Button>
            </label>
            <Field label="注册数量">
              <Input type="number" min={1} max={25000} value={count} placeholder="请输入" onChange={(event) => setCount(event.target.value)} />
            </Field>
            <Field label="并发数" hint={globalRegActive ? '有注册任务进行中，全局并发槽位已锁定' : '所有注册批次共享此并发槽位'}>
              <Input
                type="number"
                min={1}
                max={50}
                value={concurrency}
                placeholder="请输入"
                disabled={globalRegActive}
                onChange={(event) => setConcurrency(event.target.value)}
              />
            </Field>
          </div>

          <div className="mt-6 border-t pt-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <h3 className="text-sm font-medium">注册任务</h3>
                <p className="muted mt-1 text-xs">与任务空间「注册任务」同步，一行一个批次</p>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="muted mr-1 text-xs">{tasks.length} 个</span>
                <Button
                  variant="secondary"
                  className="!min-h-6 !gap-0.5 !rounded-md !px-1.5 !py-0 !text-[10px] !leading-none !font-normal"
                  disabled={Boolean(controlling) || !tasks.some((task) => canPause(task.status))}
                  onClick={() => pauseAll()}
                >
                  <Pause size={10} />暂停
                </Button>
                <Button
                  variant="secondary"
                  className="!min-h-6 !gap-0.5 !rounded-md !px-1.5 !py-0 !text-[10px] !leading-none !font-normal"
                  disabled={Boolean(controlling) || !tasks.some((task) => canStart(task.status))}
                  onClick={() => startAll()}
                >
                  <Play size={10} />启动
                </Button>
                <Button
                  variant="secondary"
                  className="!min-h-6 !gap-0.5 !rounded-md !px-1.5 !py-0 !text-[10px] !leading-none !font-normal"
                  disabled={Boolean(controlling) || !tasks.some((task) => canEnd(task.status))}
                  onClick={() => endAll()}
                >
                  <CircleStop size={10} />结束
                </Button>
                <Button
                  variant="secondary"
                  className="!min-h-6 !gap-0.5 !rounded-md !px-1.5 !py-0 !text-[10px] !leading-none !font-normal"
                  disabled={Boolean(controlling) || !tasks.some((task) => canRetry(task.status))}
                  onClick={() => retryAll()}
                >
                  <RotateCcw size={10} className={controlling === 'all' ? 'animate-spin' : ''} />重试
                </Button>
              </div>
            </div>

            {!tasks.length ? (
              <div className="rounded-xl border border-dashed px-4 py-6 text-center text-sm text-[var(--muted)]">
                暂无注册任务
              </div>
            ) : (
              <div className="space-y-1.5">
                {tasks.map((task) => {
                  const pct = Math.round((task.completed / Math.max(1, task.target_count)) * 100)
                  const busyRow = controlling === task.id || controlling === 'all'
                  const success = Number(task.success ?? 0)
                  const failed = Number(task.failed ?? 0)
                  const btn = '!min-h-6 !gap-0.5 !rounded-md !px-1.5 !py-0 !text-[10px] !font-medium'
                  return (
                    <div key={task.id} className={`rounded-lg border bg-[var(--panel)] px-2.5 py-2 ${busyRow ? 'opacity-70' : ''}`}>
                      {/* 顶栏：状态 · ID · 9/10 · 操作 */}
                      <div className="flex items-center gap-2">
                        <Badge value={task.status} />
                        <span className="min-w-0 truncate font-mono text-[11px] text-[var(--muted)]">{task.id}</span>
                        <span className="shrink-0 text-sm font-semibold tabular-nums tracking-tight">
                          {task.completed}<span className="font-normal text-[var(--muted)]">/{task.target_count}</span>
                        </span>
                        <div className="ml-auto flex shrink-0 items-center gap-0.5">
                          {canPause(task.status) && (
                            <Button variant="secondary" className={btn} disabled={busyRow} onClick={() => void singleControl(task.id, 'pause')}>
                              <Pause size={11} />暂停
                            </Button>
                          )}
                          {canStart(task.status) && (
                            <Button variant="secondary" className={btn} disabled={busyRow} onClick={() => void singleControl(task.id, 'resume')}>
                              <Play size={11} />启动
                            </Button>
                          )}
                          {canEnd(task.status) && (
                            <Button variant="secondary" className={btn} disabled={busyRow} onClick={() => void singleControl(task.id, 'stop')}>
                              <CircleStop size={11} />结束
                            </Button>
                          )}
                          {canRetry(task.status) && (
                            <Button variant="secondary" className={btn} disabled={busyRow} onClick={() => void singleControl(task.id, 'retry')}>
                              <RotateCcw size={11} />重试
                            </Button>
                          )}
                        </div>
                      </div>

                      {/* 进度条 + 百分比在后 */}
                      <div className="mt-1.5 flex items-center gap-2">
                        <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--soft)]">
                          <div className="h-full rounded-full bg-sky-500 transition-all duration-500" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="w-9 shrink-0 text-right text-[11px] font-medium tabular-nums text-[var(--muted)]">{pct}%</span>
                      </div>

                      {/* 底栏 */}
                      <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px]">
                        <div className="flex items-center gap-2.5">
                          <span className="text-[var(--muted)]">成功 <span className="font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{success}</span></span>
                          <span className="text-[var(--muted)]">失败 <span className="font-semibold tabular-nums text-red-600 dark:text-red-400">{failed}</span></span>
                        </div>
                        <div className="flex items-center gap-2.5 text-[var(--muted)]">
                          <span>总 <span className="font-medium tabular-nums text-[var(--strong)]">{formatDurationShort(task.elapsed_seconds)}</span></span>
                          <span>均 <span className="font-medium tabular-nums text-[var(--strong)]">{formatDurationShort(task.avg_account_seconds)}</span></span>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="flex min-h-[min(420px,48vh)] flex-col bg-neutral-950 xl:min-h-0">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-neutral-800 px-4 py-3 text-white sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h2 className="text-sm font-medium">实时注册日志</h2>
            <p className="mt-1 text-xs text-neutral-500">所有注册任务 · 与左侧任务列表同步</p>
          </div>
          <span className="shrink-0 rounded-full border border-neutral-700 px-2 py-0.5 text-xs text-neutral-400">{tasks.length} 个任务</span>
        </div>
        <LogViewer rows={logs} className="min-h-0 flex-1" emptyText="等待注册任务日志…" />
      </section>
    </Card>
  </>
}
