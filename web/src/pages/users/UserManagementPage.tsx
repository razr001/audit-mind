import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Empty, Form, Input, Modal, Popconfirm, Spin, Table, Tag, Tooltip } from 'antd'
import { DeleteOutlined, LockOutlined, PlusOutlined, ReloadOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppLayout } from '../../components/layout/AppLayout'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { useCurrentUser } from '../../hooks/use-current-user'
import { createUser, deleteUser, listUsers, type CreateUserRequest, type ManagedUser } from '../../service/user'

interface CreateUserForm extends CreateUserRequest {
  confirmPassword: string
}

export function UserManagementPage() {
  const { t, i18n } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const currentUser = useCurrentUser()
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm<CreateUserForm>()
  const usersQuery = useQuery({ queryKey: ['managed-users'], queryFn: listUsers })
  const users = usersQuery.data?.data ?? []

  const createMutation = useMutation({
    mutationFn: ({ username, password }: CreateUserForm) => createUser({ username, password }),
    onSuccess: async () => {
      setCreateOpen(false)
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
      void message.success(t('users.created'))
    },
  })
  const deleteMutation = useMutation({
    mutationFn: deleteUser,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['managed-users'] })
      void message.success(t('users.deleted'))
    },
  })

  const columns = [
    {
      title: t('users.username'),
      dataIndex: 'username',
      key: 'username',
      render: (username: string, user: ManagedUser) => (
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] border border-[color-mix(in_srgb,var(--mint)_20%,transparent)] bg-[color-mix(in_srgb,var(--mint)_9%,transparent)] text-[var(--mint)]"><UserOutlined /></span>
          <div><strong className="block text-[13px] font-semibold">{username}</strong>{user.id === currentUser.data?.userId && <Tag className="mt-1" color="success">{t('users.current')}</Tag>}</div>
        </div>
      ),
    },
    {
      title: t('users.createdAt'),
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 220,
      render: (value: string) => <span className="text-xs text-[var(--muted)]">{new Date(value).toLocaleString(i18n.resolvedLanguage)}</span>,
    },
    {
      title: t('users.actions'),
      key: 'actions',
      width: 100,
      align: 'right' as const,
      render: (_: unknown, user: ManagedUser) => {
        const isCurrent = user.id === currentUser.data?.userId
        const button = <Button danger type="text" icon={<DeleteOutlined />} disabled={isCurrent} loading={deleteMutation.isPending && deleteMutation.variables === user.id} />
        return isCurrent ? <Tooltip title={t('users.cannotDeleteSelf')}>{button}</Tooltip> : (
          <Popconfirm
            title={t('users.confirmDelete', { username: user.username })}
            description={t('users.deleteWarning')}
            okText={t('common.delete')}
            cancelText={t('common.cancel')}
            okButtonProps={{ danger: true }}
            onConfirm={() => deleteMutation.mutate(user.id)}
          >
            {button}
          </Popconfirm>
        )
      },
    },
  ]

  return (
    <AppLayout activeNavigation="users" headerStart={<HeaderBreadcrumb icon={<TeamOutlined />} section={t('users.center')} current={t('users.title')} />}>
      <div className="reveal mx-auto max-w-[1180px] px-[3.2vw] pt-3.5 pb-14 max-[760px]:px-4.5">
        <section className="mb-5 flex items-end justify-between gap-4 max-[760px]:items-stretch max-[760px]:flex-col">
          <div><p className="mb-1! text-[7px] tracking-[1.5px] text-(--mint)">IDENTITY CONTROL / 01</p><h1 className="m-0 text-[24px] font-semibold">{t('users.title')}</h1><p className="mt-1 mb-0 text-[11px] text-[var(--muted)]">{t('users.subtitle')}</p></div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>{t('users.add')}</Button>
        </section>

        <section className="panel overflow-hidden p-0!">
          <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-3">
            <span className="text-xs text-[var(--muted)]">{t('users.total', { count: users.length })}</span>
            <Button type="text" icon={<ReloadOutlined />} loading={usersQuery.isFetching} onClick={() => void usersQuery.refetch()}>{t('common.refresh')}</Button>
          </div>
          {usersQuery.isLoading || currentUser.isLoading ? <div className="grid min-h-72 place-items-center"><Spin /></div> : usersQuery.isError || currentUser.isError ? <Empty className="py-16" description={t('users.loadFailed')}><Button onClick={() => { void usersQuery.refetch(); void currentUser.refetch() }}>{t('common.retry')}</Button></Empty> : <Table<ManagedUser> rowKey="id" columns={columns} dataSource={users} pagination={false} scroll={{ x: 620 }} />}
        </section>
      </div>

      <Modal
        title={t('users.createTitle')}
        open={createOpen}
        okText={t('users.create')}
        cancelText={t('common.cancel')}
        confirmLoading={createMutation.isPending}
        onCancel={() => { setCreateOpen(false); form.resetFields() }}
        onOk={() => void form.submit()}
        destroyOnHidden
      >
        <p className="mt-0 mb-5 text-xs text-[var(--muted)]">{t('users.createDescription')}</p>
        <Form<CreateUserForm> form={form} layout="vertical" requiredMark={false} onFinish={(values) => createMutation.mutate(values)}>
          <Form.Item name="username" label={t('users.username')} rules={[{ required: true, message: t('users.usernameRequired') }, { min: 3, max: 64, message: t('users.usernameLength') }, { pattern: /^[\p{L}\p{N}._-]+$/u, message: t('users.usernamePattern') }]}>
            <Input prefix={<UserOutlined />} autoComplete="off" maxLength={64} />
          </Form.Item>
          <Form.Item name="password" label={t('users.password')} rules={[{ required: true, message: t('users.passwordRequired') }, { min: 8, max: 128, message: t('users.passwordLength') }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="new-password" maxLength={128} />
          </Form.Item>
          <Form.Item name="confirmPassword" label={t('users.confirmPassword')} dependencies={['password']} rules={[{ required: true, message: t('users.confirmPasswordRequired') }, ({ getFieldValue }) => ({ validator: (_, value) => !value || getFieldValue('password') === value ? Promise.resolve() : Promise.reject(new Error(t('users.passwordMismatch'))) })]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="new-password" maxLength={128} />
          </Form.Item>
        </Form>
      </Modal>
    </AppLayout>
  )
}
