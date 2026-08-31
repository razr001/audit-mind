/**
 * 使用上传文件名作为默认法规名称，仅移除最后一个 PDF 后缀。
 * 保留文件名中的其他点号，例如 `数据安全法.2024.pdf` 会得到 `数据安全法.2024`。
 */
export function regulationTitleFromFilename(filename: string): string {
  return filename.trim().replace(/\.pdf$/i, '')
}
