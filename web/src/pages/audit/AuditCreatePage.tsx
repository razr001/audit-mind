import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery } from '@tanstack/react-query'
import { App, Button, Collapse, Form, Input, Segmented, Select, Upload, type UploadFile } from 'antd'
import { AuditOutlined, FileTextOutlined, InboxOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppLayout } from '../../components/layout/AppLayout'
import { HeaderBreadcrumb } from '../../components/layout/AppHeader'
import { useCurrentUser } from '../../hooks/use-current-user'
import { createAuditWorkflowTask, createMarkdownAuditWorkflowTask, type AuditRuleScope } from '../../service/audit-task'
import type { DocumentSourceType } from '../../service/document'
import { getRegulationList } from '../../service/regulation-sources'
import './audit.css'

interface AuditCreateValues {
  files?: UploadFile[]
  title?: string
  content?: string
  regulationIds?: string[]
  categories?: ('PUBLIC_KNOWLEDGE' | 'COMPANY_RULE')[]
}

export function AuditCreatePage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [form] = Form.useForm<AuditCreateValues>()
  const [submitting, setSubmitting] = useState(false)
  const [sourceType, setSourceType] = useState<DocumentSourceType>('PDF')
  const currentUser = useCurrentUser()
  const userId = currentUser.data?.userId
  const regulations = useQuery({ queryKey: ['regulations', 'audit-scope', userId], queryFn: () => getRegulationList({ page: 1, pageSize: 100 }), enabled: Boolean(userId) })
  const mutation = useMutation({
    mutationFn: async ({ values, submittedSourceType }: { values: AuditCreateValues; submittedSourceType: DocumentSourceType }) => {
      const ruleScope: AuditRuleScope = {
        regulationIds: values.regulationIds,
        categories: values.categories,
      }
      const response = submittedSourceType === 'PDF'
        ? await createAuditWorkflowTask({ file: requirePdf(values.files, t('audit.create.selectPdf')), ruleScope })
        : await createMarkdownAuditWorkflowTask({
            title: values.title?.trim() ?? '',
            content: values.content ?? '',
            ruleScope,
          })
      if (!response.data) throw new Error(response.message)
      return response.data
    },
    onSuccess: (task) => { void message.success(t('audit.create.submitted')); void navigate({ to: '/audit/$taskId', params: { taskId: task.id } }) },
    onError: (error) => void message.error(error instanceof Error ? error.message : t('audit.create.failed')),
    onSettled: () => setSubmitting(false),
  })
  const submit = (values: AuditCreateValues) => { if (submitting) return; setSubmitting(true); mutation.mutate({ values, submittedSourceType: sourceType }) }

  return (
    <AppLayout activeNavigation="tasks" headerStart={<HeaderBreadcrumb icon={<AuditOutlined />} section={t('audit.title')} current={t('audit.create.breadcrumb')} />}>
      <div className="audit-page reveal mx-auto max-w-3xl px-[3.2vw] pt-5 pb-14 max-[760px]:px-4.5">
        <section className="mb-5"><h1 className="m-0 text-2xl font-semibold">{t('audit.create.heading')}</h1><p className="mt-2 text-xs text-[var(--muted)]">{t('audit.create.subtitle')}</p></section>
        <Form form={form} layout="vertical" initialValues={{ files: [] }} onFinish={submit}>
          <section className="panel">
            <Segmented<DocumentSourceType>
              className="mb-5"
              block
              value={sourceType}
              options={[{ value: 'PDF', label: t('audit.create.uploadPdf'), icon: <InboxOutlined /> }, { value: 'MARKDOWN', label: t('audit.create.inputText'), icon: <FileTextOutlined /> }]}
              onChange={(value) => setSourceType(value)}
            />
            {sourceType === 'PDF' ? <Form.Item name="files" valuePropName="fileList" getValueFromEvent={(event) => Array.isArray(event) ? event : event?.fileList} rules={[{ required: true, message: t('audit.create.selectPdf') }]}>
              <Upload.Dragger accept=".pdf,application/pdf" maxCount={1} beforeUpload={(file) => {
                if (file.type && file.type !== 'application/pdf') { void message.error(t('audit.create.pdfOnly')); return Upload.LIST_IGNORE }
                return false
              }}><p className="text-3xl text-[var(--mint)]"><InboxOutlined /></p><p>{t('audit.create.dropPdf')}</p><p className="text-[10px] text-[var(--muted)]">{t('audit.create.pdfValidation')}</p></Upload.Dragger>
            </Form.Item> : <>
              <Form.Item name="title" label={t('audit.create.contentTitle')} rules={[{ required: true, whitespace: true, message: t('audit.create.contentTitleRequired') }, { max: 252, message: t('audit.create.titleTooLong') }]}><Input placeholder={t('audit.create.titleExample')} /></Form.Item>
              <Form.Item name="content" label={t('audit.create.content')} rules={[{ required: true, whitespace: true, message: t('audit.create.contentRequired') }]} extra={t('audit.create.contentExtra')}><Input.TextArea className="font-mono" autoSize={{ minRows: 16, maxRows: 28 }} placeholder={t('audit.create.contentExample')} /></Form.Item>
            </>}
            <Collapse ghost items={[{ key: 'scope', label: t('audit.create.advancedScope'), children: <div className="grid grid-cols-2 gap-x-4 max-[760px]:grid-cols-1">
              <Form.Item name="regulationIds" label={t('audit.create.regulations')}><Select mode="multiple" allowClear showSearch optionFilterProp="label" loading={regulations.isLoading} options={(regulations.data?.data?.items ?? []).map((item) => ({ value: item.id, label: item.title }))} /></Form.Item>
              <Form.Item name="categories" label={t('audit.create.categories')}><Select mode="multiple" options={[{ value: 'PUBLIC_KNOWLEDGE', label: t('audit.create.publicKnowledge') }, { value: 'COMPANY_RULE', label: t('audit.create.companyRule') }]} /></Form.Item>
            </div> }]} />
            <div className="mt-5 flex justify-end gap-2"><Button onClick={() => void navigate({ to: '/audit' })}>{t('common.cancel')}</Button><Button type="primary" htmlType="submit" loading={submitting}>{t('audit.create.submit')}</Button></div>
          </section>
        </Form>
      </div>
    </AppLayout>
  )
}

function requirePdf(files: UploadFile[] | undefined, errorMessage: string): File {
  const file = files?.[0]?.originFileObj
  if (!file) throw new Error(errorMessage)
  return file
}
