import io

from pypdf import PdfWriter


def create_test_pdf() -> bytes:
    """生成一页结构完整的 PDF，避免测试把伪造文件头当作合法文档。"""
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return stream.getvalue()
