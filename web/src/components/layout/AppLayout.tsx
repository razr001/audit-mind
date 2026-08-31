import type { ReactNode } from 'react'
import { useTheme } from '../../theme'
import { AppHeader } from './AppHeader'
import { AppSidebar, type NavigationKey } from './AppSidebar'

interface AppLayoutProps {
  activeNavigation: NavigationKey
  headerStart: ReactNode
  children: ReactNode
}

export function AppLayout({ activeNavigation, headerStart, children }: AppLayoutProps) {
  const { mode } = useTheme()

  return (
    <div className="app-shell group/app flex min-h-screen bg-[radial-gradient(circle_at_76%_-10%,rgba(67,166,130,.12),transparent_30%),var(--bg)]" data-theme={mode}>
      <AppSidebar activeKey={activeNavigation} />
      <main className="ml-[238px] min-h-screen w-[calc(100%_-_238px)] transition-[margin,width] duration-300 peer-[.is-collapsed]:ml-[78px] peer-[.is-collapsed]:w-[calc(100%_-_78px)] max-[760px]:!ml-[72px] max-[760px]:!w-[calc(100%_-_72px)]">
        <AppHeader start={headerStart} />
        {children}
      </main>
    </div>
  )
}
