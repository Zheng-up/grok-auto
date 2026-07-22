import { useQuery } from '@tanstack/react-query'
import { Activity, CircleStop, Pause, Play, RotateCcw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { operationLabel } from '../lib/labels'
import type { Batch, Operation } from '../lib/types'
import { Badge } from '../components/ui'

const ACTIVE_STATUSES = new Set(['queued', 'running', 'stopping', 'pausing'])
const WAITING_STATUSES = new Set(['waiting']) // registration serial queue + operation rate-limit
const PAUSED_STATUSES = new Set(['paused'])
const FAILED_STATUSES = new Set(['failed', 'partial', 'interrupted'])
const WORKSPACE_STATUSES = new Set([...ACTIVE_STATUSES, ...WAITING_STATUSES, ...PAUSED_STATUSES, ...FAILED_STATUSES])
const STATUS_ORDER: Record<string, number> = { running: 0, stopping: 1, pausing: 1, waiting: 2, queued: 3, paused: 4, failed: 5, partial: 5, interrupted: 5 }

type TaskGroup = 'registration' | 'operation' | 'auths'

type TaskSpaceTask = {
  id: string
  kind: 'batch' | 'operation'
  group: TaskGroup
  opKind?: string
  label: string
  status: string
  completed: number
  total: number
  createdAt: string
}

const GROUP_META: Record<TaskGroup, { label: string; className: string }> = {
  registration: {
    label: '注册任务',
    className: 'border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300',
  },
  operation: {
    label: '操作任务',
    className: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  },
  auths: {
    label: 'auths 任务',
    className: 'border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  },
}

// Registration state machine buttons
// waiting:  pause, end
// paused:   start, end
// queued/running: pause, end
// pausing/stopping: end
// failed/partial/interrupted: retry
const regCanPause = (status: string) => ['queued', 'running', 'waiting'].includes(status)
const regCanStart = (status: string) => status === 'paused'
const regCanEnd = (status: string) => ['queued', 'running', 'stopping', 'pausing', 'paused', 'waiting', 'interrupted'].includes(status)
const regCanRetry = (status: string) => FAILED_STATUSES.has(status)
const opCanPause = (status: string) => ['queued', 'running'].includes(status)
const opCanStart = (status: string) => status === 'paused'
const opCanEnd = (status: string) => ['queued', 'running', 'stopping', 'pausing', 'paused', 'waiting'].includes(status)
const opCanRetry = (status: string) => FAILED_STATUSES.has(status)

export function GlobalTaskStatus() {
  const [open, setOpen] = useState(false)
  const [retrying, setRetrying] = useState<string>()
  const [controlling, setControlling] = useState<string>()
  const rootRef = useRef<HTMLElement>(null)
  const query = useQuery({
    queryKey: ['global-task-space'],
    queryFn: async () => {
      const [batches, operations] = await Promise.all([
        api<Batch[]>('/api/registration/batches?limit=500'),
        api<Operation[]>('/api/operations?limit=500'),
      ])
      return [
        ...batches.map((item): TaskSpaceTask => ({
          id: item.id,
          kind: 'batch',
          group: 'registration',
          label: '注册批次',
          status: item.status,
          completed: item.completed,
          total: item.target_count,
          createdAt: item.created_at,
        })),
        ...operations.map((item): TaskSpaceTask => ({
          id: item.id,
          kind: 'operation',
          group: item.kind === 'oidc' ? 'auths' : 'operation',
          opKind: item.kind,
          label: operationLabel(item.kind),
          status: item.status,
          completed: item.completed,
          total: item.total,
          createdAt: item.created_at,
        })),
      ]
        .filter((item) => WORKSPACE_STATUSES.has(item.status))
        .sort((left, right) => (STATUS_ORDER[left.status] ?? 9) - (STATUS_ORDER[right.status] ?? 9) || right.createdAt.localeCompare(left.createdAt))
    },
    refetchInterval: 2000,
  })
  const tasks = query.data ?? []
  const queuedCount = tasks.filter((task) => task.status === 'queued').length
  const runningCount = tasks.filter((task) => ['running', 'stopping', 'pausing'].includes(task.status)).length
  const waitingCount = tasks.filter((task) => task.status === 'waiting').length
  const registrationWaitingCount = tasks.filter((task) => task.kind === 'batch' && task.status === 'waiting').length
  const pausedCount = tasks.filter((task) => task.status === 'paused').length
  const failedCount = tasks.filter((task) => FAILED_STATUSES.has(task.status)).length
  const activeCount = queuedCount + runningCount + waitingCount

  const createRetry = async (task: TaskSpaceTask) => {
    const endpoint = task.kind === 'batch'
      ? `/api/registration/batches/${task.id}/retry`
      : `/api/operations/${task.id}/retry`
    const retried = await api<{ id: string }>(endpoint, { method: 'POST' })
    if (task.kind === 'batch') localStorage.setItem('active-registration-batch', retried.id)
  }

  const retryWaiting = (task: TaskSpaceTask) => api<{ ok: boolean }>(`/api/operations/${task.id}/retry-waiting`, { method: 'POST' })

  const retry = async (task: TaskSpaceTask) => {
    if (retrying || controlling) return
    // Only failed tasks are retryable from the task space UI.
    if (!FAILED_STATUSES.has(task.status)) return
    setRetrying(task.id)
    try {
      await createRetry(task)
      await query.refetch()
      toast.success(task.status === 'waiting' ? '已解除限流等待，远端任务立即重试' : '重试任务已创建')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '任务重试失败')
    } finally {
      setRetrying(undefined)
    }
  }

  const retryAll = async () => {
    if (retrying || controlling) return
    const retryable = tasks.filter((task) => FAILED_STATUSES.has(task.status))
    if (!retryable.length) return
    setRetrying('all')
    let succeeded = 0
    let failed = 0
    try {
      const failedOperations = retryable.filter((task) => task.kind === 'operation')
      const failedBatches = retryable.filter((task) => task.kind === 'batch')
      if (failedOperations.length) {
        try {
          const result = await api<{ total: number; started: number; marked: number; failed: number }>(
            '/api/operations/retry-failed',
            { method: 'POST' },
          )
          // All failed operation cards should leave the workspace (status -> retried).
          succeeded += Math.max(result.marked || failedOperations.length, 0)
          failed += Math.max(result.failed || 0, 0)
        } catch {
          // Fallback: still try one by one so partial progress is possible.
          for (const task of failedOperations) {
            try {
              await createRetry(task)
              succeeded += 1
            } catch {
              failed += 1
            }
          }
        }
      }
      for (const task of failedBatches) {
        try {
          await createRetry(task)
          succeeded += 1
        } catch {
          failed += 1
        }
      }
      await query.refetch()
    } finally {
      setRetrying(undefined)
    }
    if (failed) toast.warning(`批量重试完成：状态已更新 ${succeeded}，仍有 ${failed} 个未成功重排队`)
    else toast.success(`已重试并更新 ${succeeded} 个失败任务状态`)
  }

  const controlTask = async (task: TaskSpaceTask, action: 'pause' | 'resume' | 'stop') => {
    const base = task.kind === 'batch' ? '/api/registration/batches' : '/api/operations'
    // operations use /stop as well? check - operations may only have pause/resume/stop
    await api(`${base}/${task.id}/${action}`, { method: 'POST' })
  }

  const control = async (task: TaskSpaceTask, action: 'pause' | 'resume' | 'stop') => {
    if (controlling || retrying) return
    setControlling(task.id)
    try {
      await controlTask(task, action)
      await query.refetch()
      toast.success(action === 'pause' ? '已请求暂停任务' : action === 'stop' ? '已请求结束任务' : '任务已继续')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '任务控制失败')
    } finally {
      setControlling(undefined)
    }
  }

  const pauseAll = async () => {
    if (controlling || retrying) return
    const pausable = tasks.filter((task) => ['queued', 'running'].includes(task.status) || (task.kind === 'batch' && task.status === 'waiting'))
    if (!pausable.length) return
    setControlling('all')
    let succeeded = 0
    let failed = 0
    try {
      for (const task of pausable) {
        try {
          await controlTask(task, 'pause')
          succeeded += 1
        } catch {
          failed += 1
        }
      }
      await query.refetch()
    } finally {
      setControlling(undefined)
    }
    if (failed) toast.warning(`批量暂停完成：成功 ${succeeded}，失败 ${failed}`)
    else toast.success(`已依次暂停 ${succeeded} 个任务`)
  }

  const resumeAll = async () => {
    if (controlling || retrying) return
    // Registration batches: serial resume (backend queues the rest as waiting).
    // Operations: keep previous parallel resume behavior.
    const pausedBatches = tasks.filter((task) => task.kind === 'batch' && task.status === 'paused')
    const pausedOps = tasks.filter((task) => task.kind === 'operation' && task.status === 'paused')
    if (!pausedBatches.length && !pausedOps.length) return
    const orderedBatches = [...pausedBatches].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    setControlling('all')
    let succeeded = 0
    let failed = 0
    try {
      for (const task of orderedBatches) {
        try {
          await controlTask(task, 'resume')
          succeeded += 1
        } catch {
          failed += 1
        }
      }
      for (const task of pausedOps) {
        try {
          await controlTask(task, 'resume')
          succeeded += 1
        } catch {
          failed += 1
        }
      }
      await query.refetch()
    } finally {
      setControlling(undefined)
    }
    if (failed) toast.warning(`批量启动完成：成功 ${succeeded}，失败 ${failed}`)
    else if (orderedBatches.length > 1) toast.success('已启动注册任务：同时仅运行 1 个，其余排队等待')
    else toast.success(`已启动 ${succeeded} 个暂停任务`)
  }


  const endAll = async () => {
    if (controlling || retrying) return
    // End only registration batches that are active/paused/waiting
    const endable = tasks.filter((task) => task.kind === 'batch' && ['queued', 'running', 'stopping', 'pausing', 'paused', 'waiting', 'interrupted'].includes(task.status))
    if (!endable.length) return
    if (!window.confirm(`确认结束 ${endable.length} 个注册批次？未完成账号将取消。`)) return
    setControlling('all')
    let succeeded = 0
    let failed = 0
    try {
      for (const task of endable) {
        try {
          await controlTask(task, 'stop')
          succeeded += 1
        } catch {
          failed += 1
        }
      }
      await query.refetch()
    } finally {
      setControlling(undefined)
    }
    if (failed) toast.warning(`批量结束完成：成功 ${succeeded}，失败 ${failed}`)
    else toast.success(`已结束 ${succeeded} 个注册批次`)
  }

  useEffect(() => {
    if (!open) return
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', closeOutside)
    return () => document.removeEventListener('mousedown', closeOutside)
  }, [open])

  return <section
    ref={rootRef}
    className={`fixed bottom-3 right-3 z-40 origin-bottom-right border shadow-2xl will-change-[width,height,border-radius] transition-[width,height,border-radius,background-color,border-color] sm:bottom-6 sm:right-4 ${open
      ? 'h-[calc(100dvh-1.5rem)] w-[calc(100vw-1.5rem)] overflow-hidden rounded-xl border-[var(--border)] bg-[var(--panel)] duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)] sm:h-[min(540px,calc(100vh-7rem))] sm:w-[min(560px,calc(100vw-2rem))]'
      : 'h-12 w-12 overflow-visible rounded-[24px] border-transparent bg-neutral-900 duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] dark:bg-white'
    }`}
    aria-label="全局任务状态"
  >
    <div
      className={`flex h-full min-h-0 w-full flex-col transition-[opacity,transform] ${open
        ? 'pointer-events-auto scale-100 opacity-100 delay-100 duration-200'
        : 'pointer-events-none scale-[0.98] opacity-0 duration-75'
      }`}
      aria-hidden={!open}
      inert={!open}
    >
      <div className="relative shrink-0 border-b px-3 py-3 pr-14 sm:px-4 sm:pr-16">
        <button
          type="button"
          className="absolute right-2 top-2 flex size-10 items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--panel)] shadow-sm transition hover:bg-[var(--soft)] sm:right-3 sm:top-3 sm:size-11"
          onClick={() => setOpen(false)}
          aria-label="关闭任务空间"
          title="关闭"
        ><X size={22} strokeWidth={2.4} /></button>
        <div className="min-w-0 pr-1">
          <h2 className="text-sm font-medium">任务空间</h2>
          <div className="muted mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] sm:text-[11px]">
            <span>待执行 {queuedCount}</span>
            <span>执行中 {runningCount}</span>
            <span className={waitingCount ? 'text-sky-600 dark:text-sky-400' : ''}>等待中 {waitingCount}</span>
            <span>已暂停 {pausedCount}</span>
            <span className={failedCount ? 'text-red-600 dark:text-red-400' : ''}>失败 {failedCount}</span>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <button type="button" style={{ fontSize: 9 }} className="inline-flex h-6 items-center justify-center gap-0.5 rounded-md border bg-[var(--panel)] px-1.5 font-medium leading-none transition hover:bg-[var(--soft)] disabled:pointer-events-none disabled:opacity-45" disabled={!tasks.some((task) => ['queued', 'running'].includes(task.status) || (task.kind === 'batch' && task.status === 'waiting')) || Boolean(controlling) || Boolean(retrying)} onClick={() => void pauseAll()}><Pause size={10} />暂停</button>
          <button type="button" style={{ fontSize: 9 }} className="inline-flex h-6 items-center justify-center gap-0.5 rounded-md border bg-[var(--panel)] px-1.5 font-medium leading-none transition hover:bg-[var(--soft)] disabled:pointer-events-none disabled:opacity-45" disabled={!pausedCount || Boolean(controlling) || Boolean(retrying)} onClick={() => void resumeAll()}><Play size={10} />启动</button>
          <button type="button" style={{ fontSize: 9 }} className="inline-flex h-6 items-center justify-center gap-0.5 rounded-md border bg-[var(--panel)] px-1.5 font-medium leading-none transition hover:bg-[var(--soft)] disabled:pointer-events-none disabled:opacity-45" disabled={!tasks.some((task) => task.kind === 'batch' && ['queued','running','stopping','pausing','paused','waiting','interrupted'].includes(task.status)) || Boolean(controlling) || Boolean(retrying)} onClick={() => void endAll()}><CircleStop size={10} />结束</button>
          <button type="button" style={{ fontSize: 9 }} className="inline-flex h-6 items-center justify-center gap-0.5 rounded-md border bg-[var(--panel)] px-1.5 font-medium leading-none transition hover:bg-[var(--soft)] disabled:pointer-events-none disabled:opacity-45" disabled={!failedCount || Boolean(retrying) || Boolean(controlling)} onClick={() => void retryAll()}><RotateCcw className={retrying === 'all' ? 'animate-spin' : ''} size={10} />重试</button>
        </div>
      </div>
      <div className="scrollbar min-h-0 flex-1 overflow-auto">
        {tasks.length ? (
          <div className="space-y-3 p-2 sm:p-3">
            {([
              { group: 'registration' as const, items: tasks.filter((task) => task.group === 'registration') },
              { group: 'operation' as const, items: tasks.filter((task) => task.group === 'operation') },
              { group: 'auths' as const, items: tasks.filter((task) => task.group === 'auths') },
            ]).filter((section) => section.items.length > 0).map((section) => (
              <section key={section.group} className="overflow-hidden rounded-xl border">
                <div className={`flex items-center justify-between gap-2 border-b px-3 py-2 ${GROUP_META[section.group].className}`}>
                  <h3 className="text-xs font-semibold">{GROUP_META[section.group].label}</h3>
                  <span className="text-[10px] opacity-80">{section.items.length}</span>
                </div>
                <div className="divide-y bg-[var(--panel)]">
                  {section.items.map((task) => {
                    const progress = Math.round((task.completed / Math.max(1, task.total)) * 100)
                    const active = ACTIVE_STATUSES.has(task.status)
                    const waiting = WAITING_STATUSES.has(task.status)
                    const failed = FAILED_STATUSES.has(task.status)
                    const isReg = task.group === 'registration'
                    const pausable = isReg ? regCanPause(task.status) : opCanPause(task.status)
                    const resumable = isReg ? regCanStart(task.status) : opCanStart(task.status)
                    const endable = isReg ? regCanEnd(task.status) : opCanEnd(task.status)
                    const retryable = isReg ? regCanRetry(task.status) : opCanRetry(task.status)
                    return (
                      <div key={task.id} className="px-3 py-3 sm:px-4 sm:py-2.5">
                        <div className="grid gap-2 sm:flex sm:items-center sm:gap-3">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-xs font-medium">{task.label}</div>
                            <div className="muted mt-1 truncate font-mono text-[10px]">{task.id}</div>
                          </div>
                          <div className="flex min-w-0 items-center justify-between gap-2 sm:contents">
                            <span className="muted shrink-0 text-[10px] sm:text-[11px]">{task.completed}/{task.total} · {progress}%</span>
                            <span className="ml-auto sm:ml-0"><Badge value={task.status} /></span>
                            {pausable && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(controlling) || Boolean(retrying)} onClick={() => void control(task, 'pause')} aria-label={`暂停${task.label}`} title="暂停"><Pause size={13} /></button>}
                            {resumable && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(controlling) || Boolean(retrying)} onClick={() => void control(task, 'resume')} aria-label={`启动${task.label}`} title="启动"><Play size={13} /></button>}
                            {endable && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(controlling) || Boolean(retrying)} onClick={() => void control(task, 'stop')} aria-label={`结束${task.label}`} title="结束"><CircleStop size={13} /></button>}
                            {retryable && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(retrying) || Boolean(controlling)} onClick={() => void retry(task)} aria-label={`重试${task.label}`} title="重试"><RotateCcw className={retrying === task.id ? 'animate-spin' : ''} size={13} /></button>}
                          </div>
                        </div>
                        <div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--soft)] sm:mt-1.5"><div className={`h-full rounded-full transition-all ${waiting ? 'bg-sky-500' : active ? 'bg-amber-500' : resumable ? 'bg-sky-500' : failed ? 'bg-red-500' : 'bg-neutral-400'}`} style={{ width: `${progress}%` }} /></div>
                      </div>
                    )
                  })}
                </div>
              </section>
            ))}
          </div>
        ) : <div className="muted flex h-full min-h-40 items-center justify-center px-4 text-center text-sm">当前没有待执行、等待中、执行中或失败的任务</div>}
      </div>
    </div>

    <button
      type="button"
      className={`absolute inset-0 flex items-center justify-center text-white transition-[opacity,transform] dark:text-black ${open
        ? 'pointer-events-none scale-50 opacity-0 duration-100'
        : 'pointer-events-auto scale-100 opacity-100 delay-150 duration-150'
      }`}
      onClick={() => setOpen(true)}
      aria-label="打开全局任务状态"
      aria-expanded={open}
      tabIndex={open ? -1 : 0}
    >
      <Activity size={20} />
      {activeCount > 0 && <span className="absolute -right-1 -top-1 flex min-w-5 items-center justify-center rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">{activeCount}</span>}
    </button>
  </section>
}