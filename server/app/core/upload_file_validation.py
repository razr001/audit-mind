import asyncio
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.error_codes import UNSUPPORTED_FILE_TYPE
from app.core.exceptions import BusinessException

SUPPORTED_FILE_TYPES = {
    ".pdf": "application/pdf",
}


def get_supported_file_type(filename: str) -> tuple[str, str]:
    """根据文件名取得允许的后缀及服务端认可的标准 MIME 类型。"""
    # 后缀只用于选择对应的内容校验器，不能单独证明文件内容可信。
    suffix = Path(filename).suffix.lower()
    content_type = SUPPORTED_FILE_TYPES.get(suffix)
    if content_type is None:
        raise BusinessException(
            UNSUPPORTED_FILE_TYPE,
            "only PDF files are supported; convert other files to PDF first",
        )
    return suffix, content_type


async def validate_file_content(
    *,
    suffix: str,
    first_chunk: bytes,
    file: UploadFile,
) -> None:
    """综合扩展名、文件签名和 PDF 结构校验真实文件类型。"""
    # suffix 当前只能是 .pdf；保留参数用于明确防御未来调用者绕过
    # get_supported_file_type() 直接调用本函数。
    if suffix != ".pdf" or not first_chunk.startswith(b"%PDF-"):
        raise BusinessException(
            UNSUPPORTED_FILE_TYPE,
            "file content is not a valid PDF",
        )

    # 浏览器声明的 Content-Type 可以伪造，因此只作为辅助信息。真正的安全
    # 边界是服务端解析 PDF 交叉引用表和页面树，不能让仅伪造 `%PDF-` 文件头
    # 的任意数据进入 MinIO 和 MinerU。解析可能涉及磁盘和 CPU，放入线程池
    # 避免阻塞 FastAPI 事件循环。
    try:
        await file.seek(0)
        page_count = await asyncio.to_thread(_read_pdf_page_count, file.file)
        if page_count < 1:
            raise PdfReadError("PDF does not contain pages")
    # pypdf 对不同损坏方式可能抛出 PdfReadError、ValueError、KeyError 等
    # 不同异常。这里是“不可信上传内容”的边界，统一转换为可读的业务错误，
    # 避免畸形 PDF 变成 500；CancelledError 属于 BaseException，不会被吞掉。
    except Exception as exc:
        raise BusinessException(
            UNSUPPORTED_FILE_TYPE,
            "file content is not a valid PDF",
        ) from exc
    finally:
        await file.seek(0)


def _read_pdf_page_count(file_object: BinaryIO) -> int:
    """同步解析 PDF；由 ``validate_file_content`` 在线程池中调用。"""
    reader = PdfReader(file_object, strict=False)
    if reader.is_encrypted:
        # 当前 MinerU 流程无法可靠解析需要密码的文档，上传阶段直接拒绝，
        # 比后台长时间等待后失败更容易让用户理解和修正。
        raise PdfReadError("encrypted PDF is not supported")
    return len(reader.pages)
