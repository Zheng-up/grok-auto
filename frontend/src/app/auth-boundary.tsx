import { useEffect, useState, type FormEvent, type PropsWithChildren } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import type { AuthStatus } from '../lib/types'
import { Button, Card, Field, Input, Spinner } from '../components/ui'

function AuthForm({ initialized, onDone }: { initialized: boolean; onDone: () => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true)
    try {
      if (!initialized) await api('/api/auth/initialize', { method: 'POST', body: JSON.stringify({ username, password }) })
      await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
      onDone()
    } catch (error) { toast.error(error instanceof Error ? error.message : '操作失败') }
    finally { setBusy(false) }
  }
  return <main className="flex min-h-screen items-center justify-center p-5">
    <Card className="w-full max-w-sm p-7 shadow-sm">
      <div className="mb-7 flex items-center gap-3"><div className="flex size-10 items-center justify-center rounded-xl bg-neutral-900 text-white dark:bg-white dark:text-black"><ShieldCheck size={20} /></div><div><h1 className="font-semibold">Grok 注册台</h1><p className="muted text-xs">{initialized ? '管理员登录' : '初始化管理员'}</p></div></div>
      <form className="space-y-4" onSubmit={submit}>
        <Field label="用户名"><Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></Field>
        <Field label="密码" hint={!initialized ? '至少 8 个字符，初始化后请妥善保存' : undefined}><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={initialized ? 'current-password' : 'new-password'} /></Field>
        <Button className="w-full" disabled={busy}>{busy ? '处理中…' : initialized ? '登录' : '创建并登录'}</Button>
      </form>
    </Card>
  </main>
}

export function AuthBoundary({ children }: PropsWithChildren) {
  const status = useQuery({ queryKey: ['auth'], queryFn: () => api<AuthStatus>('/api/auth/status'), retry: false })
  const { refetch } = status
  useEffect(() => {
    const refresh = () => { void refetch() }
    window.addEventListener('session-expired', refresh)
    return () => window.removeEventListener('session-expired', refresh)
  }, [refetch])
  if (status.isLoading) return <Spinner />
  if (!status.data?.authenticated) return <AuthForm initialized={Boolean(status.data?.initialized)} onDone={() => status.refetch()} />
  return children
}