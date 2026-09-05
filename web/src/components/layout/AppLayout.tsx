import { createContext, useContext, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Outlet, useRouterState } from '@tanstack/react-router'
import { useTheme } from '../../theme'
import { AppHeader } from './AppHeader'
import { AppSidebar, type NavigationKey } from './AppSidebar'

interface AppLayoutProps {
  headerStart: ReactNode
  children: ReactNode
}

const AppHeaderSlotContext = createContext<HTMLElement | null>(null)

export function AppLayout({ headerStart, children }: AppLayoutProps) {
  const headerSlot = useContext(AppHeaderSlotContext)

  return (
    <>
      {headerSlot && createPortal(headerStart, headerSlot)}
      {children}
    </>
  )
}

export function AuthenticatedLayout() {
  const { mode } = useTheme()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const [headerSlot, setHeaderSlot] = useState<HTMLDivElement | null>(null)

  return (
    <div className="app-shell group/app flex min-h-screen bg-[radial-gradient(circle_at_76%_-10%,rgba(67,166,130,.12),transparent_30%),var(--bg)]" data-theme={mode}>
      <AppSidebar activeKey={getActiveNavigation(pathname)} />
      <main className="ml-[238px] min-h-screen w-[calc(100%_-_238px)] transition-[margin,width] duration-300 peer-[.is-collapsed]:ml-[78px] peer-[.is-collapsed]:w-[calc(100%_-_78px)] max-[760px]:!ml-[72px] max-[760px]:!w-[calc(100%_-_72px)]">
        <AppHeader start={<div ref={setHeaderSlot} />} />
        <AppHeaderSlotContext.Provider value={headerSlot}>
          <Outlet />
        </AppHeaderSlotContext.Provider>
      </main>
    </div>
  )
}

export function getActiveNavigation(pathname: string): NavigationKey {
  if (pathname.startsWith('/regulation')) return 'regulations'
  if (pathname.startsWith('/assistant')) return 'assistant'
  if (pathname.startsWith('/users')) return 'users'
  return 'tasks'
}
