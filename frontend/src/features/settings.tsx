import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, Loader2, Mail, Save, Shield, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'
import { useState } from 'react'
import { Button, Card, Field, Input, PageHeader, Select, Textarea, Toggle } from '../components/ui'
import { api } from '../lib/api'

type Settings = Record<string, string | number | boolean>

function SecretControl({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [visible, setVisible] = useState(false)
  return <div className="relative">
    <Input className="pr-10" type={visible ? 'text' : 'password'} value={value} onChange={(event) => onChange(event.target.value)} autoComplete="new-password" />
    <button type="button" className="absolute right-2 top-1/2 z-10 flex size-7 -translate-y-1/2 items-center justify-center rounded-md border bg-[var(--panel)] text-[var(--strong)] shadow-sm hover:bg-[var(--soft)]" onClick={() => setVisible((current) => !current)} aria-label={visible ? '隐藏敏感信息' : '查看敏感信息'} aria-pressed={visible} title={visible ? '隐藏' : '查看'}>{visible ? <EyeOff size={15} /> : <Eye size={15} />}</button>
  </div>
}

function NumberInput({ value, min, max, onChange }: { value: unknown; min: number; max: number; onChange: (value: number) => void }) {
  return <Input type="number" min={min} max={max} value={Number(value ?? min)} onChange={(event) => onChange(Number(event.target.value))} />
}

function SwitchSetting({ title, description, checked, onChange }: { title: string; description: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <div className="flex min-h-24 items-start justify-between gap-4 rounded-xl border bg-[var(--soft)]/60 p-4">
    <div><div className="text-sm font-medium">{title}</div><p className="muted mt-1 text-xs leading-5">{description}</p></div>
    <Toggle checked={checked} onChange={onChange} />
  </div>
}

export function SettingsPage() {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/api/settings') })
  if (query.isLoading) return <div className="muted">加载设置…</div>
  if (!query.data) return <div className="text-sm text-red-600">{query.error instanceof Error ? query.error.message : '设置加载失败'}</div>
  return <SettingsForm initial={query.data} client={client} />
}

function SettingsForm({ initial, client }: { initial: Settings; client: ReturnType<typeof useQueryClient> }) {
  const [form, setForm] = useState<Settings>(initial)
  const set = (key: string, value: string | number | boolean) => setForm((current) => ({ ...current, [key]: value }))
  const configured = (key: string) => Boolean(form[key]) || Boolean(form[`${key}_configured`])
  const save = useMutation({
    mutationFn: () => api<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify({ values: form }) }),
    onSuccess: (data) => { client.setQueryData(['settings'], data); setForm(data); toast.success('设置已保存') },
    onError: (error) => toast.error(error instanceof Error ? error.message : '设置保存失败'),
  })
  const test = useMutation({
    mutationFn: () => api<{ ok: boolean; mode?: string }>('/api/settings/test-remote', { method: 'POST', body: '{}' }),
    onSuccess: (data) => data.ok ? toast.success('远端管理接口连接正常') : toast.error('远端连接失败'),
    onError: (error) => toast.error(error instanceof Error ? error.message : '远端连接失败'),
  })

  return <>
    <PageHeader title="系统设置" actions={<Button onClick={() => save.mutate()} disabled={save.isPending}><Save size={16} />{save.isPending ? '保存中' : '保存设置'}</Button>} />

    <div className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-2">
        <div id="mail-settings" className="scroll-mt-6"><Card className="h-full border-l-4 border-l-sky-500 p-5 shadow-sm">
          <div className="flex items-center gap-3"><span className="flex size-9 items-center justify-center rounded-lg bg-sky-50 text-sky-600 dark:bg-sky-950 dark:text-sky-400"><Mail size={17} /></span><div><h2 className="font-medium">临时邮箱</h2><p className="muted mt-0.5 text-xs">创建邮箱并轮询注册验证码</p></div></div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <Field label="邮箱服务"><Select value={String(form.mail_provider ?? 'cfmail')} onChange={(event) => set('mail_provider', event.target.value)}><option value="cfmail">Cloudflare Mail</option><option value="moemail">MoeMail</option><option value="yyds">YYDS Mail</option><option value="gptmail">GPTMail</option><option value="tempmail">TempMail.lol</option></Select></Field>
            <Field label="API Base URL"><Input value={String(form.mail_base_url ?? '')} onChange={(event) => set('mail_base_url', event.target.value)} placeholder="https://mail.example.com" /></Field>
            <Field label="API 密钥" hint={configured('mail_api_key') ? '已配置，点击眼睛查看' : '尚未配置'}><SecretControl value={String(form.mail_api_key ?? '')} onChange={(value) => set('mail_api_key', value)} /></Field>
            <Field label="邮箱域名" hint="多个域名用逗号分隔"><Input value={String(form.mail_domains ?? '')} onChange={(event) => set('mail_domains', event.target.value)} /></Field>
            <Field label="验证码轮询超时（秒）"><NumberInput value={form.mail_poll_timeout} min={30} max={600} onChange={(value) => set('mail_poll_timeout', value)} /></Field>
          </div>
        </Card></div>

        <div id="captcha-settings" className="scroll-mt-6"><Card className="h-full border-l-4 border-l-amber-500 p-5 shadow-sm">
          <div className="flex items-center gap-3"><span className="flex size-9 items-center justify-center rounded-lg bg-amber-50 text-amber-600 dark:bg-amber-950 dark:text-amber-400"><Shield size={17} /></span><div><h2 className="font-medium">Turnstile 验证</h2><p className="muted mt-0.5 text-xs">配置本地 Solver 或第三方验证服务</p></div></div>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <Field label="验证服务"><Select value={String(form.captcha_provider ?? 'local')} onChange={(event) => set('captcha_provider', event.target.value)}><option value="local">本地 Solver</option><option value="yescaptcha">YesCaptcha</option></Select></Field>
            <Field label="本地 Solver URL"><Input value={String(form.local_solver_url ?? '')} onChange={(event) => set('local_solver_url', event.target.value)} /></Field>
            <Field label="服务密钥" hint={configured('captcha_api_key') ? '已配置，点击眼睛查看' : '仅第三方服务需要'}><SecretControl value={String(form.captcha_api_key ?? '')} onChange={(value) => set('captcha_api_key', value)} /></Field>
          </div>
        </Card></div>
      </div>

      <div id="registration-settings" className="scroll-mt-6"><Card className="border-l-4 border-l-violet-500 p-5 shadow-sm">
        <div className="flex items-center gap-3"><span className="flex size-9 items-center justify-center rounded-lg bg-violet-50 text-violet-600 dark:bg-violet-950 dark:text-violet-400"><SlidersHorizontal size={17} /></span><div><h2 className="font-medium">注册与自动化</h2><p className="muted mt-0.5 text-xs">批次参数、代理分配和注册后的自动任务链</p></div></div>
        <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="默认注册数（最多 25000）"><NumberInput value={form.registration_count} min={1} max={25000} onChange={(value) => set('registration_count', value)} /></Field>
          <Field label="默认并发数（注册与账号操作，最多 50）"><NumberInput value={form.registration_concurrency} min={1} max={50} onChange={(value) => set('registration_concurrency', value)} /></Field>
          <Field label="失败重试次数（注册与账号操作）"><NumberInput value={form.registration_retry_limit} min={0} max={5} onChange={(value) => set('registration_retry_limit', value)} /></Field>
          <Field label="代理策略"><Select value={String(form.proxy_strategy ?? 'round_robin')} onChange={(event) => set('proxy_strategy', event.target.value)}><option value="round_robin">轮询</option><option value="random">随机</option></Select></Field>
        </div>
        <div className="mt-4"><Field label="代理池" hint="每行一个代理，内容按原文显示"><Textarea value={String(form.proxy_pool ?? '')} onChange={(event) => set('proxy_pool', event.target.value)} /></Field></div>
        <div className="mt-6 border-t pt-5"><h3 className="text-sm font-medium">注册后自动处理</h3><p className="muted mt-1 text-xs">各任务独立执行，远端入池失败不会改变注册成功状态。</p></div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <SwitchSetting title="自动生成 auths" description="注册成功后生成 OAuth/auths；自动 Build 入池依赖此结果。" checked={Boolean(form.oidc_auto_mint)} onChange={(checked) => set('oidc_auto_mint', checked)} />
          <SwitchSetting title="自动入池 SSO" description="注册成功并获取 SSO 后，自动提交到 Grok Web。" checked={Boolean(form.remote_web_auto_push)} onChange={(checked) => set('remote_web_auto_push', checked)} />
          <SwitchSetting title="自动入池 Build" description="auths 生成成功后，自动提交到 Grok Build。" checked={Boolean(form.remote_build_auto_push)} onChange={(checked) => set('remote_build_auto_push', checked)} />
          <SwitchSetting title="自动入池 Console" description="注册成功并获取 SSO 后，自动提交到 Grok Console。" checked={Boolean(form.remote_console_auto_push)} onChange={(checked) => set('remote_console_auto_push', checked)} />
        </div>
      </Card></div>

      <div id="remote-settings" className="scroll-mt-6"><Card className="border-l-4 border-l-emerald-500 p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><span className="flex size-9 items-center justify-center rounded-lg bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400"><ShieldCheck size={17} /></span><div><h2 className="font-medium">Grok2API 远端池</h2><p className="muted mt-0.5 text-xs">SSO、Build 和 Console 共用管理员登录配置</p></div></div><Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending}>{test.isPending && <Loader2 className="animate-spin" size={15} />}测试连接</Button></div>
        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-[minmax(240px,2fr)_minmax(180px,1fr)_minmax(220px,1.3fr)_minmax(180px,0.8fr)]">
          <Field label="Base URL"><Input value={String(form.remote_base_url ?? '')} onChange={(event) => set('remote_base_url', event.target.value)} placeholder="https://grok2api.example.com" /></Field>
          <Field label="管理员用户名"><Input value={String(form.remote_username ?? '')} onChange={(event) => set('remote_username', event.target.value)} /></Field>
          <Field label="密码 / App Key" hint={configured('remote_secret') ? '已配置，点击眼睛查看' : '尚未配置'}><SecretControl value={String(form.remote_secret ?? '')} onChange={(value) => set('remote_secret', value)} /></Field>
          <Field label="远端操作并发数" hint="默认 4，最多 50；仅限制远端入池"><NumberInput value={form.remote_operation_concurrency} min={1} max={50} onChange={(value) => set('remote_operation_concurrency', value)} /></Field>
        </div>
      </Card></div>
    </div>
  </>
}