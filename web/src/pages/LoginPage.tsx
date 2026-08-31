import { useMutation } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { App, Button, Form, Input, Tooltip } from 'antd'
import { ArrowRightOutlined, LockOutlined, MoonOutlined, SunOutlined, UserOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { login, type LoginRequest } from '../service/auth'
import { setAccessToken } from '../service/client'
import { showGlobalError } from '../components/feedback/GlobalMessage'
import { useTheme } from '../theme'

export function LoginPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const { t } = useTranslation()
  const { mode, toggleTheme } = useTheme()

  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: async (response) => {
      if (!response.data) {
        showGlobalError(response.message || '登录失败')
        return
      }
      setAccessToken(response.data.accessToken)
      void message.success(t('auth.loginSuccess'))
      await navigate({ to: '/audit', replace: true })
    },
  })

  return (
    <main
      data-theme={mode}
      className="group/login relative grid min-h-screen place-items-center overflow-hidden bg-[#07110f] px-6 py-10 text-[#eaf5f1] data-[theme=light]:bg-[#f4f6f2] data-[theme=light]:text-[#16251f]"
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_22%_15%,rgba(128,241,198,.13),transparent_27%),radial-gradient(circle_at_84%_88%,rgba(57,115,94,.12),transparent_31%)] [[data-theme=light]_&]:bg-[radial-gradient(circle_at_22%_15%,rgba(20,125,99,.12),transparent_27%),radial-gradient(circle_at_84%_88%,rgba(90,164,135,.11),transparent_31%)]" />
      <div className="pointer-events-none absolute inset-0 opacity-[.045] [background-image:linear-gradient(rgba(128,241,198,.8)_1px,transparent_1px),linear-gradient(90deg,rgba(128,241,198,.8)_1px,transparent_1px)] [background-size:42px_42px] [[data-theme=light]_&]:opacity-[.035]" />

      <Tooltip title={mode === 'light' ? '切换暗色主题' : '切换明亮主题'}>
        <button className="absolute top-6 right-6 grid h-10 w-10 place-items-center rounded-[10px] border border-white/10 bg-white/[.035] text-base text-[#8ba098] transition hover:border-[#80f1c6]/30 hover:text-[#80f1c6] [[data-theme=light]_&]:border-[#173c31]/10 [[data-theme=light]_&]:bg-white/70 [[data-theme=light]_&]:text-[#60756c]" onClick={toggleTheme} aria-label="切换主题">
          {mode === 'light' ? <MoonOutlined /> : <SunOutlined />}
        </button>
      </Tooltip>

      <section className="relative w-full max-w-[430px] rounded-[22px] border border-white/10 bg-[rgba(10,24,20,.86)] p-2 shadow-[0_35px_100px_rgba(0,0,0,.42)] backdrop-blur-2xl [[data-theme=light]_&]:border-[#173c31]/10 [[data-theme=light]_&]:bg-white/80 [[data-theme=light]_&]:shadow-[0_30px_80px_rgba(35,75,59,.14)]">
        <div className="rounded-[17px] border border-white/[.055] px-8 py-9 [[data-theme=light]_&]:border-[#173c31]/[.07]">
          <div className="mb-8 flex items-center gap-3">
            <div className="grid h-11 w-11 rotate-45 place-items-center rounded-xl border border-[#80f1c6]/35 bg-[#80f1c6]/10 shadow-[inset_0_0_20px_rgba(128,241,198,.09)] [[data-theme=light]_&]:border-[#147d63]/30 [[data-theme=light]_&]:bg-[#147d63]/10"><span className="h-3.5 w-3.5 rounded-[4px] bg-[#80f1c6] shadow-[0_0_16px_rgba(128,241,198,.5)] [[data-theme=light]_&]:bg-[#147d63]" /></div>
            <div><strong className="block font-['Manrope'] text-xl leading-none tracking-[-.5px]">AuditMind</strong><small className="mt-1.5 block text-[8px] tracking-[2.4px] text-[#71877e]">INTELLIGENCE</small></div>
          </div>

          <p className="mb-2 text-[9px] font-semibold tracking-[2px] text-[#80f1c6] [[data-theme=light]_&]:text-[#147d63]">SECURE WORKSPACE</p>
          <h1 className="m-0 font-['Manrope'] text-[28px] font-semibold tracking-[-.8px]">{t('auth.title')}</h1>
          <p className="mt-2 mb-7 text-xs leading-5 text-[#7f938b] [[data-theme=light]_&]:text-[#71827a]">{t('auth.subtitle')}</p>

          <Form<LoginRequest> layout="vertical" requiredMark={false} onFinish={(values) => loginMutation.mutate(values)}>
            <Form.Item name="username" label={t('auth.username')} rules={[{ required: true, message: t('auth.usernameRequired') }]}>
              <Input prefix={<UserOutlined />} autoComplete="username" maxLength={64} />
            </Form.Item>
            <Form.Item name="password" label={t('auth.password')} rules={[{ required: true, message: t('auth.passwordRequired') }, { min: 8, message: t('auth.passwordLength') }]}>
              <Input.Password prefix={<LockOutlined />} autoComplete="current-password" maxLength={128} />
            </Form.Item>
            <Button htmlType="submit" className="!mt-2 !h-11 !w-full !text-[13px]" type="primary" loading={loginMutation.isPending}>
              {t('auth.login')} <ArrowRightOutlined />
            </Button>
          </Form>
        </div>
      </section>
    </main>
  )
}
