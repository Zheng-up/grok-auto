import clsx from 'clsx'
import type { ButtonHTMLAttributes, InputHTMLAttributes, PropsWithChildren, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import type { LogRow } from '../lib/events'
import { logMessageLabel, statusLabel } from '../lib/labels'

export function Button({ className, variant = 'primary', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }) {
  return <button className={clsx(
    'inline-flex min-h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition disabled:pointer-events-none disabled:opacity-45',
    variant === 'primary' && 'bg-neutral-900 text-white hover:bg-neutral-700 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-white',
    variant === 'secondary' && 'border bg-[var(--panel)] hover:bg-[var(--soft)]',
    variant === 'danger' && 'bg-red-600 text-white hover:bg-red-500',
    variant === 'ghost' && 'hover:bg-[var(--soft)]',
    className,
  )} {...props} />
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) { return <input {...props} className={clsx('field', props.className)} /> }
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) { return <select {...props} className={clsx('field', props.className)} /> }
export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea {...props} className={clsx('field min-h-24 resize-y', props.className)} /> }
export function Toggle({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className={clsx('relative h-5 w-9 shrink-0 rounded-full transition', checked ? 'bg-neutral-900 dark:bg-white' : 'bg-neutral-300 dark:bg-neutral-700')}><span className={clsx('absolute top-0.5 size-4 rounded-full bg-white shadow transition-all dark:bg-neutral-900', checked ? 'left-[18px]' : 'left-0.5')} /></button>
}

export function Card({ children, className = '' }: PropsWithChildren<{ className?: string }>) {
  return <section className={`surface ${className}`}>{children}</section>
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return <div className="mb-6 flex min-h-10 flex-col justify-between gap-4 sm:flex-row sm:items-center">
    <div><h1 className="m-0 text-2xl font-semibold tracking-tight">{title}</h1>{description && <p className="muted mt-1 text-sm">{description}</p>}</div>
    {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
  </div>
}

export function Field({ label, hint, children }: PropsWithChildren<{ label: string; hint?: string }>) {
  return <label className="block"><span className="mb-1.5 block text-sm font-medium">{label}</span>{children}{hint && <span className="muted mt-1 block text-xs">{hint}</span>}</label>
}

export function Badge({ value }: { value: string }) {
  const normalized = value.trim().toLowerCase()
  const success = ['success', 'completed', 'imported', 'resolved'].includes(normalized)
  const danger = ['failed', 'error'].includes(normalized)
  const running = ['running', 'queued', 'stopping', 'pausing'].includes(normalized)
  return <span className={clsx('inline-flex whitespace-nowrap items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs', success && 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300', danger && 'border-red-300 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300', running && 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300', !success && !danger && !running && 'text-[var(--muted)]')}>
    <span className={clsx('status-dot', success ? 'bg-emerald-500' : danger ? 'bg-red-500' : running ? 'bg-amber-500' : 'bg-neutral-400')} />{statusLabel(normalized)}
  </span>
}

function displayLog(row: LogRow) {
  const message = logMessageLabel(row.message)
  if (['success', 'warning', 'error'].includes(row.level)) return { message, level: row.level }
  if (message.startsWith('[+]')) return { message, level: 'success' }
  if (message.startsWith('[!]')) return { message, level: 'warning' }
  if (message.startsWith('[-]')) return { message, level: 'error' }
  return { message, level: 'info' }
}

export function LogViewer({ rows, className = 'h-64', emptyText = '等待任务日志…' }: { rows: LogRow[]; className?: string; emptyText?: string }) {
  return <div className={clsx('scrollbar overflow-auto bg-neutral-950 p-3 font-mono text-xs text-neutral-300', className)}>
    {rows.length ? <div className="space-y-1.5">{rows.map((row) => {
      const display = displayLog(row)
      return <div
        key={row.id}
        className={clsx(
          'grid grid-cols-[64px_1fr] gap-2 rounded-md border-l-2 px-2.5 py-1.5 leading-5',
          display.level === 'success' && 'border-emerald-500 bg-emerald-500/10 text-emerald-300',
          display.level === 'warning' && 'border-amber-500 bg-amber-500/10 text-amber-200',
          display.level === 'error' && 'border-red-500 bg-red-500/10 text-red-300',
          display.level === 'info' && 'border-sky-500/70 bg-sky-500/5 text-neutral-300',
        )}
      ><span className="text-neutral-600">{row.created_at.slice(11, 19)}</span><span className="break-words">{display.message}</span></div>
    })}</div> : <div className="px-2 py-3 text-neutral-600">{emptyText}</div>}
  </div>
}

export function PaginationBar({
  total,
  page,
  pageSize,
  pageSizes,
  onPageChange,
  onPageSizeChange,
}: {
  total: number
  page: number
  pageSize: number
  pageSizes: readonly number[]
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const currentPage = Math.max(1, Math.min(page, totalPages))
  return <div className="grid gap-3 border-t px-4 py-3 sm:grid-cols-[1fr_auto_1fr] sm:items-center">
    <div className="muted text-xs">共 {total} 条</div>
    <div className="flex items-center justify-center gap-2">
      <Button variant="secondary" className="min-h-8 px-2.5 text-xs" disabled={currentPage <= 1} onClick={() => onPageChange(currentPage - 1)}>上一页</Button>
      <span className="min-w-20 text-center text-xs">{currentPage} / {totalPages}</span>
      <Button variant="secondary" className="min-h-8 px-2.5 text-xs" disabled={currentPage >= totalPages} onClick={() => onPageChange(currentPage + 1)}>下一页</Button>
    </div>
    <label className="flex items-center justify-end gap-2 text-xs">
      <span className="muted">每页</span>
      <select className="field page-size-select" value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
        {pageSizes.map((size) => <option key={size} value={size}>{size}</option>)}
      </select>
      <span className="muted">条</span>
    </label>
  </div>
}

export function Empty({ text = '暂无数据' }: { text?: string }) { return <div className="muted py-12 text-center text-sm">{text}</div> }

export function Spinner() { return <div className="flex min-h-48 items-center justify-center"><div className="size-5 animate-spin rounded-full border-2 border-neutral-400 border-t-transparent" /></div> }