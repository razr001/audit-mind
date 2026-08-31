import re
from dataclasses import dataclass
from uuid import UUID

from app.models.regulation_chunk import RegulationChunk
from app.models.regulation_parse_block import RegulationParseBlock

REGULATION_CHUNK_TARGET_SIZE = 1000
REGULATION_CHUNK_OVERLAP_SIZE = 150
REGULATION_CHUNK_HARD_SIZE = 4000
REGULATION_CHUNK_IGNORED_BLOCK_TYPES = {"header", "footer", "page_number"}


@dataclass(frozen=True)
class _SemanticBlock:
    block: RegulationParseBlock
    char_start: int
    char_end: int


@dataclass(frozen=True)
class _ChunkUnit:
    char_start: int
    char_end: int
    blocks: tuple[_SemanticBlock, ...]
    context_heading: str | None = None


class RegulationChunkBuilder:
    """Build searchable chunks while preserving exact source-block mappings."""

    @classmethod
    def _build_chunks(
        cls,
        *,
        regulation_id: UUID,
        blocks: list[RegulationParseBlock],
        target_size: int = REGULATION_CHUNK_TARGET_SIZE,
        overlap_size: int = REGULATION_CHUNK_OVERLAP_SIZE,
    ) -> list[RegulationChunk]:
        cls._validate_sizes(target_size=target_size, overlap_size=overlap_size)
        semantic_blocks, semantic_source_text = cls._build_semantic_source(blocks)
        if not semantic_blocks:
            return []
        units = cls._build_units(
            semantic_blocks=semantic_blocks,
            target_size=target_size,
            overlap_size=overlap_size,
        )
        grouped_units = cls._group_units(
            units=units,
            target_size=target_size,
            overlap_size=overlap_size,
        )
        return [
            cls._to_chunk(
                regulation_id=regulation_id,
                grouped_units=group,
                semantic_source_text=semantic_source_text,
                chunk_index=index,
            )
            for index, group in enumerate(grouped_units)
        ]

    @staticmethod
    def _validate_sizes(*, target_size: int, overlap_size: int) -> None:
        if target_size <= 0:
            raise ValueError("target_size must be greater than 0")
        if overlap_size < 0 or overlap_size >= min(
            target_size,
            REGULATION_CHUNK_HARD_SIZE,
        ):
            raise ValueError("overlap_size must be between 0 and target_size")

    @staticmethod
    def _build_semantic_source(
        blocks: list[RegulationParseBlock],
    ) -> tuple[list[_SemanticBlock], str]:
        ordered_blocks = sorted(blocks, key=lambda block: block.block_index)
        raw_source_text = "\n\n".join(block.content for block in ordered_blocks)
        for block in ordered_blocks:
            if (
                block.char_start < 0
                or block.char_end < block.char_start
                or raw_source_text[block.char_start : block.char_end] != block.content
            ):
                raise RuntimeError("regulation parse block offsets do not match source text")

        semantic_blocks: list[_SemanticBlock] = []
        parts: list[str] = []
        cursor = 0
        for block in ordered_blocks:
            if (
                not block.content.strip()
                or block.block_type.lower() in REGULATION_CHUNK_IGNORED_BLOCK_TYPES
            ):
                continue
            if parts:
                cursor += 2
            parts.append(block.content)
            semantic_blocks.append(
                _SemanticBlock(
                    block=block,
                    char_start=cursor,
                    char_end=cursor + len(block.content),
                )
            )
            cursor += len(block.content)
        return semantic_blocks, "\n\n".join(parts)

    @classmethod
    def _build_units(
        cls,
        *,
        semantic_blocks: list[_SemanticBlock],
        target_size: int,
        overlap_size: int,
    ) -> list[_ChunkUnit]:
        units: list[_ChunkUnit] = []
        index = 0
        while index < len(semantic_blocks):
            unit_blocks = [semantic_blocks[index]]
            current = semantic_blocks[index]
            if current.block.content.rstrip().endswith((":", "：")):
                while index + 1 < len(semantic_blocks):
                    next_block = semantic_blocks[index + 1]
                    if not cls._continues_rule_list(next_block.block):
                        break
                    unit_blocks.append(next_block)
                    index += 1
            units.extend(
                cls._window_unit(
                    unit_blocks=unit_blocks,
                    target_size=target_size,
                    overlap_size=overlap_size,
                )
            )
            index += 1
        return units

    @staticmethod
    def _window_unit(
        *,
        unit_blocks: list[_SemanticBlock],
        target_size: int,
        overlap_size: int,
    ) -> list[_ChunkUnit]:
        unit_start = unit_blocks[0].char_start
        unit_end = unit_blocks[-1].char_end
        unit_size = unit_end - unit_start
        contains_table = any(item.block.block_type.lower() == "table" for item in unit_blocks)
        window_size: int | None = None
        context_heading: str | None = None
        if unit_size > REGULATION_CHUNK_HARD_SIZE and not contains_table:
            window_size = REGULATION_CHUNK_HARD_SIZE
            if len(unit_blocks) > 1:
                context_heading = unit_blocks[0].block.content
        elif len(unit_blocks) == 1 and unit_size > target_size and not contains_table:
            window_size = target_size
        if window_size is None:
            return [
                _ChunkUnit(
                    char_start=unit_start,
                    char_end=unit_end,
                    blocks=tuple(unit_blocks),
                )
            ]

        units: list[_ChunkUnit] = []
        step = window_size - overlap_size
        part_start = unit_start
        while part_start < unit_end:
            part_end = min(part_start + window_size, unit_end)
            units.append(
                _ChunkUnit(
                    char_start=part_start,
                    char_end=part_end,
                    blocks=tuple(
                        block
                        for block in unit_blocks
                        if block.char_start < part_end and block.char_end > part_start
                    ),
                    context_heading=context_heading,
                )
            )
            if part_end == unit_end:
                break
            part_start += step
        return units

    @classmethod
    def _group_units(
        cls,
        *,
        units: list[_ChunkUnit],
        target_size: int,
        overlap_size: int,
    ) -> list[list[_ChunkUnit]]:
        groups: list[list[_ChunkUnit]] = []
        current: list[_ChunkUnit] = []
        for unit in units:
            prospective = [*current, unit]
            size = prospective[-1].char_end - prospective[0].char_start
            if current and size > target_size:
                groups.append(current)
                current = cls._select_overlap_units(
                    units=current,
                    overlap_size=overlap_size,
                )
            current.append(unit)
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _to_chunk(
        *,
        regulation_id: UUID,
        grouped_units: list[_ChunkUnit],
        semantic_source_text: str,
        chunk_index: int,
    ) -> RegulationChunk:
        all_blocks = [block for unit in grouped_units for block in unit.blocks]
        chunk_blocks = list({item.block.block_index: item for item in all_blocks}.values())
        chunk_blocks.sort(key=lambda item: item.block.block_index)
        start = grouped_units[0].char_start
        end = grouped_units[-1].char_end
        pages = [
            item.block.page_number for item in chunk_blocks if item.block.page_number is not None
        ]
        source_segments = RegulationChunkBuilder._source_segments(
            blocks=chunk_blocks,
            start=start,
            end=end,
        )
        return RegulationChunk(
            regulation_id=regulation_id,
            chunk_index=chunk_index,
            article_number=None,
            chapter=None,
            page_number=pages[0] if pages else None,
            char_start=start,
            char_end=end,
            content=semantic_source_text[start:end],
            chunk_metadata={
                "blockIds": [str(item.block.id) for item in chunk_blocks],
                "blockIndexes": [item.block.block_index for item in chunk_blocks],
                "blockTypes": list(dict.fromkeys(item.block.block_type for item in chunk_blocks)),
                "sourceSegments": source_segments,
                "pageStart": min(pages) if pages else None,
                "pageEnd": max(pages) if pages else None,
                "contextHeading": next(
                    (unit.context_heading for unit in grouped_units if unit.context_heading),
                    None,
                ),
            },
        )

    @staticmethod
    def _source_segments(
        *,
        blocks: list[_SemanticBlock],
        start: int,
        end: int,
    ) -> list[dict[str, object]]:
        segments: list[dict[str, object]] = []
        for item in blocks:
            segment_start = max(start, item.char_start)
            segment_end = min(end, item.char_end)
            if segment_start >= segment_end:
                continue
            source_offset = segment_start - item.char_start
            segments.append(
                {
                    "blockId": str(item.block.id),
                    "blockIndex": item.block.block_index,
                    "blockType": item.block.block_type,
                    "pageNumber": item.block.page_number,
                    "chunkStart": segment_start - start,
                    "chunkEnd": segment_end - start,
                    "sourceStart": item.block.char_start + source_offset,
                    "sourceEnd": (
                        item.block.char_start + source_offset + segment_end - segment_start
                    ),
                }
            )
        return segments

    @staticmethod
    def _continues_rule_list(block: RegulationParseBlock) -> bool:
        if block.block_type.lower() in {"list", "table"}:
            return True
        return bool(re.match(r"^\s*\d+\s*[.、．)）]", block.content))

    @staticmethod
    def _select_overlap_units(
        *,
        units: list[_ChunkUnit],
        overlap_size: int,
    ) -> list[_ChunkUnit]:
        if overlap_size == 0:
            return []
        overlap: list[_ChunkUnit] = []
        for unit in reversed(units):
            candidate = [unit, *overlap]
            if candidate[-1].char_end - candidate[0].char_start > overlap_size:
                break
            overlap = candidate
        return overlap
