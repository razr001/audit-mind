import type { ReactNode } from 'react'
import { Modal, type ModalProps } from 'antd'
import './app-modal.css'

export interface AppModalProps extends Omit<ModalProps, 'title'> {
  title: ReactNode
  description?: ReactNode
  icon?: ReactNode
}

export function AppModal({ title, description, icon, className, children, ...modalProps }: AppModalProps) {
  return (
    <Modal
      {...modalProps}
      className={['app-modal', className].filter(Boolean).join(' ')}
      title={
        <div className="flex items-center gap-2.5">
          {icon && <span className="grid h-[34px] w-[34px] place-items-center rounded-[9px] border border-[rgba(128,241,198,.14)] bg-[rgba(128,241,198,.06)] text-[#80f1c6] [[data-theme=light]_&]:border-[rgba(20,125,99,.13)] [[data-theme=light]_&]:bg-[rgba(20,125,99,.06)] [[data-theme=light]_&]:text-[#147d63]">{icon}</span>}
          <div><strong className="block font-['Manrope'] text-sm font-semibold tracking-[-.2px]">{title}</strong>{description && <small className="mt-0.5 block text-[9px] font-normal text-[#71877e] [[data-theme=light]_&]:text-[#76877f]">{description}</small>}</div>
        </div>
      }
    >
      {children}
    </Modal>
  )
}
