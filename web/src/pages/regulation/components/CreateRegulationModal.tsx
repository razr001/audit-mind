import { useEffect, useRef } from 'react'
import { App, DatePicker, Form, Input, Segmented, Select, Upload, type UploadFile } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { CloudUploadOutlined, FileTextOutlined, InboxOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { AppModal } from '../../../components/modal/AppModal'
import { processRegulation } from '../../../service/regulation-processing'
import { createRegulationText, uploadRegulation, type KnowledgeVisibility, type RegulationSourceType, type RegulationUploadForm } from '../../../service/regulation-sources'
import { regulationTitleFromFilename } from './regulation-file-name'
import { getSourceTypeLabels } from './regulation-options'

interface CreateRegulationModalProps {
  open: boolean
  onClose: () => void
  onCreated: () => Promise<void>
}

interface CreateRegulationFormValues {
  inputMode: 'file' | 'text'
  title: string
  sourceType: RegulationSourceType
  visibility: KnowledgeVisibility
  documentNumber?: string
  authority?: string
  effectiveDate?: { format: (pattern: string) => string }
  expirationDate?: { format: (pattern: string) => string }
  version?: string
  sourceUrl?: string
  files?: UploadFile[]
  content?: string
}

export function CreateRegulationModal({ open, onClose, onCreated }: CreateRegulationModalProps) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [form] = Form.useForm<CreateRegulationFormValues>()
  const lastSuggestedTitle = useRef<string | undefined>(undefined)
  const inputMode = Form.useWatch('inputMode', form) ?? 'file'
  const createMutation = useMutation({
    mutationFn: async (values: CreateRegulationFormValues) => {
      const metadata: RegulationUploadForm = { title: values.title, sourceType: values.sourceType, visibility: values.visibility, documentNumber: values.documentNumber, authority: values.authority, effectiveDate: values.effectiveDate?.format('YYYY-MM-DD'), expirationDate: values.expirationDate?.format('YYYY-MM-DD'), version: values.version, sourceUrl: values.sourceUrl }
      const response = values.inputMode === 'text'
        ? await createRegulationText({ ...metadata, content: values.content ?? '' })
        : await uploadRegulation({ ...metadata, file: requireFile(values.files, t('regulation.create.selectPdf')) })
      if (!response.data) throw new Error(response.message)
      await processRegulation(response.data.id)
      return response.data
    },
    onSuccess: async () => { void message.success(t('regulation.create.success')); onClose(); await onCreated() },
  })

  useEffect(() => {
    if (!open) {
      form.resetFields()
      lastSuggestedTitle.current = undefined
    }
  }, [form, open])

  const normalizeFiles = (event: UploadFile[] | { fileList?: UploadFile[] } | undefined): UploadFile[] => {
    const files = Array.isArray(event) ? event : event?.fileList ?? []
    const selectedFile = files[0]
    const currentTitle = form.getFieldValue('title')?.trim()

    if (!selectedFile) {
      // 只有仍在使用自动名称时才清空，避免删除文件时误删用户手动填写的名称。
      if (currentTitle === lastSuggestedTitle.current) form.setFieldValue('title', undefined)
      lastSuggestedTitle.current = undefined
      return files
    }

    const suggestedTitle = regulationTitleFromFilename(selectedFile.name)
    if (!currentTitle || currentTitle === lastSuggestedTitle.current) {
      form.setFieldValue('title', suggestedTitle)
    }
    lastSuggestedTitle.current = suggestedTitle
    return files
  }
  const sourceTypeLabels = getSourceTypeLabels(t)

  return (
    <AppModal className="regulation-create-modal" title={t('regulation.create.title')} description={t('regulation.create.description')} icon={<CloudUploadOutlined />} open={open} onCancel={onClose} onOk={() => form.submit()} confirmLoading={createMutation.isPending} okText={t(inputMode === 'text' ? 'regulation.create.saveProcess' : 'regulation.create.uploadProcess')} cancelText={t('common.cancel')} width={720} centered destroyOnHidden>
      <Form form={form} layout="vertical" initialValues={{ inputMode: 'file', sourceType: 'REGULATION', visibility: 'SHARED', files: [] }} onFinish={(values) => createMutation.mutate(values)} requiredMark="optional">
        <Form.Item name="inputMode" className="!mb-3"><Segmented block options={[{ value: 'file', label: t('regulation.create.uploadPdf'), icon: <CloudUploadOutlined /> }, { value: 'text', label: t('regulation.create.inputText'), icon: <FileTextOutlined /> }]} /></Form.Item>
        {inputMode === 'file' ? (
          <Form.Item name="files" valuePropName="fileList" getValueFromEvent={normalizeFiles} rules={[{ required: true, message: t('regulation.create.selectPdf') }]}><Upload.Dragger beforeUpload={() => false} maxCount={1} accept=".pdf,application/pdf"><div className="flex items-center gap-3 p-[13px_15px] text-left"><span className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[9px] bg-[rgba(128,241,198,.08)] text-lg text-[#80f1c6] [[data-theme=light]_&]:bg-[rgba(20,125,99,.07)] [[data-theme=light]_&]:text-[#147d63]"><InboxOutlined /></span><div className="flex-1"><strong className="block text-[11px] font-semibold text-[#dcebe5] [[data-theme=light]_&]:text-[#1d3129]">{t('regulation.create.choosePdf')}</strong><small className="mt-[3px] block text-[9px] text-[#687d75] [[data-theme=light]_&]:text-[#7a8b83]">{t('regulation.create.pdfDescription')}</small></div><em className="rounded-[7px] border border-[#31483f] px-2.5 py-1.5 text-[9px] text-[#91a69d] not-italic [[data-theme=light]_&]:border-[#d4dfda] [[data-theme=light]_&]:bg-white [[data-theme=light]_&]:text-[#47665a]">{t('regulation.create.browse')}</em></div></Upload.Dragger></Form.Item>
        ) : (
          <Form.Item name="content" label={t('regulation.create.sourceText')} extra={t('regulation.create.sourceTextExtra')} rules={[{ required: true, whitespace: true, message: t('regulation.create.sourceTextRequired') }, { max: 500000, message: t('regulation.create.sourceTextTooLong') }]}><Input.TextArea rows={10} showCount maxLength={500000} placeholder={t('regulation.create.textPlaceholder')} /></Form.Item>
        )}
        <div className="grid grid-cols-2 gap-x-3.5 max-[760px]:grid-cols-1">
          <Form.Item name="title" label={t('regulation.create.name')} rules={[{ required: true, whitespace: true, message: t('regulation.create.nameRequired') }, { max: 255 }]}><Input placeholder={t('regulation.create.nameExample')} /></Form.Item>
          <Form.Item name="documentNumber" label={t('regulation.create.number')}><Input placeholder={t('regulation.create.numberExample')} maxLength={100} /></Form.Item>
          <Form.Item name="sourceType" label={t('regulation.create.type')} rules={[{ required: true }]}><Select options={Object.entries(sourceTypeLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="visibility" label={t('regulation.create.visibility')} rules={[{ required: true }]}><Select options={[{ value: 'SHARED', label: t('common.shared') }, { value: 'PRIVATE', label: t('regulation.create.selfOnly') }]} /></Form.Item>
          <Form.Item name="authority" label={t('regulation.create.authority')}><Input placeholder={t('regulation.create.authorityExample')} maxLength={255} /></Form.Item>
          <Form.Item name="version" label={t('regulation.create.version')}><Input placeholder={t('regulation.create.versionExample')} maxLength={50} /></Form.Item>
        </div>
        <div className="grid grid-cols-2 gap-x-3.5 max-[760px]:grid-cols-1"><Form.Item name="effectiveDate" label={t('regulation.create.effectiveDate')}><DatePicker className="!w-full" placeholder={t('regulation.create.selectDate')} /></Form.Item><Form.Item name="expirationDate" label={t('regulation.create.expirationDate')}><DatePicker className="!w-full" placeholder={t('regulation.create.selectDate')} /></Form.Item></div>
        <Form.Item name="sourceUrl" label={t('regulation.create.sourceUrl')} rules={[{ type: 'url', message: t('regulation.create.validUrl') }, { max: 1000 }]}><Input placeholder="https://..." /></Form.Item>
      </Form>
    </AppModal>
  )
}

function requireFile(files: UploadFile[] | undefined, errorMessage: string): File {
  const file = files?.[0]?.originFileObj
  if (!file) throw new Error(errorMessage)
  return file
}
