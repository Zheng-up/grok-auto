import clsx from 'clsx'
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Database,
  ListChecks,
  LogOut,
  Menu,
  Moon,
  Play,
  Settings,
  Sun,
  X,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import { useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { GlobalTaskStatus } from './global-task-status'

const nav = [
  { to: '/', label: '开始注册', icon: Play },
  { to: '/accounts', label: '账号管理', icon: Database },
  { to: '/tasks', label: '任务日志', icon: ListChecks },
  { to: '/settings', label: '系统设置', icon: Settings },
]

export function AppShell() {
  const [open, setOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('sidebar-collapsed') === '1')
  const { theme, setTheme } = useTheme()
  const navigate = useNavigate()
  const location = useLocation()
  const fixedViewport = location.pathname === '/'

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      localStorage.setItem('sidebar-collapsed', current ? '0' : '1')
      return !current
    })
  }
  const logout = async () => {
    await api('/api/auth/logout', { method: 'POST' })
    navigate(0)
  }

  return <div className="min-h-screen bg-[var(--bg)]">
    {open && <button className="fixed inset-0 z-30 bg-black/35 lg:hidden" onClick={() => setOpen(false)} aria-label="关闭导航" />}
    <aside className={clsx(
      'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r bg-[var(--panel)] transition-[width,transform] duration-200 ease-in-out lg:translate-x-0',
      open ? 'translate-x-0' : '-translate-x-full',
      collapsed ? 'lg:w-20' : 'lg:w-64',
    )}>
      <button
        type="button"
        className="absolute -right-4 top-5 z-10 hidden size-8 items-center justify-center rounded-full border bg-[var(--panel)] text-[var(--strong)] shadow-md transition-colors hover:bg-[var(--soft)] lg:flex"
        onClick={toggleCollapsed}
        aria-label={collapsed ? '展开导航' : '折叠导航'}
        title={collapsed ? '展开导航' : '折叠导航'}
      >{collapsed ? <ChevronRight size={19} strokeWidth={2.4} /> : <ChevronLeft size={19} strokeWidth={2.4} />}</button>
      <div className={clsx('flex h-16 items-center justify-between overflow-hidden border-b px-3', collapsed && 'lg:justify-center lg:px-2')}>
        <Link to="/" className={clsx('flex min-w-0 items-center gap-2.5 font-semibold transition-[gap] duration-200', collapsed && 'lg:gap-0')} title="Grok 注册台">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-neutral-900 text-white dark:bg-white dark:text-black"><Activity size={17} /></span>
          <span className={clsx('overflow-hidden whitespace-nowrap transition-[max-width,opacity,transform] duration-200', collapsed ? 'lg:max-w-0 lg:-translate-x-1 lg:opacity-0' : 'lg:max-w-40 lg:translate-x-0 lg:opacity-100')}>Grok 注册台</span>
        </Link>
        <Button className="size-8 px-0 lg:hidden" variant="ghost" onClick={() => setOpen(false)} aria-label="关闭导航"><X size={18} /></Button>
      </div>
      <nav className="flex-1 space-y-1 overflow-hidden p-3">{nav.map(({ to, label, icon: Icon }) => <NavLink
        key={to}
        to={to}
        end={to === '/'}
        title={collapsed ? label : undefined}
        onClick={() => setOpen(false)}
        className={({ isActive }) => clsx(
          'flex items-center gap-3 overflow-hidden whitespace-nowrap rounded-lg px-3 py-2.5 text-sm transition-all duration-200',
          collapsed && 'lg:justify-center lg:gap-0 lg:px-0',
          isActive
            ? 'bg-neutral-900 text-white dark:bg-white dark:text-black'
            : 'muted hover:bg-[var(--soft)] hover:text-[var(--strong)]',
        )}
      ><Icon className="shrink-0" size={17} /><span className={clsx('overflow-hidden whitespace-nowrap transition-[max-width,opacity,transform] duration-200', collapsed ? 'lg:max-w-0 lg:-translate-x-1 lg:opacity-0' : 'lg:max-w-40 lg:translate-x-0 lg:opacity-100')}>{label}</span></NavLink>)}</nav>
      <div className="space-y-1 overflow-hidden border-t p-3">
        <button
          className={clsx('muted flex w-full items-center gap-3 overflow-hidden whitespace-nowrap rounded-lg px-3 py-2.5 text-sm transition-all duration-200 hover:bg-[var(--soft)]', collapsed && 'lg:justify-center lg:gap-0 lg:px-0')}
          title={collapsed ? '切换主题' : undefined}
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        >{theme === 'dark' ? <Sun className="shrink-0" size={17} /> : <Moon className="shrink-0" size={17} />}<span className={clsx('overflow-hidden whitespace-nowrap transition-[max-width,opacity,transform] duration-200', collapsed ? 'lg:max-w-0 lg:-translate-x-1 lg:opacity-0' : 'lg:max-w-40 lg:translate-x-0 lg:opacity-100')}>切换主题</span></button>
        <button
          className={clsx('muted flex w-full items-center gap-3 overflow-hidden whitespace-nowrap rounded-lg px-3 py-2.5 text-sm transition-all duration-200 hover:bg-[var(--soft)]', collapsed && 'lg:justify-center lg:gap-0 lg:px-0')}
          title={collapsed ? '退出登录' : undefined}
          onClick={logout}
        ><LogOut className="shrink-0" size={17} /><span className={clsx('overflow-hidden whitespace-nowrap transition-[max-width,opacity,transform] duration-200', collapsed ? 'lg:max-w-0 lg:-translate-x-1 lg:opacity-0' : 'lg:max-w-40 lg:translate-x-0 lg:opacity-100')}>退出登录</span></button>
      </div>
    </aside>
    <div className={clsx('transition-[padding] duration-200 ease-in-out', collapsed ? 'lg:pl-20' : 'lg:pl-64')}>
      <header className="sticky top-0 z-20 flex h-16 items-center border-b bg-[color-mix(in_srgb,var(--bg)_88%,transparent)] px-4 backdrop-blur lg:hidden">
        <Button variant="ghost" onClick={() => setOpen(true)} aria-label="打开导航"><Menu size={19} /></Button>
        <span className="ml-2 font-medium">Grok 注册台</span>
      </header>
      <main className={clsx(
        'w-full max-w-none p-4 sm:p-6 sm:pr-8 lg:p-8 lg:pr-10',
        fixedViewport && 'h-[calc(100vh-4rem)] overflow-hidden lg:h-screen',
      )}><Outlet /></main>
    </div>
    <GlobalTaskStatus />
  </div>
}