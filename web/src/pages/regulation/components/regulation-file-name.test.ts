import { describe, expect, it } from 'vitest'
import { regulationTitleFromFilename } from './regulation-file-name'

describe('regulationTitleFromFilename', () => {
  it('removes the PDF extension from a Chinese filename', () => {
    expect(regulationTitleFromFilename('个人信息保护法.pdf')).toBe('个人信息保护法')
  })

  it('only removes the final extension and ignores its letter case', () => {
    expect(regulationTitleFromFilename('数据安全法.2024.PDF')).toBe('数据安全法.2024')
  })
})
