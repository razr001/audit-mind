import { Spin } from 'antd'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../theme'

export function RouteLoading() {
  const { t } = useTranslation()
  const { mode } = useTheme()

  return (
    <output className="app-shell grid min-h-screen place-items-center bg-[var(--bg)]" data-theme={mode} aria-live="polite">
      <Spin size="large" description={t('common.loading')} />
    </output>
  )
}
