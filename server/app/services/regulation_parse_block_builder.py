import zipfile
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.infrastructure.mineru_content import extract_mineru_block_content
from app.models import RegulationParseBlock

settings = get_settings()
MAX_ZIP_COMPRESSION_RATIO = 100


class RegulationParseBlockBuilder:
    """Validate MinerU archives and build persisted text and visual blocks."""

    @staticmethod
    def _build_visual_targets(
        content_list: list[Any],
    ) -> dict[str, tuple[int, dict[str, Any]]]:
        """按 MinerU 图片文件名定位需要补充描述的 image/chart 块。"""
        targets: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, item in enumerate(content_list):
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"image", "chart"}:
                continue
            img_path = item.get("img_path")
            if not isinstance(img_path, str) or not img_path:
                continue
            filename = PurePosixPath(img_path.replace("\\", "/")).name
            targets[filename] = (index, item)
        return targets

    @staticmethod
    def _has_mineru_visual_content(item: dict[str, Any]) -> bool:
        """只检查 MinerU 的视觉 content，不把原文 caption 当成图片描述。"""
        value = item.get("content")
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (dict, list)):
            return bool(extract_mineru_block_content({"content": value}).strip())
        return False

    @staticmethod
    def _build_nearby_text(
        *,
        content_list: list[Any],
        item_index: int,
    ) -> str:
        """提取同页相邻原文，帮助视觉模型理解图片所在语境。"""
        target = content_list[item_index]
        if not isinstance(target, dict):
            return ""
        page_index = target.get("page_idx")
        parts: list[str] = []
        for index, item in enumerate(content_list):
            if index == item_index or not isinstance(item, dict):
                continue
            if item.get("page_idx") != page_index:
                continue
            if item.get("type") in {"image", "chart"}:
                continue
            content = extract_mineru_block_content(item)
            if content.strip():
                parts.append(content)
        return "\n\n".join(parts)[:4000]

    @staticmethod
    def _validate_archive_members(
        members: list[zipfile.ZipInfo],
    ) -> None:
        """校验 ZIP 路径、解压总量、图片数量和常见压缩炸弹特征。"""
        total_uncompressed = 0
        image_count = 0

        for member in members:
            name = RegulationParseBlockBuilder._normalized_member_name(member)
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("unsafe path in MinerU ZIP")

            total_uncompressed += member.file_size
            if total_uncompressed > settings.MINERU_MAX_RESULT_UNCOMPRESSED_SIZE:
                raise RuntimeError("MinerU ZIP uncompressed content is too large")

            if member.file_size > 0 and member.compress_size == 0:
                raise RuntimeError("invalid compression size in MinerU ZIP")
            if (
                member.compress_size > 0
                and member.file_size / member.compress_size > MAX_ZIP_COMPRESSION_RATIO
            ):
                raise RuntimeError("MinerU ZIP compression ratio is too high")

            if RegulationParseBlockBuilder._is_image_member(member):
                image_count += 1
                if image_count > settings.MINERU_MAX_RESULT_IMAGES:
                    raise RuntimeError("MinerU ZIP contains too many images")
                if member.file_size > settings.MINERU_MAX_RESULT_IMAGE_SIZE:
                    raise RuntimeError("MinerU image is too large")

    @staticmethod
    def _normalized_member_name(member: zipfile.ZipInfo) -> str:
        return member.filename.replace("\\", "/")

    @staticmethod
    def _is_image_member(member: zipfile.ZipInfo) -> bool:
        if member.is_dir():
            return False
        parts = PurePosixPath(RegulationParseBlockBuilder._normalized_member_name(member)).parts
        return "images" in parts[:-1]

    @staticmethod
    def _prepare_image(data: bytes) -> tuple[bytes, str, str] | None:
        """校验图片真实类型；SVG 返回 None，表示直接丢弃。"""
        if data.startswith(b"\xff\xd8\xff"):
            return data, ".jpg", "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return data, ".png", "image/png"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return data, ".gif", "image/gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return data, ".webp", "image/webp"
        if data.startswith(b"BM"):
            return data, ".bmp", "image/bmp"
        if data.startswith((b"II*\x00", b"MM\x00*")):
            return data, ".tiff", "image/tiff"

        # 不信任 ZIP 中的扩展名，按内容识别 SVG。图片在解压校验阶段已受
        # 单图大小限制，因此扫描完整字节可覆盖长 XML 注释或声明后的根元素。
        if b"<svg" in data.lower():
            return None

        raise RuntimeError("MinerU returned an unsupported image type")

    @staticmethod
    def _build_parse_blocks(
        *,
        regulation_id: UUID,
        content_list: list[Any],
        assets_by_filename: dict[str, dict[str, Any]],
        visual_analysis_by_filename: dict[str, dict[str, Any]],
        discarded_image_filenames: set[str] | None = None,
    ) -> list[RegulationParseBlock]:
        """把文本和视觉块统一保存，并保持规范原文字符偏移一致。"""
        parse_blocks: list[RegulationParseBlock] = []
        discarded_image_filenames = discarded_image_filenames or set()
        cursor = 0
        # MinerU 的 table 块也可能包含 img_path。它既有可检索的表格文本，
        # 也有用于前端原文预览的局部截图，因此和 image/chart 一样处理。
        visual_types = {"image", "table", "chart"}

        for item in content_list:
            if not isinstance(item, dict):
                continue

            block_type = str(item.get("type") or "unknown")
            content = extract_mineru_block_content(item)
            is_visual = block_type in visual_types

            # 视觉块即使暂时没有描述文本也必须保存，否则前端会丢失
            # 图片的页码、bbox 和 MinIO 对象关联。
            if not content.strip() and not is_visual:
                continue

            img_path = item.get("img_path")
            asset: dict[str, Any] | None = None
            if isinstance(img_path, str) and img_path:
                filename = PurePosixPath(img_path.replace("\\", "/")).name
                asset = assets_by_filename.get(filename)
                if asset is None:
                    # SVG 资产被有意丢弃。MinerU 若已抽出文字则保留文字块；
                    # 没有任何内容的纯 SVG 块没有预览或检索价值，直接跳过。
                    if filename in discarded_image_filenames:
                        if not content.strip():
                            continue
                    else:
                        raise RuntimeError(f"MinerU image is missing from ZIP: {img_path}")

            metadata = None
            analysis: dict[str, Any] | None = None
            if is_visual:
                metadata = {
                    "mineru_image_path": img_path,
                    "image_caption": item.get("image_caption", []),
                    "image_footnote": item.get("image_footnote", []),
                    "table_caption": item.get("table_caption", []),
                    "table_footnote": item.get("table_footnote", []),
                    "chart_caption": item.get("chart_caption", []),
                    "chart_footnote": item.get("chart_footnote", []),
                    "sub_type": item.get("sub_type"),
                    "asset": asset,
                }
                if isinstance(img_path, str) and img_path:
                    filename = PurePosixPath(img_path.replace("\\", "/")).name
                    analysis = visual_analysis_by_filename.get(filename)
                    if analysis is not None:
                        metadata["ai_visual_analysis"] = analysis

            # MinerU 的专业正文和 caption 始终优先。只有视觉块完全没有
            # 原始文字时，才用视觉模型的一句话描述生成可检索语义内容。
            if not content.strip() and analysis is not None:
                content = RegulationParseBlockBuilder._build_visual_fallback_content(analysis)

            page_index = item.get("page_idx")
            page_number = page_index + 1 if isinstance(page_index, int) else None
            char_start = cursor
            char_end = char_start + len(content)
            cursor = char_end + 2
            bbox = item.get("bbox")

            parse_blocks.append(
                RegulationParseBlock(
                    regulation_id=regulation_id,
                    block_index=len(parse_blocks),
                    block_type=block_type,
                    content=content,
                    page_number=page_number,
                    bbox=bbox if isinstance(bbox, list) else None,
                    text_level=(
                        item.get("text_level") if isinstance(item.get("text_level"), int) else None
                    ),
                    char_start=char_start,
                    char_end=char_end,
                    block_metadata=metadata,
                )
            )

        return parse_blocks

    @staticmethod
    def _build_visual_fallback_content(
        analysis: dict[str, Any],
    ) -> str:
        """图片块正文只保存视觉模型返回的客观描述。"""
        description = analysis.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
        return ""
