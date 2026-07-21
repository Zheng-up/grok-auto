import { useQuery } from '@tanstack/react-query'
import { Activity, Pause, Play, RotateCcw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { operationLabel } from '../lib/labels'
import type { Batch, Operation } from '../lib/types'
import { Badge } from '../components/ui'

const ACTIVE_STATUSES = new Set(['queued', 'running', 'stopping', 'pausing'])
const PAUSED_STATUSES = new Set(['paused'])
const FAILED_STATUSES = new Set(['failed', 'partial', 'interrupted'])
const WORKSPACE_STATUSES = new Set([...ACTIVE_STATUSES, ...PAUSED_STATUSES, ...FAILED_STATUSES])
const STATUS_ORDER: Record<string, number> = { running: 0, stopping: 1, pausing: 1, queued: 2, paused: 3, failed: 4, partial: 4, interrupted: 4 }

type TaskSpaceTask = {
  id: string
  kind: 'batch' | 'operation'
  label: string
  status: string
  completed: number
  total: number
  createdAt: string
}

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
        ...batches.map((item): TaskSpaceTask => ({ id: item.id, kind: 'batch', label: '注册批次', status: item.status, completed: item.completed, total: item.target_count, createdAt: item.created_at })),
        ...operations.map((item): TaskSpaceTask => ({ id: item.id, kind: 'operation', label: operationLabel(item.kind), status: item.status, completed: item.completed, total: item.total, createdAt: item.created_at })),
      ]
        .filter((item) => WORKSPACE_STATUSES.has(item.status))
        .sort((left, right) => (STATUS_ORDER[left.status] ?? 9) - (STATUS_ORDER[right.status] ?? 9) || right.createdAt.localeCompare(left.createdAt))
    },
    refetchInterval: 2000,
  })
  const tasks = query.data ?? []
  const queuedCount = tasks.filter((task) => task.status === 'queued').length
  const runningCount = tasks.filter((task) => ['running', 'stopping', 'pausing'].includes(task.status)).length
  const pausedCount = tasks.filter((task) => task.status === 'paused').length
  const failedCount = tasks.filter((task) => FAILED_STATUSES.has(task.status)).length
  const activeCount = queuedCount + runningCount

  const createRetry = async (task: TaskSpaceTask) => {
    const endpoint = task.kind === 'batch'
      ? `/api/registration/batches/${task.id}/retry`
      : `/api/operations/${task.id}/retry`
    const retried = await api<{ id: string }>(endpoint, { method: 'POST' })
    if (task.kind === 'batch') localStorage.setItem('active-registration-batch', retried.id)
  }

  const retry = async (task: TaskSpaceTask) => {
    if (retrying || controlling) return
    setRetrying(task.id)
    try {
      await createRetry(task)
      await query.refetch()
      toast.success('重试任务已创建')
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
      for (const task of retryable) {
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
    if (failed) toast.warning(`批量重试完成：成功 ${succeeded}，失败 ${failed}`)
    else toast.success(`已依次重试 ${succeeded} 个任务`)
  }

  const controlTask = async (task: TaskSpaceTask, action: 'pause' | 'resume') => {
    const base = task.kind === 'batch' ? '/api/registration/batches' : '/api/operations'
    await api(`${base}/${task.id}/${action}`, { method: 'POST' })
  }

  const control = async (task: TaskSpaceTask, action: 'pause' | 'resume') => {
    if (controlling || retrying) return
    setControlling(task.id)
    try {
      await controlTask(task, action)
      await query.refetch()
      toast.success(action === 'pause' ? '已请求暂停任务' : '任务已继续')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '任务控制失败')
    } finally {
      setControlling(undefined)
    }
  }

  const pauseAll = async () => {
    if (controlling || retrying) return
    const pausable = tasks.filter((task) => ['queued', 'running'].includes(task.status))
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
    className={`fixed bottom-6 right-3 z-40 origin-bottom-right border shadow-2xl will-change-[width,height,border-radius] transition-[width,height,border-radius,background-color,border-color] sm:right-4 ${open
      ? 'h-[min(540px,calc(100vh-7rem))] w-[min(560px,calc(100vw-2rem))] overflow-hidden rounded-xl border-[var(--border)] bg-[var(--panel)] duration-[450ms] ease-[cubic-bezier(0.16,1,0.3,1)]'
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
      <div className="flex shrink-0 items-center justify-between border-b px-4 py-3"><div><h2 className="text-sm font-medium">任务空间</h2><div className="muted mt-1 flex items-center gap-3 text-[11px]"><span>待执行 {queuedCount}</span><span>执行中 {runningCount}</span><span>已暂停 {pausedCount}</span><span className={failedCount ? 'text-red-600 dark:text-red-400' : ''}>失败 {failedCount}</span></div></div><div className="flex items-center gap-2"><button type="button" className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-[var(--panel)] px-2.5 text-xs font-medium transition hover:bg-[var(--soft)] disabled:pointer-events-none disabled:opacity-45" disabled={!tasks.some((task) => ['queued', 'running'].includes(task.status)) || Boolean(controlling) || Boolean(retrying)} onClick={() => void pauseAll()}><Pause size={13} />全部暂停</button><button type="button" className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-[var(--panel)] px-2.5 text-xs font-medium transition hover:bg-[var(--soft)] disabled:pointer-events-none disabled:opacity-45" disabled={!failedCount || Boolean(retrying) || Boolean(controlling)} onClick={() => void retryAll()}><RotateCcw className={retrying === 'all' ? 'animate-spin' : ''} size={13} />全部重试</button><button type="button" className="rounded-md p-1.5 hover:bg-[var(--soft)]" onClick={() => setOpen(false)} aria-label="关闭"><X size={16} /></button></div></div>
      <div className="scrollbar min-h-0 flex-1 overflow-auto">
        {tasks.length ? <div className="divide-y">{tasks.map((task) => {
          const progress = Math.round((task.completed / Math.max(1, task.total)) * 100)
          const active = ACTIVE_STATUSES.has(task.status)
          const failed = FAILED_STATUSES.has(task.status)
          const pausable = ['queued', 'running'].includes(task.status)
          const resumable = task.status === 'paused'
          return <div key={task.id} className="px-4 py-2.5">
            <div className="flex items-center gap-3"><span className="w-20 shrink-0 text-xs font-medium">{task.label}</span><span className="muted min-w-0 flex-1 truncate font-mono text-[10px]">{task.id}</span><span className="muted shrink-0 text-[11px]">{task.completed}/{task.total} · {progress}%</span><Badge value={task.status} />{pausable && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(controlling) || Boolean(retrying)} onClick={() => void control(task, 'pause')} aria-label={`暂停${task.label}`} title="暂停"><Pause size={13} /></button>}{resumable && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(controlling) || Boolean(retrying)} onClick={() => void control(task, 'resume')} aria-label={`继续${task.label}`} title="继续"><Play size={13} /></button>}{failed && <button type="button" className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border bg-[var(--panel)] transition hover:bg-[var(--soft)] disabled:opacity-50" disabled={Boolean(retrying) || Boolean(controlling)} onClick={() => void retry(task)} aria-label={`重试${task.label}`} title="重试"><RotateCcw className={retrying === task.id ? 'animate-spin' : ''} size={13} /></button>}</div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--soft)]"><div className={`h-full rounded-full transition-all ${active ? 'bg-amber-500' : resumable ? 'bg-sky-500' : 'bg-red-500'}`} style={{ width: `${progress}%` }} /></div>
          </div>
        })}</div> : <div className="muted flex h-full min-h-40 items-center justify-center px-4 text-center text-sm">当前没有待执行、执行中或失败的任务</div>}
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