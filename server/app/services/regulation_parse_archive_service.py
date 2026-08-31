import asyncio
import hashlib
import json
import zipfile
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

from app.ai.visual_analyzer import RegulationVisualAnalyzer
from app.core.regulation_failure import log_regulation_failure
from app.models import RegulationParseBlock
from app.services.regulation_parse_block_builder import RegulationParseBlockBuilder
from app.services.regulation_storage_service import RegulationStorageService


class RegulationParseArchiveService(RegulationParseBlockBuilder):
    """Read bounded MinerU archives and persist validated visual resources."""

    def __init__(
        self,
        *,
        storage: RegulationStorageService,
        visual_analyzer: RegulationVisualAnalyzer | None,
    ) -> None:
        self.storage = storage
        self.visual_analyzer = visual_analyzer

    async def build(
        self,
        *,
        regulation_id: UUID,
        parse_task_id: str,
        archive_file: BinaryIO,
    ) -> list[RegulationParseBlock]:
        """读取 MinerU ZIP、持久化局部图片，再构造按阅读顺序排列的块。"""
        try:
            with zipfile.ZipFile(archive_file) as archive:
                members = archive.infolist()
                self._validate_archive_members(members)

                content_members = [
                    member
                    for member in members
                    if self._normalized_member_name(member).endswith("_content_list.json")
                ]
                if len(content_members) != 1:
                    raise RuntimeError("MinerU ZIP must contain exactly one content_list")

                raw_content_list = await asyncio.to_thread(
                    archive.read,
                    content_members[0],
                )
                content_list = json.loads(raw_content_list.decode("utf-8-sig"))
                if not isinstance(content_list, list):
                    raise RuntimeError("MinerU content_list must be a list")

                assets_by_filename: dict[str, dict[str, Any]] = {}
                visual_analysis_by_filename: dict[str, dict[str, Any]] = {}
                discarded_image_filenames: set[str] = set()
                visual_targets = self._build_visual_targets(content_list)
                for member in members:
                    if not self._is_image_member(member):
                        continue

                    filename = PurePosixPath(self._normalized_member_name(member)).name

                    # 单张图片逐个读取和上传，内存峰值受单图大小限制，
                    # 不会把 ZIP 内全部图片同时放进内存。
                    data = await asyncio.to_thread(
                        archive.read,
                        member,
                    )
                    # SVG 不进入 MinIO、数据库和视觉模型。浏览器直接展示 SVG
                    # 会扩大攻击面；MinerU 已抽出的文字仍可在后面保留。
                    prepared_image = await asyncio.to_thread(
                        self._prepare_image,
                        data,
                    )
                    if prepared_image is None:
                        discarded_image_filenames.add(filename)
                        continue

                    data, suffix, content_type = prepared_image
                    content_hash = hashlib.sha256(data).hexdigest()
                    storage_key = await self.storage.upload_parse_asset(
                        regulation_id=regulation_id,
                        parse_task_id=parse_task_id,
                        content_hash=content_hash,
                        suffix=suffix,
                        content_type=content_type,
                        data=data,
                    )
                    assets_by_filename[filename] = {
                        "storage_key": storage_key,
                        "content_type": content_type,
                        "file_size": len(data),
                        "content_hash": content_hash,
                    }

                    target = visual_targets.get(filename)
                    if target is None:
                        continue

                    item_index, item = target
                    # MinerU 的专业图片/图表分析优先。只有它没有返回 content
                    # 时才调用通用视觉模型，且补充结果只写 metadata。
                    if self._has_mineru_visual_content(item):
                        continue
                    # 视觉配置为空表示主动关闭补充识别。图片仍正常上传并入库，
                    # MinerU 的主解析结果不因此失败。
                    if self.visual_analyzer is None:
                        continue

                    try:
                        analysis = await self.visual_analyzer.analyze(
                            image_data=data,
                            content_type=content_type,
                            nearby_text=self._build_nearby_text(
                                content_list=content_list,
                                item_index=item_index,
                            ),
                        )
                        # 对外继续保留 ai_visual_analysis.description 的既有
                        # 数据形状，避免数据库迁移和前端兼容性改造。
                        visual_analysis_by_filename[filename] = {
                            "description": analysis,
                        }
                    except Exception as exc:
                        # 视觉描述只是 MinerU 主结果的可选增强。模型不可用、
                        # 超时或返回空内容时记录异常类型并跳过当前图片，不能
                        # 因此阻断法规原文和局部图片的正常入库。
                        log_regulation_failure(
                            "regulation_visual_analysis_skipped",
                            regulation_id=regulation_id,
                            error=exc,
                        )
                        continue

                return self._build_parse_blocks(
                    regulation_id=regulation_id,
                    content_list=content_list,
                    assets_by_filename=assets_by_filename,
                    visual_analysis_by_filename=(visual_analysis_by_filename),
                    discarded_image_filenames=discarded_image_filenames,
                )
        except Exception as exc:
            if isinstance(exc, zipfile.BadZipFile):
                raise RuntimeError("MinerU returned an invalid ZIP") from exc
            raise
