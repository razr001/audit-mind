import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { App as AntApp, ConfigProvider, theme as antTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import { useTranslation } from 'react-i18next'
import { GlobalMessageBridge } from './components/feedback/GlobalMessage'

type ThemeMode = 'light' | 'dark'

type ThemeContextValue = {
  mode: ThemeMode
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  const chinese = i18n.resolvedLanguage?.startsWith('zh') ?? true
  const [mode, setMode] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem('auditmind-theme')
    return saved === 'dark' || saved === 'light' ? saved : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = mode
    document.documentElement.style.colorScheme = mode
    localStorage.setItem('auditmind-theme', mode)
  }, [mode])

  useEffect(() => {
    document.documentElement.lang = chinese ? 'zh-CN' : 'en'
  }, [chinese])

  const value = useMemo(
    () => ({ mode, toggleTheme: () => setMode((current) => current === 'light' ? 'dark' : 'light') }),
    [mode],
  )

  const isLight = mode === 'light'
  const fieldBackground = isLight ? '#ffffff' : '#0e1d18'
  const fieldBorder = isLight ? '#dce4df' : '#263c34'
  const fieldBorderActive = isLight ? '#6cae98' : 'rgba(128, 241, 198, .55)'
  const fieldText = isLight ? '#1d3129' : '#dce9e4'
  const fieldPlaceholder = isLight ? '#8a9a93' : '#4e645b'
  const elevatedBackground = isLight ? '#fbfcfa' : '#0b1613'

  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider
        locale={chinese ? zhCN : enUS}
        theme={{
          algorithm: isLight ? antTheme.defaultAlgorithm : antTheme.darkAlgorithm,
          token: {
            colorPrimary: isLight ? '#147d63' : '#80f1c6',
            colorBgBase: isLight ? '#f6f8f4' : '#07110f',
            colorTextBase: isLight ? '#14241f' : '#edf8f4',
            colorBgContainer: fieldBackground,
            colorBgElevated: elevatedBackground,
            colorText: fieldText,
            colorTextPlaceholder: fieldPlaceholder,
            colorBorder: fieldBorder,
            colorBorderSecondary: isLight ? '#e4ebe7' : '#20322c',
            borderRadius: 9,
            controlHeight: 36,
            controlOutline: 'transparent',
            fontFamily: '"DM Sans", "Noto Sans SC", sans-serif',
          },
          components: {
            Button: {
              controlHeight: 36,
              fontWeight: 600,
              defaultBg: fieldBackground,
              defaultBorderColor: fieldBorder,
              defaultColor: fieldText,
              defaultHoverBg: fieldBackground,
              defaultHoverBorderColor: fieldBorderActive,
              defaultHoverColor: isLight ? '#147d63' : '#80f1c6',
              defaultShadow: 'none',
              primaryColor: isLight ? '#ffffff' : '#063d31',
              primaryShadow: 'none',
            },
            Input: {
              activeBg: fieldBackground,
              activeBorderColor: fieldBorderActive,
              activeShadow: 'none',
              hoverBg: fieldBackground,
              hoverBorderColor: fieldBorderActive,
            },
            Select: {
              selectorBg: fieldBackground,
              activeBorderColor: fieldBorderActive,
              activeOutlineColor: 'transparent',
              hoverBorderColor: fieldBorderActive,
              optionSelectedBg: isLight ? '#e7f2ed' : '#17362c',
            },
            DatePicker: {
              activeBg: fieldBackground,
              activeBorderColor: fieldBorderActive,
              activeShadow: 'none',
              hoverBg: fieldBackground,
              hoverBorderColor: fieldBorderActive,
            },
            Drawer: { colorBgElevated: elevatedBackground },
            Segmented: { itemSelectedBg: isLight ? '#ffffff' : '#1a2b25' },
            Tooltip: { colorBgSpotlight: isLight ? '#173c31' : '#182722' },
          },
        }}
      >
        <AntApp><GlobalMessageBridge />{children}</AntApp>
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used inside ThemeProvider')
  return context
}
