const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '进行中',
  stopping: '停止中',
  pausing: '暂停中',
  paused: '已暂停',
  success: '成功',
  completed: '已完成',
  partial: '部分成功',
  failed: '失败',
  error: '错误',
  cancelled: '已取消',
  interrupted: '已中断',
  retried: '已重试',
  resolved: '已恢复',
  pending: '待处理',
  not_pushed: '未入池',
  imported: '已导入',
}

const AUTH_STATUS_LABELS: Record<string, string> = {
  pending: '未生成',
  queued: '生成排队中',
  running: '生成中',
  success: '已生成',
  failed: '生成失败',
  interrupted: '生成已中断',
}

const REMOTE_STATUS_LABELS: Record<string, string> = {
  not_pushed: '未入池',
  pending: '未入池',
  queued: '入池排队中',
  running: '入池中',
  stopping: '停止中',
  success: '已入池',
  failed: '入池失败',
  interrupted: '入池已中断',
}

const STAGE_LABELS: Record<string, string> = {
  queued: '等待开始',
  mailbox: '创建邮箱',
  signup_page: '加载注册协议',
  turnstile: 'Turnstile 验证',
  email_code: '邮箱验证码',
  create_account: '创建账号',
  sso: '获取 SSO',
  oidc: '生成 auths',
  completed: '注册完成',
  failed: '注册失败',
  cancelled: '已取消',
}

const OPERATION_LABELS: Record<string, string> = {
  oidc: '生成 auths',
  remote_sso: 'SSO 入池',
  remote_web: 'SSO 入池',
  remote_cpa: 'Build 入池',
  remote_console: 'Console 入池',
}

export const statusLabel = (value: string) => STATUS_LABELS[value] ?? value
export const authStatusLabel = (value: string) => AUTH_STATUS_LABELS[value] ?? value
export const remoteStatusLabel = (value: string) => REMOTE_STATUS_LABELS[value] ?? value
export const stageLabel = (value: string) => STAGE_LABELS[value] ?? value
export const operationLabel = (value: string) => OPERATION_LABELS[value] ?? value

const LEGACY_LOG_TEXT: Array<[RegExp, string]> = [
  [/^batch queued count=(\d+) concurrency=(\d+)$/, '[*] 注册批次已创建 · 数量 $1 · 并发 $2'],
  [/^stop requested$/, '[!] 正在停止任务'],
  [/^#(\d+) \[mailbox\] creating temporary mailbox$/, '[*] 账号 #$1 · 正在创建临时邮箱'],
  [/^#(\d+) \[mailbox\] mailbox ready: (.+)$/, '[+] 账号 #$1 · 临时邮箱已就绪：$2'],
  [/^#(\d+) \[signup_page\] loading live xAI signup page$/, '[*] 账号 #$1 · 正在加载 xAI 注册协议'],
  [/^#(\d+) \[turnstile\] solving Turnstile before email code$/, '[*] 账号 #$1 · 正在进行 Turnstile 验证'],
  [/^#(\d+) \[email_code\] sending email verification code$/, '[*] 账号 #$1 · 正在发送邮箱验证码'],
  [/^#(\d+) \[email_code\] waiting for verification code · (\d+)s$/, '[!] 账号 #$1 · 正在等待邮箱验证码 · $2 秒'],
  [/^#(\d+) \[email_code\] verification code received$/, '[+] 账号 #$1 · 邮箱验证码已获取'],
  [/^#(\d+) \[create_account\] creating xAI account · attempt (\d+)\/3$/, '[*] 账号 #$1 · 正在创建 xAI 账号 · 第 $2/3 次'],
  [/^#(\d+) \[sso\] extracting SSO session$/, '[*] 账号 #$1 · 正在获取 SSO 会话'],
  [/^#(\d+) \[completed\] registration and SSO completed$/, '[+] 账号 #$1 · 注册完成，SSO 已获取'],
  [/^#(\d+) registration success: (.+)$/, '[+] 账号 #$1 注册成功：$2'],
  [/^#(\d+) registration failed: (.+)$/, '[-] 账号 #$1 注册失败：$2'],
  [/^OIDC mint started: (.+)$/, '[*] 开始生成 auths：$1'],
  [/^OIDC mint success: (.+)$/, '[+] auths 生成成功：$1'],
  [/^OIDC mint failed: (.+)$/, '[-] auths 生成失败：$1'],
  [/^remote push started \(cpa\): (.+)$/, '[*] 开始 Build 入池：$1'],
  [/^remote push started \(sso\): (.+)$/, '[*] 开始 SSO 入池：$1'],
  [/^remote push success: (.+)$/, '[+] 远端入池成功：$1'],
  [/^remote push failed: (.+)$/, '[-] 远端入池失败：$1'],
]

export function logMessageLabel(message: string) {
  for (const [pattern, replacement] of LEGACY_LOG_TEXT) {
    if (pattern.test(message)) return message.replace(pattern, replacement)
  }
  return message
}