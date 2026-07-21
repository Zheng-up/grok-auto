export type AuthStatus = {
  initialized: boolean
  authenticated: boolean
  user: { id: number; username: string } | null
}

export type Batch = {
  id: string
  status: string
  target_count: number
  concurrency: number
  completed: number
  success: number
  failed: number
  cancel_requested: number
  pause_requested?: number
  error?: string
  created_at: string
  updated_at: string
  jobs?: RegistrationJob[]
  config?: Record<string, unknown>
}

export type RegistrationJob = {
  id: string
  slot: number
  status: string
  stage: string
  message: string
  email?: string
  account_id?: string
  error?: string
  oidc_status?: string
  oidc_error?: string
  started_at?: string
  created_at?: string
  updated_at?: string
}

export type Account = {
  id: string
  email: string
  register_status: string
  oidc_status: string
  remote_status: string
  remote_web_status: string
  remote_build_status: string
  remote_console_status: string
  cpa_file?: string
  source_job_id?: string
  last_error?: string
  oidc_error?: string
  remote_error?: string
  remote_web_error?: string
  remote_build_error?: string
  remote_console_error?: string
  created_at: string
  updated_at: string
  active_operations?: string[]
  has_sso: boolean
  has_oidc: boolean
}

export type Operation = {
  id: string
  kind: string
  status: string
  total: number
  completed: number
  success: number
  failed: number
  cancel_requested?: number
  pause_requested?: number
  error?: string
  created_at: string
  updated_at: string
  items?: { account_id: string; status: string; message: string }[]
  reused?: boolean
}

export type Dashboard = {
  accounts: {
    total: number
    oidc_ready: number
    remote_web_ready: number
    remote_build_ready: number
    remote_console_ready: number
  }
  today: { total: number; success: number; failed: number; average_seconds: number; total_seconds: number }
  active: { active_batches: number; active_operations: number }
  recent_batches: Batch[]
  recent_operations: Operation[]
}

export type Settings = Record<string, string | number | boolean | undefined>