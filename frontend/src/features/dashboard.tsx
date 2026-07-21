import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowRight, CheckCircle2, KeyRound, Play, Users, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { operationLabel } from '../lib/labels'
import type { Dashboard } from '../lib/types'
import { Badge, Card, Empty, PageHeader, Spinner } from '../components/ui'

const durationLabel = (seconds: number) => {
  const safe = Math.max(0, Math.round(seconds || 0))
  const hours = Math.floor(safe / 3600)
  const minutes = Math.floor((safe % 3600) / 60)
  const rest = safe % 60
  if (hours) return `${hours}时 ${minutes}分 ${rest}秒`
  if (minutes) return `${minutes}分 ${rest}秒`
  return `${rest}秒`
}

export function DashboardPage() {
  const query = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/api/dashboard'), refetchInterval: 5000 })
  if (!query.data) return <Spinner />
  const data = query.data
  const activeTasks = data.active.active_batches + data.active.active_operations
  const todayRate = data.today.total ? Math.round((data.today.success / data.today.total) * 100) : 0
  const availability = [
    { label: 'auths 可用', ready: data.accounts.oidc_ready, tone: 'bg-sky-500' },
    { label: 'SSO 已入池', ready: data.accounts.remote_web_ready, tone: 'bg-emerald-500' },
    { label: 'Build 已入池', ready: data.accounts.remote_build_ready, tone: 'bg-violet-500' },
    { label: 'Console 已入池', ready: data.accounts.remote_console_ready, tone: 'bg-amber-500' },
  ]
  const recentTasks = [
    ...data.recent_batches.map((batch) => ({ id: batch.id, label: '注册批次', status: batch.status, completed: batch.completed, total: batch.target_count, createdAt: batch.created_at })),
    ...data.recent_operations.map((operation) => ({ id: operation.id, label: operationLabel(operation.kind), status: operation.status, completed: operation.completed, total: operation.total, createdAt: operation.created_at })),
  ].sort((left, right) => right.createdAt.localeCompare(left.createdAt)).slice(0, 6)

  return <>
    <PageHeader title="仪表盘" actions={<>
      <Link to="/accounts" className="inline-flex h-10 w-28 items-center justify-center rounded-lg border bg-[var(--panel)] text-sm font-medium transition hover:bg-[var(--soft)]">账号管理</Link>
      <Link to="/register" className="inline-flex h-10 w-28 items-center justify-center gap-2 rounded-lg bg-neutral-900 text-sm font-medium text-white transition hover:bg-neutral-700 dark:bg-white dark:text-black"><Play size={15} />开始注册</Link>
    </>} />

    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Stat icon={Users} label="账号总数" value={data.accounts.total} detail="本地已保存账号" />
      <Stat icon={CheckCircle2} label="今日成功" value={data.today.success} detail={`今日共处理 ${data.today.total}`} tone="success" />
      <Stat icon={KeyRound} label="auths 可用" value={data.accounts.oidc_ready} detail={`未生成 ${Math.max(0, data.accounts.total - data.accounts.oidc_ready)}`} tone="info" />
      <Stat icon={Activity} label="运行中任务" value={activeTasks} detail={`注册 ${data.active.active_batches} · 账号操作 ${data.active.active_operations}`} tone={activeTasks ? 'warning' : undefined} />
    </div>

    <div className="mt-5 grid gap-5 xl:grid-cols-[0.8fr_1fr_1.35fr]">
      <Card className="min-h-[390px] overflow-hidden">
        <div className="border-b px-5 py-4"><h2 className="font-medium">今日注册结果</h2></div>
        <div className="p-5">
          <div className="flex items-end justify-between"><div><div className="text-4xl font-semibold tracking-tight">{todayRate}%</div><div className="muted mt-1 text-xs">今日成功率</div></div><div className="text-right text-xs"><div className="text-emerald-600 dark:text-emerald-400">成功 {data.today.success}</div><div className="mt-1 text-red-600 dark:text-red-400">失败 {data.today.failed}</div></div></div>
          <div className="mt-5 h-2 overflow-hidden rounded-full bg-[var(--soft)]"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${todayRate}%` }} /></div>
          <div className="mt-6 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-emerald-50 p-3 dark:bg-emerald-950"><CheckCircle2 size={16} className="text-emerald-600 dark:text-emerald-400" /><div className="mt-2 text-2xl font-semibold">{data.today.success}</div><div className="muted text-xs">注册成功</div></div>
            <div className="rounded-xl bg-red-50 p-3 dark:bg-red-950"><XCircle size={16} className="text-red-600 dark:text-red-400" /><div className="mt-2 text-2xl font-semibold">{data.today.failed}</div><div className="muted text-xs">注册失败</div></div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 border-t pt-4">
            <div><div className="muted text-xs">单账号平均耗时</div><div className="mt-1 font-semibold text-sky-600 dark:text-sky-400">{durationLabel(data.today.average_seconds)}</div></div>
            <div><div className="muted text-xs">今日累计耗时</div><div className="mt-1 font-semibold text-violet-600 dark:text-violet-400">{durationLabel(data.today.total_seconds)}</div></div>
          </div>
          <Link to="/register" className="mt-4 flex items-center justify-center gap-1 rounded-lg border py-2.5 text-sm font-medium hover:bg-[var(--soft)]">进入注册页面<ArrowRight size={14} /></Link>
        </div>
      </Card>

      <Card className="min-h-[390px] overflow-hidden">
        <div className="flex items-center justify-between border-b px-5 py-4"><h2 className="font-medium">账号就绪度</h2><Link to="/accounts" className="muted text-xs hover:text-[var(--strong)]">账号管理</Link></div>
        <div className="divide-y">{availability.map((item) => {
          const rate = data.accounts.total ? Math.round((item.ready / data.accounts.total) * 100) : 0
          return <div key={item.label} className="px-5 py-4"><div className="flex items-center justify-between text-sm"><span className="font-medium">{item.label}</span><span><strong>{item.ready}</strong><span className="muted"> / {data.accounts.total}</span></span></div><div className="mt-2.5 flex items-center gap-3"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--soft)]"><div className={`h-full rounded-full ${item.tone}`} style={{ width: `${rate}%` }} /></div><span className="muted w-9 text-right text-xs">{rate}%</span></div></div>
        })}</div>
      </Card>

      <Card className="min-h-[390px] overflow-hidden">
        <div className="flex items-center justify-between border-b px-5 py-4"><h2 className="font-medium">最近任务</h2><Link to="/tasks" className="muted inline-flex items-center gap-1 text-xs hover:text-[var(--strong)]">查看全部<ArrowRight size={13} /></Link></div>
        {recentTasks.length ? <div className="divide-y">{recentTasks.map((task) => {
          const progress = Math.round((task.completed / Math.max(1, task.total)) * 100)
          return <div key={task.id} className="flex items-center gap-3 px-5 py-3"><span className={`size-2 shrink-0 rounded-full ${['queued', 'running', 'stopping', 'pausing'].includes(task.status) ? 'bg-amber-500' : task.status === 'paused' ? 'bg-sky-500' : task.status === 'failed' ? 'bg-red-500' : 'bg-emerald-500'}`} /><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="text-sm font-medium">{task.label}</span><span className="muted truncate font-mono text-[10px]">{task.id}</span></div><div className="muted mt-1 text-xs">{task.completed}/{task.total} · {progress}%</div></div><Badge value={task.status} /></div>
        })}</div> : <Empty text="暂无任务" />}
      </Card>
    </div>
  </>
}

function Stat({ icon: Icon, label, value, detail, tone }: { icon: typeof Users; label: string; value: number; detail: string; tone?: 'success' | 'info' | 'warning' }) {
  const iconTone = tone === 'success' ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400' : tone === 'info' ? 'bg-sky-50 text-sky-600 dark:bg-sky-950 dark:text-sky-400' : tone === 'warning' ? 'bg-amber-50 text-amber-600 dark:bg-amber-950 dark:text-amber-400' : 'bg-[var(--soft)] muted'
  return <Card className="p-4"><div className="flex items-center justify-between"><span className="muted text-sm">{label}</span><span className={`flex size-8 items-center justify-center rounded-lg ${iconTone}`}><Icon size={16} /></span></div><div className="mt-3 text-3xl font-semibold tracking-tight">{value}</div><div className="muted mt-1 text-xs">{detail}</div></Card>
}