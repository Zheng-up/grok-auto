import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, CloudUpload, Download, KeyRound, ListFilter, RefreshCw, Search, Server, Trash2, Users, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, download } from '../lib/api'
import type { Account, Dashboard, Operation } from '../lib/types'
import { authStatusLabel, operationLabel, remoteStatusLabel, formatDbTime } from '../lib/labels'
import { Badge, Button, Card, Empty, Input, PageHeader, PaginationBar, Spinner } from '../components/ui'

type RemoteKind = 'remote_web' | 'remote_cpa' | 'remote_console'
type ImportRange = 'missing' | 'all'
type AccountFilters = {
  register_status: string
  oidc_status: string
  remote_web_status: string
  remote_build_status: string
  remote_console_status: string
}

const EMPTY_FILTERS: AccountFilters = {
  register_status: '',
  oidc_status: '',
  remote_web_status: '',
  remote_build_status: '',
  remote_console_status: '',
}
type FilterField = {
  key: keyof AccountFilters
  label: string
  values: Array<{ value: string; label: string }>
}
const FILTER_FIELDS: FilterField[] = [
  { key: 'register_status', label: '注册状态', values: [{ value: 'success', label: '成功' }, { value: 'failed', label: '失败' }] },
  { key: 'oidc_status', label: 'auths 状态', values: [{ value: 'pending', label: '未生成' }, { value: 'success', label: '成功' }, { value: 'failed', label: '失败' }] },
  { key: 'remote_web_status', label: 'SSO 入池', values: [{ value: 'not_pushed', label: '未入池' }, { value: 'success', label: '已入池' }, { value: 'failed', label: '入池失败' }] },
  { key: 'remote_build_status', label: 'Build 入池', values: [{ value: 'not_pushed', label: '未入池' }, { value: 'success', label: '已入池' }, { value: 'failed', label: '入池失败' }] },
  { key: 'remote_console_status', label: 'Console 入池', values: [{ value: 'not_pushed', label: '未入池' }, { value: 'success', label: '已入池' }, { value: 'failed', label: '入池失败' }] },
]

const accountSearchParams = (query: string, filters: AccountFilters) => {
  const params = new URLSearchParams()
  if (query) params.set('q', query)
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value)
  }
  return params
}

const PAGE_SIZES = [20, 50, 100, 500, 1000, 2000] as const
const REMOTE_OPTIONS: Array<{ kind: RemoteKind; label: string }> = [
  { kind: 'remote_web', label: 'SSO' },
  { kind: 'remote_cpa', label: 'Build' },
  { kind: 'remote_console', label: 'Console' },
]

const remoteStatus = (account: Account, kind: RemoteKind) => {
  if (kind === 'remote_web') return account.remote_web_status
  if (kind === 'remote_cpa') return account.remote_build_status
  return account.remote_console_status
}

const remoteRunning = (account: Account, kind: RemoteKind) => {
  if (remoteStatus(account, kind) === 'running') return true
  if (kind === 'remote_web') return account.active_operations?.some((active) => ['remote_sso', 'remote_web'].includes(active)) ?? false
  return account.active_operations?.includes(kind) ?? false
}

const statusTone = (value: string) => {
  const normalized = value.toLowerCase()
  if (normalized === 'success') return 'bg-emerald-500'
  if (['failed', 'error'].includes(normalized)) return 'bg-red-500'
  if (['running', 'queued', 'stopping'].includes(normalized)) return 'bg-amber-400'
  if (normalized === 'waiting') return 'bg-sky-500'
  return 'bg-neutral-400'
}

function RemoteStatuses({ account }: { account: Account }) {
  const statuses = [
    { label: 'SSO', value: account.remote_web_status },
    { label: 'Build', value: account.remote_build_status },
    { label: 'Console', value: account.remote_console_status },
  ]
  return <div className="flex items-center gap-4 whitespace-nowrap">
    {statuses.map((item) => <span key={item.label} className="inline-flex items-center gap-1.5 text-xs" title={`${item.label}：${remoteStatusLabel(item.value)}`}>
      <span className="muted">{item.label}</span><span className={`size-2.5 rounded-full ring-2 ring-[var(--panel)] ${statusTone(item.value)}`} aria-label={remoteStatusLabel(item.value)} />
    </span>)}
  </div>
}

function AuthStatus({ value }: { value: string }) {
  return <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs"><span className={`size-2 rounded-full ${statusTone(value)}`} />{authStatusLabel(value)}</span>
}

function AccountFilterPanel({
  filters,
  onChange,
  onClear,
  onClose,
}: {
  filters: AccountFilters
  onChange: (key: keyof AccountFilters, value: string) => void
  onClear: () => void
  onClose: () => void
}) {
  const [activeKey, setActiveKey] = useState<keyof AccountFilters | null>(null)
  const activeField = FILTER_FIELDS.find((field) => field.key === activeKey)
  const selectedOption = (field: FilterField) => field.values.find((option) => option.value === filters[field.key])
  const optionTone = (value: string) => {
    if (value === 'success') return 'text-emerald-600 dark:text-emerald-400'
    if (value === 'failed') return 'text-red-600 dark:text-red-400'
    if (value === 'not_pushed' || value === 'pending') return 'text-neutral-500'
    return 'text-sky-600 dark:text-sky-400'
  }

  return <div className={`surface absolute right-0 top-12 z-30 grid w-[min(calc(100vw-2.5rem),24rem)] ${activeField ? 'grid-cols-2' : 'grid-cols-1'} overflow-hidden p-1 shadow-xl sm:left-0 sm:right-auto sm:block sm:w-52 sm:overflow-visible`} onMouseLeave={() => setActiveKey(null)}>
    <div className="py-1">
      {FILTER_FIELDS.map((field) => {
        const selected = selectedOption(field)
        return <button
          type="button"
          key={field.key}
          className={`flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm transition ${activeKey === field.key ? 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300' : 'hover:bg-[var(--soft)]'}`}
          onMouseEnter={() => setActiveKey(field.key)}
          onFocus={() => setActiveKey(field.key)}
          onClick={() => setActiveKey(field.key)}
        ><span>{field.label}</span><span className="flex items-center gap-1">{selected && <span className={`max-w-16 truncate text-xs ${optionTone(selected.value)}`}>{selected.label}</span>}<ChevronRight size={14} className="muted" /></span></button>
      })}
      <div className="mt-1 border-t px-2 pt-2"><button type="button" className="muted w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-[var(--soft)] hover:text-[var(--strong)]" onClick={onClear}>清除全部筛选</button></div>
    </div>
    {activeField && <div className="surface min-w-0 border-l p-1 shadow-xl sm:absolute sm:left-[calc(100%-1px)] sm:top-0 sm:w-48 sm:border-l-0">
      {[{ value: '', label: '全部' }, ...activeField.values].map((option) => <button
        type="button"
        key={option.value || 'all'}
        className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition hover:bg-[var(--soft)] ${optionTone(option.value)}`}
        onClick={() => { onChange(activeField.key, option.value); onClose() }}
      ><span>{option.label}</span>{filters[activeField.key] === option.value && <span className="font-medium">✓</span>}</button>)}
    </div>}
  </div>
}

function RemoteImportDialog({
  accounts,
  pending,
  onClose,
  onSubmit,
}: {
  accounts: Account[]
  pending: boolean
  onClose: () => void
  onSubmit: (kind: RemoteKind, accountIds: string[]) => void
}) {
  const [kind, setKind] = useState<RemoteKind>('remote_web')
  const [range, setRange] = useState<ImportRange>('missing')
  const imported = accounts.filter((account) => remoteStatus(account, kind) === 'success').length
  const missing = accounts.length - imported
  const running = accounts.filter((account) => remoteRunning(account, kind)).length
  const unavailable = kind === 'remote_cpa' ? accounts.filter((account) => account.oidc_status !== 'success').length : 0
  const candidates = accounts.filter((account) => {
    if (remoteRunning(account, kind)) return false
    if (kind === 'remote_cpa' && account.oidc_status !== 'success') return false
    return range === 'all' || remoteStatus(account, kind) !== 'success'
  })
  const selectedLabel = REMOTE_OPTIONS.find((option) => option.kind === kind)?.label ?? ''

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4 backdrop-blur-[2px]" onMouseDown={(event) => {
    if (event.target === event.currentTarget && !pending) onClose()
  }}>
    <section role="dialog" aria-modal="true" aria-labelledby="remote-import-title" className="surface w-full max-w-lg p-5 shadow-2xl">
      <div className="flex items-start justify-between gap-4">
        <div><h2 id="remote-import-title" className="text-lg font-semibold">远端入池</h2><p className="muted mt-1 text-sm">已选择 {accounts.length} 个账号</p></div>
        <button type="button" className="rounded-md p-1.5 hover:bg-[var(--soft)]" onClick={onClose} disabled={pending} aria-label="关闭"><X size={17} /></button>
      </div>

      <div className="mt-5">
        <div className="mb-2 text-sm font-medium">入池类型</div>
        <div className="grid grid-cols-3 rounded-xl bg-[var(--soft)] p-1">
          {REMOTE_OPTIONS.map((option) => <button
            type="button"
            key={option.kind}
            className={`rounded-lg px-3 py-2 text-sm transition ${kind === option.kind ? 'bg-[var(--panel)] font-medium shadow-sm' : 'muted hover:text-[var(--strong)]'}`}
            onClick={() => setKind(option.kind)}
          >{option.label}</button>)}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg border p-3"><div className="text-xl font-semibold">{imported}</div><div className="muted mt-1 text-xs">已入池</div></div>
        <div className="rounded-lg border p-3"><div className="text-xl font-semibold">{missing}</div><div className="muted mt-1 text-xs">未入池</div></div>
        <div className="rounded-lg border p-3"><div className="text-xl font-semibold">{running}</div><div className="muted mt-1 text-xs">执行中</div></div>
      </div>

      <div className="mt-5">
        <div className="mb-2 text-sm font-medium">入池范围</div>
        <div className="grid grid-cols-2 rounded-xl bg-[var(--soft)] p-1">
          <button type="button" className={`rounded-lg px-3 py-2 text-sm transition ${range === 'missing' ? 'bg-[var(--panel)] font-medium shadow-sm' : 'muted hover:text-[var(--strong)]'}`} onClick={() => setRange('missing')}>仅入池缺失</button>
          <button type="button" className={`rounded-lg px-3 py-2 text-sm transition ${range === 'all' ? 'bg-[var(--panel)] font-medium shadow-sm' : 'muted hover:text-[var(--strong)]'}`} onClick={() => setRange('all')}>全部重新入池</button>
        </div>
        <p className="muted mt-2 text-xs">
          {range === 'missing' ? `仅提交尚未成功入池 ${selectedLabel} 的账号。` : `重新提交全部可用账号，已入池状态会在执行后刷新。`}
          {unavailable > 0 && kind === 'remote_cpa' ? ` 其中 ${unavailable} 个账号尚未生成 auths，将自动跳过。` : ''}
        </p>
      </div>

      <div className="mt-6 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose} disabled={pending}>取消</Button>
        <Button onClick={() => onSubmit(kind, candidates.map((account) => account.id))} disabled={pending || candidates.length === 0}>
          {pending ? '正在创建…' : `开始入池（${candidates.length}）`}
        </Button>
      </div>
    </section>
  </div>
}

function AuthsDialog({
  accounts,
  pending,
  onClose,
  onSubmit,
}: {
  accounts: Account[]
  pending: boolean
  onClose: () => void
  onSubmit: (accountIds: string[]) => void
}) {
  const [range, setRange] = useState<ImportRange>('missing')
  const ready = accounts.filter((account) => account.oidc_status === 'success').length
  const missing = accounts.length - ready
  const running = accounts.filter((account) => account.oidc_status === 'running' || account.active_operations?.includes('oidc')).length
  const candidates = accounts.filter((account) => {
    if (account.oidc_status === 'running' || account.active_operations?.includes('oidc')) return false
    return range === 'all' || account.oidc_status !== 'success'
  })

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4 backdrop-blur-[2px]" onMouseDown={(event) => {
    if (event.target === event.currentTarget && !pending) onClose()
  }}>
    <section role="dialog" aria-modal="true" aria-labelledby="auths-generate-title" className="surface w-full max-w-lg p-5 shadow-2xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 id="auths-generate-title" className="text-lg font-semibold">生成 auths</h2>
          <p className="muted mt-1 text-sm">已选择 {accounts.length} 个账号</p>
        </div>
        <button type="button" className="rounded-md p-1.5 hover:bg-[var(--soft)]" onClick={onClose} disabled={pending} aria-label="关闭"><X size={17} /></button>
      </div>

      <div className="mt-5 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg border p-3"><div className="text-xl font-semibold">{ready}</div><div className="muted mt-1 text-xs">已生成</div></div>
        <div className="rounded-lg border p-3"><div className="text-xl font-semibold">{missing}</div><div className="muted mt-1 text-xs">未生成/失败</div></div>
        <div className="rounded-lg border p-3"><div className="text-xl font-semibold">{running}</div><div className="muted mt-1 text-xs">执行中</div></div>
      </div>

      <div className="mt-5">
        <div className="mb-2 text-sm font-medium">生成范围</div>
        <div className="grid grid-cols-2 rounded-xl bg-[var(--soft)] p-1">
          <button type="button" className={`rounded-lg px-3 py-2 text-sm transition ${range === 'missing' ? 'bg-[var(--panel)] font-medium shadow-sm' : 'muted hover:text-[var(--strong)]'}`} onClick={() => setRange('missing')}>仅生成缺失</button>
          <button type="button" className={`rounded-lg px-3 py-2 text-sm transition ${range === 'all' ? 'bg-[var(--panel)] font-medium shadow-sm' : 'muted hover:text-[var(--strong)]'}`} onClick={() => setRange('all')}>全部重新生成</button>
        </div>
        <p className="muted mt-2 text-xs">
          {range === 'missing'
            ? '仅提交尚未成功生成 auths 的账号（包含未生成与失败）。'
            : '重新提交全部可用账号，已成功生成的账号也会重新生成。'}
          {running > 0 ? ` 当前有 ${running} 个账号正在执行，将自动跳过。` : ''}
        </p>
      </div>

      <div className="mt-6 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose} disabled={pending}>取消</Button>
        <Button onClick={() => onSubmit(candidates.map((account) => account.id))} disabled={pending || candidates.length === 0}>
          {pending ? '正在创建…' : `开始生成（${candidates.length}）`}
        </Button>
      </div>
    </section>
  </div>
}

function ExportDialog({ selectedCount, pending, onClose, onSubmit }: { selectedCount: number; pending: boolean; onClose: () => void; onSubmit: (kind: 'tokens' | 'cpa') => void }) {
  const [kind, setKind] = useState<'tokens' | 'cpa'>('tokens')
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4 backdrop-blur-[2px]" onMouseDown={(event) => {
    if (event.target === event.currentTarget && !pending) onClose()
  }}>
    <section role="dialog" aria-modal="true" aria-labelledby="export-title" className="surface w-full max-w-md p-5 shadow-2xl">
      <div className="flex items-start justify-between gap-4"><div><h2 id="export-title" className="text-lg font-semibold">导出账号</h2><p className="muted mt-1 text-sm">{selectedCount ? `导出已选择的 ${selectedCount} 个账号` : '未选择账号，将导出全部可用账号'}</p></div><button type="button" className="rounded-md p-1.5 hover:bg-[var(--soft)]" onClick={onClose} disabled={pending} aria-label="关闭"><X size={17} /></button></div>
      <div className="mt-5 grid grid-cols-2 gap-3">
        <button type="button" className={`rounded-xl border p-4 text-left transition ${kind === 'tokens' ? 'border-neutral-900 bg-[var(--soft)] dark:border-white' : 'hover:bg-[var(--soft)]'}`} onClick={() => setKind('tokens')}><div className="font-medium">SSO</div><div className="muted mt-1 text-xs">导出为 tokens.txt</div></button>
        <button type="button" className={`rounded-xl border p-4 text-left transition ${kind === 'cpa' ? 'border-neutral-900 bg-[var(--soft)] dark:border-white' : 'hover:bg-[var(--soft)]'}`} onClick={() => setKind('cpa')}><div className="font-medium">auths</div><div className="muted mt-1 text-xs">导出为 auths.zip</div></button>
      </div>
      <div className="mt-6 flex justify-end gap-2"><Button variant="ghost" onClick={onClose} disabled={pending}>取消</Button><Button onClick={() => onSubmit(kind)} disabled={pending}><Download size={15} />{pending ? '正在导出…' : '确认导出'}</Button></div>
    </section>
  </div>
}

export function AccountsPage() {
  const client = useQueryClient()
  const dashboard = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/api/dashboard'), refetchInterval: 5000 })
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [filters, setFilters] = useState<AccountFilters>(EMPTY_FILTERS)
  const [filterOpen, setFilterOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(20)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [authsDialogOpen, setAuthsDialogOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [exportPending, setExportPending] = useState(false)
  const filterRef = useRef<HTMLDivElement>(null)
  const offset = (page - 1) * pageSize
  const filterParams = accountSearchParams(query, filters)
  const listParams = new URLSearchParams(filterParams)
  listParams.set('limit', String(pageSize))
  listParams.set('offset', String(offset))
  const accounts = useQuery({
    queryKey: ['accounts', query, filters, page, pageSize],
    queryFn: () => api<Account[]>(`/api/accounts?${listParams.toString()}`),
    refetchInterval: 3000,
  })
  const count = useQuery({
    queryKey: ['accounts-count', query, filters],
    queryFn: () => api<{ total: number }>(`/api/accounts/count?${filterParams.toString()}`),
    refetchInterval: 3000,
  })
  const rows = useMemo(() => accounts.data ?? [], [accounts.data])
  const total = count.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.id))
  const ids = useMemo(() => rows.filter((row) => selected.has(row.id)).map((row) => row.id), [rows, selected])
  const selectedRows = rows.filter((row) => selected.has(row.id))
  const authsRunning = selectedRows.some((row) => row.oidc_status === 'running' || row.active_operations?.includes('oidc'))

  useEffect(() => {
    if (!filterOpen) return
    const closeOutside = (event: MouseEvent) => {
      if (!filterRef.current?.contains(event.target as Node)) setFilterOpen(false)
    }
    document.addEventListener('mousedown', closeOutside)
    return () => document.removeEventListener('mousedown', closeOutside)
  }, [filterOpen])

  const operation = useMutation({
    mutationFn: ({ kind, accountIds }: { kind: 'oidc' | RemoteKind; accountIds: string[] }) => api<Operation>('/api/accounts/operations', {
      method: 'POST',
      body: JSON.stringify({ kind, account_ids: accountIds }),
    }),
    onSuccess: (value) => {
      const label = operationLabel(value.kind)
      if (value.reused) toast.info(`${label}任务已在执行中，无需重复提交`)
      else toast.success(`${label}任务已创建`)
      setDialogOpen(false)
      setAuthsDialogOpen(false)
      void client.invalidateQueries({ queryKey: ['accounts'] })
      void client.invalidateQueries({ queryKey: ['accounts-count'] })
      void client.invalidateQueries({ queryKey: ['operations'] })
    },
    onError: (error) => toast.error(error.message),
  })

  const runDownload = async (kind: 'tokens' | 'cpa') => {
    setExportPending(true)
    try {
      await download(`/api/exports/${kind}`, { account_ids: ids }, kind === 'cpa' ? 'auths.zip' : 'tokens.txt')
      setExportOpen(false)
      toast.success('导出已开始')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '下载失败')
    } finally {
      setExportPending(false)
    }
  }

  const remove = async () => {
    if (!ids.length || !window.confirm(`确认删除 ${ids.length} 个本地账号记录？`)) return
    try {
      await api('/api/accounts', { method: 'DELETE', body: JSON.stringify({ account_ids: ids }) })
      setSelected(new Set())
      setPage(1)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['accounts'] }),
        client.invalidateQueries({ queryKey: ['accounts-count'] }),
      ])
      toast.success('账号记录已删除')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '删除失败')
    }
  }

  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((row) => row.id)))
  const toggle = (id: string) => setSelected((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const changePage = (nextPage: number) => {
    setPage(Math.max(1, Math.min(totalPages, nextPage)))
    setSelected(new Set())
  }
  const updateFilter = (key: keyof AccountFilters, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }))
    setPage(1)
    setSelected(new Set())
  }
  const activeFilterCount = Object.values(filters).filter(Boolean).length
  const accountSummary = dashboard.data?.accounts
  const readiness = (ready: number | undefined) => accountSummary?.total
    ? `${Math.round(((ready ?? 0) / accountSummary.total) * 100)}% 就绪`
    : '暂无账号'

  return <>
    <PageHeader title="账号管理" />

    <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
      <AccountMetric icon={Users} label="账号总数" value={accountSummary?.total ?? 0} detail="本地已保存" tone="neutral" />
      <AccountMetric icon={KeyRound} label="auths 可用" value={accountSummary?.oidc_ready ?? 0} detail={readiness(accountSummary?.oidc_ready)} tone="sky" />
      <AccountMetric icon={CloudUpload} label="SSO 已入池" value={accountSummary?.remote_web_ready ?? 0} detail={readiness(accountSummary?.remote_web_ready)} tone="emerald" />
      <AccountMetric icon={Server} label="Build 已入池" value={accountSummary?.remote_build_ready ?? 0} detail={readiness(accountSummary?.remote_build_ready)} tone="violet" />
      <AccountMetric icon={Server} label="Console 已入池" value={accountSummary?.remote_console_ready ?? 0} detail={readiness(accountSummary?.remote_console_ready)} tone="amber" />
    </div>

    <Card>
      <div className="grid gap-3 border-b p-4 xl:grid-cols-[minmax(260px,1fr)_auto] xl:items-center">
        <div className="flex w-full min-w-0 items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <Search className="muted pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" size={16} />
            <Input className="search-field h-10" placeholder="搜索邮箱" value={query} onChange={(event) => {
              setQuery(event.target.value)
              setPage(1)
              setSelected(new Set())
            }} />
          </div>
          <div className="relative shrink-0" ref={filterRef}>
            <Button variant="secondary" className="h-10 shrink-0 whitespace-nowrap px-3" onClick={() => setFilterOpen((open) => !open)}>
              <ListFilter size={15} />筛选{activeFilterCount > 0 ? ` ${activeFilterCount}` : ''}
            </Button>
            {filterOpen && <AccountFilterPanel
              filters={filters}
              onChange={updateFilter}
              onClear={() => {
                setFilters(EMPTY_FILTERS)
                setPage(1)
                setSelected(new Set())
              }}
              onClose={() => setFilterOpen(false)}
            />}
          </div>
        </div>
        <div className="flex min-h-10 flex-wrap items-center gap-2 xl:justify-end">
          <span className="muted mr-1 text-xs">已选 {ids.length}</span>
          <Button variant="secondary" onClick={() => accounts.refetch()}><RefreshCw size={15} />刷新</Button>
          <Button variant="secondary" disabled={!ids.length || operation.isPending} onClick={() => setAuthsDialogOpen(true)}>
            <KeyRound size={15} />生成 auths
          </Button>
          <Button variant="secondary" disabled={!ids.length || operation.isPending} onClick={() => setDialogOpen(true)}>
            <CloudUpload size={15} />远端入池
          </Button>
          <Button variant="secondary" onClick={() => setExportOpen(true)}><Download size={15} />{ids.length ? `导出（${ids.length}）` : '导出全部'}</Button>
          <Button variant="ghost" disabled={!ids.length} onClick={remove} aria-label="删除所选账号"><Trash2 size={15} className="text-red-500" /></Button>
        </div>
      </div>

      {accounts.isLoading ? <Spinner /> : rows.length ? <div className="overflow-x-auto scrollbar">
        <table className="w-full min-w-[1040px] table-fixed text-left text-sm">
          <thead className="text-xs muted"><tr>
            <th className="w-12 px-4 py-3"><input type="checkbox" checked={allSelected} onChange={toggleAll} /></th>
            <th className="w-[30%] px-3 py-3 font-medium">账号</th>
            <th className="w-24 px-3 py-3 font-medium">注册状态</th>
            <th className="w-28 px-3 py-3 font-medium">auths 状态</th>
            <th className="w-[260px] px-3 py-3 font-medium">远端入池状态</th>
            <th className="w-40 px-3 py-3 font-medium">创建时间</th>
          </tr></thead>
          <tbody className="divide-y">{rows.map((row) => <tr key={row.id} className="hover:bg-[var(--soft)]">
            <td className="px-4 py-3"><input type="checkbox" checked={selected.has(row.id)} onChange={() => toggle(row.id)} /></td>
            <td className="px-3 py-3"><div className="truncate font-medium" title={row.email}>{row.email}</div><div className="muted mt-0.5 truncate font-mono text-[11px]">{row.id}</div></td>
            <td className="px-3 py-3"><Badge value={row.register_status} /></td>
            <td className="px-3 py-3"><AuthStatus value={row.oidc_status} /></td>
            <td className="px-3 py-3"><RemoteStatuses account={row} /></td>
            <td className="muted px-3 py-3 text-xs">{formatDbTime(row.created_at, true)}</td>
          </tr>)}</tbody>
        </table>
      </div> : <Empty text="还没有账号，请先创建注册批次" />}

      <PaginationBar
        total={total}
        page={page}
        pageSize={pageSize}
        pageSizes={PAGE_SIZES}
        onPageChange={changePage}
        onPageSizeChange={(nextPageSize) => {
          setPageSize(nextPageSize)
          setPage(1)
          setSelected(new Set())
        }}
      />
    </Card>

    {exportOpen && <ExportDialog
      selectedCount={ids.length}
      pending={exportPending}
      onClose={() => setExportOpen(false)}
      onSubmit={(kind) => void runDownload(kind)}
    />}
    {authsDialogOpen && (
      <AuthsDialog
        accounts={selectedRows}
        pending={operation.isPending}
        onClose={() => setAuthsDialogOpen(false)}
        onSubmit={(accountIds) => {
          if (!accountIds.length) {
            toast.error('没有可生成的账号')
            return
          }
          operation.mutate({ kind: 'oidc', accountIds }, {
            onSuccess: () => setAuthsDialogOpen(false),
          })
        }}
      />
    )}
    {dialogOpen && <RemoteImportDialog
      accounts={selectedRows}
      pending={operation.isPending}
      onClose={() => setDialogOpen(false)}
      onSubmit={(kind, accountIds) => operation.mutate({ kind, accountIds })}
    />}
  </>
}

function AccountMetric({ icon: Icon, label, value, detail, tone }: { icon: typeof Users; label: string; value: number; detail: string; tone: 'neutral' | 'sky' | 'emerald' | 'violet' | 'amber' }) {
  const tones = {
    neutral: 'border-neutral-200 bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900',
    sky: 'border-sky-200 bg-sky-50 dark:border-sky-900 dark:bg-sky-950',
    emerald: 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950',
    violet: 'border-violet-200 bg-violet-50 dark:border-violet-900 dark:bg-violet-950',
    amber: 'border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950',
  }
  return <section className={`min-h-[68px] rounded-xl border px-3.5 py-2 ${tones[tone]}`}>
    <div className="flex items-center justify-between gap-3">
      <span className="muted text-[11px] font-medium">{label}</span>
      <Icon size={14} className="muted" />
    </div>
    <div className="mt-1 flex items-end justify-between gap-2"><strong className="text-xl tracking-tight">{value}</strong><span className="muted pb-0.5 text-[10px]">{detail}</span></div>
  </section>
}
