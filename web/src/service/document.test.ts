import { describe, expect, it } from 'vitest'
import { toMinioProxyUrl } from './document'

describe('toMinioProxyUrl', () => {
  it('keeps the object path and every presigned query parameter', () => {
    const source = 'http://192.168.5.205:9000/auditmind-documents/documents/test.pdf?X-Amz-Expires=1800&X-Amz-Signature=abc'

    expect(toMinioProxyUrl(source)).toBe(
      '/minio/auditmind-documents/documents/test.pdf?X-Amz-Expires=1800&X-Amz-Signature=abc',
    )
  })
})
