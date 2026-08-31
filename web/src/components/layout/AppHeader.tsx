import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { App, Avatar, Dropdown, Tooltip } from 'antd'
import { DownOutlined, GlobalOutlined, LogoutOutlined, MoonOutlined, SearchOutlined, SunOutlined } from '@ant-design/icons'
import { useTheme } from '../../theme'
import { setAccessToken } from '../../lib/auth-token'
import { logout as logoutRequest } from '../../service/auth'
import { useCurrentUser } from '../../hooks/use-current-user'

interface AppHeaderProps {
  start: ReactNode
}

export function AppHeader({ start }: AppHeaderProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const { t, i18n } = useTranslation()
  const { mode, toggleTheme } = useTheme()
  const currentUser = useCurrentUser()
  const username = currentUser.data?.username?.trim() || t('common.currentUser')
  const avatarText = username.slice(0, 1).toLocaleUpperCase()

  const switchLanguage = async () => {
    const next = i18n.language.startsWith('zh') ? 'en' : 'zh'
    await i18n.changeLanguage(next)
    localStorage.setItem('auditmind-language', next)
  }

  const logout = async () => {
    try {
      await logoutRequest()
    } catch {
      // 即使服务端暂时不可用，本地也必须退出；Cookie 会按原有效期失效。
    }
    setAccessToken(null)
    queryClient.clear()
    void message.success(t('common.loggedOut'))
    await navigate({ to: '/login', replace: true })
  }

  return (
    <header className="sticky top-0 z-10 flex h-[78px] items-center justify-between border-b border-[var(--line)] bg-[rgba(7,17,15,.78)] px-[3.2vw] backdrop-blur-[20px] group-data-[theme=light]/app:bg-[rgba(247,249,245,.82)] max-[760px]:px-[18px]">
      {start}
      <div className="flex items-center gap-2">
        <Tooltip title={mode === 'light' ? t('header.dark') : t('header.light')}>
          <button className="h-[38px] w-[38px] rounded-[9px] border-0 bg-transparent text-[17px] text-[#82968e] hover:bg-white/[.04] hover:text-[var(--mint)] group-data-[theme=light]/app:bg-[#e6f2ed] group-data-[theme=light]/app:text-[#116c56] max-[760px]:hidden" onClick={toggleTheme} aria-label={mode === 'light' ? t('header.dark') : t('header.light')}>
            {mode === 'light' ? <MoonOutlined /> : <SunOutlined />}
          </button>
        </Tooltip>
        <button className="h-9 border-0 bg-transparent px-2.5 text-[#82968e] group-data-[theme=light]/app:text-[#6f8179] max-[520px]:hidden" onClick={switchLanguage}><GlobalOutlined /> {i18n.language.startsWith('zh') ? 'EN' : '中'}</button>
        <Dropdown
          placement="bottomRight"
          trigger={['click']}
          menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />, label: t('common.logout'), danger: true }], onClick: ({ key }) => { if (key === 'logout') void logout() } }}
        >
          <button className="ml-2 flex items-center gap-2.5 border-0 border-l border-[var(--line)] bg-transparent pl-4 text-left text-inherit">
            <Avatar className="!bg-[linear-gradient(145deg,#335f50,#173229)] !text-[var(--mint)] group-data-[theme=light]/app:!bg-[linear-gradient(145deg,#d9eee5,#c6e1d6)] group-data-[theme=light]/app:!text-[#116c56]">{avatarText}</Avatar>
            <div className="max-[1120px]:hidden"><strong className="block max-w-32 truncate text-[13px]">{username}</strong><span className="mt-0.5 block text-[10px] text-[#71867e] group-data-[theme=light]/app:text-[#7b8c84]">{t('common.authenticatedUser')}</span></div>
            <DownOutlined className="text-[9px] text-[#657970] max-[760px]:hidden" />
          </button>
        </Dropdown>
      </div>
    </header>
  )
}

export function HeaderSearch({ placeholder }: { placeholder: string }) {
  return <div className="flex h-10 w-[min(430px,38vw)] items-center gap-2.5 rounded-[10px] border border-[var(--line)] bg-white/[.025] px-3 text-[#6f837b] group-data-[theme=light]/app:bg-white/70 group-data-[theme=light]/app:text-[#71827b] max-[760px]:w-auto max-[760px]:flex-1"><SearchOutlined /><input className="min-w-0 flex-1 border-0 bg-transparent text-[#dcece6] outline-none placeholder:text-[#596c65] group-data-[theme=light]/app:text-[#183228] group-data-[theme=light]/app:placeholder:text-[#91a099]" aria-label="Search" placeholder={placeholder} /><kbd className="rounded-[5px] border border-[#283a34] px-1.5 py-0.5 text-[10px] text-[#73867f] group-data-[theme=light]/app:border-[#d7e0db] group-data-[theme=light]/app:bg-[#f3f6f3] max-[760px]:hidden">⌘ K</kbd></div>
}

export function HeaderBreadcrumb({ section, current, icon }: { section: string; current: string; icon: ReactNode }) {
  return <div className="flex items-center gap-[9px] text-xs text-[var(--muted)]"><span>{icon}</span><span className="max-[760px]:hidden">{section}</span><i className="not-italic opacity-45 max-[760px]:hidden">/</i><strong className="font-semibold">{current}</strong></div>
}
