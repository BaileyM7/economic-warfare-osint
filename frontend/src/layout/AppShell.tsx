import { Suspense } from 'react'
import { Outlet } from 'react-router-dom'
import TopNav from './TopNav'
import SideNav from './SideNav'
import StatusBar from './StatusBar'

function PageLoader() {
  return (
    <div className="flex items-center justify-center h-[calc(100vh-48px)]">
      <span className="material-symbols-outlined text-3xl text-primary animate-spin">
        progress_activity
      </span>
    </div>
  )
}

export default function AppShell() {
  return (
    <>
      <TopNav />
      <SideNav />
      <main className="ml-[240px] pt-12 pb-8 min-h-screen bg-surface-dim">
        <Suspense fallback={<PageLoader />}>
          <Outlet />
        </Suspense>
      </main>
      <StatusBar />
    </>
  )
}
