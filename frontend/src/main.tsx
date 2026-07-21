import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from 'next-themes'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'
import { Toaster } from 'sonner'
import { AuthBoundary } from './app/auth-boundary'
import { AppShell } from './app/app-shell'
import { RegisterPage } from './features/register'
import { AccountsPage } from './features/accounts'
import { TasksPage } from './features/tasks'
import { SettingsPage } from './features/settings'
import './index.css'

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 3000, refetchOnWindowFocus: false } } })
const router = createBrowserRouter([{ element: <AuthBoundary><AppShell /></AuthBoundary>, children: [
  { index: true, element: <RegisterPage /> },
  { path: 'register', element: <Navigate to="/" replace /> },
  { path: 'accounts', element: <AccountsPage /> },
  { path: 'tasks', element: <TasksPage /> },
  { path: 'settings', element: <SettingsPage /> },
]}])

createRoot(document.getElementById('root')!).render(<StrictMode><ThemeProvider attribute="class" defaultTheme="system" enableSystem><QueryClientProvider client={queryClient}><RouterProvider router={router} /><Toaster
  richColors
  position="top-right"
  expand={false}
  visibleToasts={3}
  gap={10}
  offset={24}
  toastOptions={{ duration: 3200, className: 'app-toast' }}
/></QueryClientProvider></ThemeProvider></StrictMode>)