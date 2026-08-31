import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Tooltip } from 'antd'
import { useTranslation } from 'react-i18next'
import {
  AuditOutlined,
  BookOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  TeamOutlined,
} from '@ant-design/icons'

export type NavigationKey = 'tasks' | 'regulations' | 'assistant' | 'users'

interface AppSidebarProps {
  activeKey: NavigationKey
}

export function AppSidebar({ activeKey }: AppSidebarProps) {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState(false)
  const navigationItems = [
    { key: 'tasks', label: t('nav.tasks'), icon: <AuditOutlined />, path: '/audit' },
    { key: 'regulations', label: t('nav.regulations'), icon: <BookOutlined />, path: '/regulation' },
    { key: 'assistant', label: t('nav.assistant'), icon: <RobotOutlined />, path: '/assistant' },
    { key: 'users', label: t('nav.users'), icon: <TeamOutlined />, path: '/users' },
  ] as const

  return (
    <aside className={`sidebar peer fixed inset-y-0 left-0 z-20 flex flex-col border-r border-[var(--line)] bg-[rgba(8,18,15,.94)] px-3.5 pt-[22px] pb-[18px] backdrop-blur-[18px] transition-[width] duration-300 group-data-[theme=light]/app:bg-[rgba(250,252,248,.92)] group-data-[theme=light]/app:shadow-[12px_0_38px_rgba(31,62,51,.035)] max-[760px]:!w-[72px] ${collapsed ? 'is-collapsed w-[78px]' : 'w-[238px]'}`}>
      <button className="flex h-[52px] w-full items-center gap-[11px] border-0 bg-transparent px-2 text-left text-inherit" onClick={() => navigate({ to: '/audit' })} aria-label={t('nav.tasks')}>
        <div className="grid h-[34px] w-[34px] shrink-0 rotate-45 place-items-center rounded-[10px] border border-[rgba(128,241,198,.38)] bg-[linear-gradient(145deg,rgba(128,241,198,.22),rgba(128,241,198,.02))] shadow-[inset_0_0_18px_rgba(128,241,198,.1)] group-data-[theme=light]/app:border-[rgba(20,125,99,.3)] group-data-[theme=light]/app:bg-[linear-gradient(145deg,rgba(20,125,99,.14),rgba(255,255,255,.8))]">
          <span className="h-3 w-3 rounded-[3px] bg-[var(--mint)] shadow-[0_0_15px_rgba(128,241,198,.55)]" />
        </div>
        {!collapsed && <div className="max-[760px]:hidden"><strong className="block font-['Manrope'] text-[18px] leading-none font-bold tracking-[-.4px]">AuditMind</strong><small className="mt-1.5 block text-[8px] tracking-[2.1px] text-[#6f847d] group-data-[theme=light]/app:text-[#82928b]">INTELLIGENCE</small></div>}
      </button>

      <nav className="mt-[34px] flex flex-col gap-1.5" aria-label={t('common.primaryNavigation')}>
        {navigationItems.map((item) => (
          <Tooltip key={item.key} title={collapsed ? item.label : ''} placement="right">
            <button
              className={`relative flex h-[46px] w-full items-center gap-[13px] whitespace-nowrap rounded-[10px] border-0 px-[13px] text-left transition-colors duration-200 ${activeKey === item.key ? 'text-[var(--mint)] bg-[linear-gradient(90deg,rgba(128,241,198,.15),rgba(128,241,198,.03))] before:absolute before:left-[-14px] before:h-[25px] before:w-0.5 before:bg-[var(--mint)] before:shadow-[0_0_10px_var(--mint)] group-data-[theme=light]/app:bg-[linear-gradient(90deg,rgba(20,125,99,.12),rgba(20,125,99,.025))]' : 'bg-transparent text-[#84978f] hover:bg-white/[.035] hover:text-[#e8f6f0] group-data-[theme=light]/app:text-[#71827a] group-data-[theme=light]/app:hover:bg-[rgba(20,125,99,.05)] group-data-[theme=light]/app:hover:text-[#173c31]'}`}
              onClick={() => 'path' in item && navigate({ to: item.path })}
              aria-current={activeKey === item.key ? 'page' : undefined}
            >
              <span className="w-6 text-center text-[18px]">{item.icon}</span>
              {!collapsed && <span className="max-[760px]:hidden">{item.label}</span>}
            </button>
          </Tooltip>
        ))}
      </nav>

      <div className="mt-auto">
        <button className="h-[38px] w-full rounded-[9px] border border-[var(--line)] bg-transparent text-[#667b73] group-data-[theme=light]/app:bg-white/55 group-data-[theme=light]/app:text-[#809088]" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? t('common.expandMenu') : t('common.collapseMenu')}>
          {collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        </button>
      </div>
    </aside>
  )
}
