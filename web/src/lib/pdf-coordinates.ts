/** 将 MinerU 的 0..1000 标准化坐标映射到 PDF 当前旋转方向。 */
export function rotateBox(box: number[], rotation: number): [number, number, number, number] | null {
  if (box.length !== 4 || box.some((value) => !Number.isFinite(value))) return null
  const [x0, y0, x1, y1] = box.map((value) => Math.min(1000, Math.max(0, value)))
  if (x1 <= x0 || y1 <= y0) return null
  if (rotation === 90) return [1000 - y1, x0, 1000 - y0, x1]
  if (rotation === 180) return [1000 - x1, 1000 - y1, 1000 - x0, 1000 - y0]
  if (rotation === 270) return [y0, 1000 - x1, y1, 1000 - x0]
  return [x0, y0, x1, y1]
}
