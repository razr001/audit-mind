import { describe, expect, it } from 'vitest'
import { rotateBox } from './pdf-coordinates'

describe('rotateBox', () => {
  it('keeps normalized coordinates aligned at every supported rotation', () => {
    const box = [100, 200, 400, 500]
    expect(rotateBox(box, 0)).toEqual([100, 200, 400, 500])
    expect(rotateBox(box, 90)).toEqual([500, 100, 800, 400])
    expect(rotateBox(box, 180)).toEqual([600, 500, 900, 800])
    expect(rotateBox(box, 270)).toEqual([200, 600, 500, 900])
  })

  it('rejects malformed or empty boxes instead of drawing misleading highlights', () => {
    expect(rotateBox([1, 2, 3], 0)).toBeNull()
    expect(rotateBox([100, 100, 100, 200], 0)).toBeNull()
    expect(rotateBox([Number.NaN, 0, 10, 10], 0)).toBeNull()
  })
})
