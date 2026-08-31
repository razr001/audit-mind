from app.ai.regulation.schemas import ExtractedComplianceRule
from app.models.regulation_chunk import RegulationChunk
from app.models.regulation_parse_block import RegulationParseBlock


class RegulationRuleSourceMapper:
    """Map extracted local spans back to exact persisted source intervals."""

    @staticmethod
    def _resolve_rule_source(
        *,
        chunk: RegulationChunk,
        blocks: list[RegulationParseBlock],
        local_start: int,
        local_end: int,
    ) -> (
        tuple[
            list[RegulationParseBlock],
            list[dict],
            int,
            int,
        ]
        | None
    ):
        """把 Chunk 局部区间映射回一个或多个原始 ParseBlock 区间。"""
        metadata = chunk.chunk_metadata or {}
        raw_segments = metadata.get("sourceSegments")
        blocks_by_id = {str(block.id): block for block in blocks}

        if isinstance(raw_segments, list):
            mapped_segments: list[dict] = []
            source_blocks: list[RegulationParseBlock] = []
            seen_block_ids: set[str] = set()
            for segment in raw_segments:
                if not isinstance(segment, dict):
                    return None
                try:
                    chunk_start = int(segment["chunkStart"])
                    chunk_end = int(segment["chunkEnd"])
                    source_start = int(segment["sourceStart"])
                    source_end = int(segment["sourceEnd"])
                    block_id = str(segment["blockId"])
                except (KeyError, TypeError, ValueError):
                    return None

                overlap_start = max(local_start, chunk_start)
                overlap_end = min(local_end, chunk_end)
                if overlap_start >= overlap_end:
                    continue

                block = blocks_by_id.get(block_id)
                if block is None:
                    return None
                mapped_start = source_start + overlap_start - chunk_start
                mapped_end = source_end - (chunk_end - overlap_end)
                if (
                    mapped_start < block.char_start
                    or mapped_end > block.char_end
                    or mapped_start >= mapped_end
                    or source_end - source_start != chunk_end - chunk_start
                ):
                    return None
                block_local_start = source_start - block.char_start
                block_local_end = source_end - block.char_start
                if (
                    chunk.content[chunk_start:chunk_end]
                    != block.content[block_local_start:block_local_end]
                ):
                    return None

                mapped_segments.append(
                    {
                        "blockId": block_id,
                        "pageNumber": block.page_number,
                        "sourceStart": mapped_start,
                        "sourceEnd": mapped_end,
                    }
                )
                if block_id not in seen_block_ids:
                    seen_block_ids.add(block_id)
                    source_blocks.append(block)

            if not mapped_segments:
                return None
            return (
                source_blocks,
                mapped_segments,
                min(item["sourceStart"] for item in mapped_segments),
                max(item["sourceEnd"] for item in mapped_segments),
            )

        # 兼容尚未重建的旧 Chunk：旧偏移直接基于完整 ParseBlock 全文。
        if chunk.char_start is None:
            return None
        global_start = chunk.char_start + local_start
        global_end = chunk.char_start + local_end
        source_blocks = [
            block
            for block in blocks
            if block.char_start < global_end and block.char_end > global_start
        ]
        if not source_blocks:
            return None
        legacy_segments = [
            {
                "blockId": str(block.id),
                "pageNumber": block.page_number,
                "sourceStart": max(global_start, block.char_start),
                "sourceEnd": min(global_end, block.char_end),
            }
            for block in source_blocks
            if max(global_start, block.char_start) < min(global_end, block.char_end)
        ]
        return (
            source_blocks,
            legacy_segments,
            global_start,
            global_end,
        )

    @staticmethod
    def _resolve_source_interval(
        *,
        chunk: RegulationChunk,
        blocks: list[RegulationParseBlock],
        extracted: ExtractedComplianceRule,
    ) -> tuple[int, int] | None:
        """把 LangExtract 结果安全地对齐回 Chunk 连续原文。

        跨页规则中间可能夹着 MinerU 识别出的页眉、页脚和页码。模型通常
        会自然忽略这些页面噪声，导致 extraction_text 不是原文的连续切片。
        这里仅允许跨过明确标记为页面噪声的 Block；任何普通正文、列表或
        表格都不能被跳过。返回区间仍覆盖中间噪声，因此数据库保存的
        source_text 继续是可定位、未经修改的 Chunk 原文。
        """
        assert chunk.char_start is not None
        normalized_extraction = "".join(extracted.content.split())
        if not normalized_extraction:
            return None

        # 常规路径无需构建映射，也覆盖 LangExtract 只合并换行的情况。
        direct_source = chunk.content[extracted.char_start : extracted.char_end]
        if "".join(direct_source.split()) == normalized_extraction:
            return extracted.char_start, extracted.char_end

        # 新 Chunk 已在构建阶段剔除页面噪声，若仍不能直接对齐，就表示
        # 模型省略或改写了语义正文，必须拒绝，不能再做宽松匹配。
        if isinstance(
            (chunk.chunk_metadata or {}).get("sourceSegments"),
            list,
        ):
            return None

        ignored_ranges: list[tuple[int, int]] = []
        ignored_types = {"header", "footer", "page_number"}
        chunk_global_end = chunk.char_start + len(chunk.content)
        for block in blocks:
            if block.block_type.lower() not in ignored_types:
                continue
            if block.char_end <= chunk.char_start or block.char_start >= chunk_global_end:
                continue
            ignored_ranges.append(
                (
                    max(0, block.char_start - chunk.char_start),
                    min(len(chunk.content), block.char_end - chunk.char_start),
                )
            )

        if not ignored_ranges:
            return None

        # cleaned_chars 的每个字符都保留其在原始 Chunk 中的位置。这样在
        # 清洗文本中命中后，可以准确恢复覆盖噪声的连续原文区间。
        ignored_positions = {
            position for start, end in ignored_ranges for position in range(start, end)
        }
        cleaned_chars: list[str] = []
        original_positions: list[int] = []
        for position, character in enumerate(chunk.content):
            if position in ignored_positions or character.isspace():
                continue
            cleaned_chars.append(character)
            original_positions.append(position)

        cleaned_text = "".join(cleaned_chars)
        matches: list[tuple[int, int]] = []
        search_from = 0
        while True:
            match_start = cleaned_text.find(
                normalized_extraction,
                search_from,
            )
            if match_start < 0:
                break
            match_end = match_start + len(normalized_extraction)
            matches.append(
                (
                    original_positions[match_start],
                    original_positions[match_end - 1] + 1,
                )
            )
            search_from = match_start + 1

        if not matches:
            return None

        # 同一句在 Chunk 内重复时，以 LangExtract 给出的原始位置为线索，
        # 选择距离最近的一处，避免把规则绑定到错误段落。
        return min(
            matches,
            key=lambda interval: abs(interval[0] - extracted.char_start),
        )
