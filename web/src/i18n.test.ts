import { afterAll, describe, expect, it } from 'vitest'
import i18n from './i18n'

describe('module translations', () => {
  afterAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('provides English copy for the three primary business modules', async () => {
    await i18n.changeLanguage('en')

    expect(i18n.t('audit.title')).toBe('Audit tasks')
    expect(i18n.t('regulation.title')).toBe('Regulations')
    expect(i18n.t('assistant.title')).toBe('Audit assistant')
    expect(i18n.t('audit.status.RUNNING')).toBe('Processing')
    expect(i18n.t('regulation.rules.type.PROHIBITION')).toBe('Prohibition')
    expect(i18n.t('assistant.phase.retrieving')).toBe('Searching regulation knowledge')
    expect(i18n.t('assistant.connectionInterrupted')).toContain('unexpectedly')
  })
})
