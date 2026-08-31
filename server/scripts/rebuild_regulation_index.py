"""从 PostgreSQL 完整重建法规 Elasticsearch 向量索引。

这个脚本用于 Elasticsearch Mapping、分词器、Embedding 模型或向量维度
发生不兼容变化时的数据迁移。它不复制旧 Elasticsearch 数据，而是重新读取
PostgreSQL 中已经构建完成的 Regulation 和 RegulationChunk，使用当前应用代码
重新组织检索文本、生成 Embedding，并写入一个全新的物理索引。

为什么不直接修改旧索引：

1. Elasticsearch 不能原地修改字段类型、分词器或 dense_vector 维度。
2. 旧 ES 数据可能由旧版本业务逻辑生成，直接 _reindex 无法修复内容结构。
3. PostgreSQL 才是事实数据源，ES 只是可以随时重建的查询副本。
4. 新索引构建或验证失败时，旧索引必须继续提供服务，不能被提前删除。

脚本执行顺序：

1. 拒绝覆盖已经存在的目标索引，防止误删线上数据。
2. 创建目标索引，Mapping 完全复用 RegulationVectorStore 的当前实现。
3. 查询所有 enabled、解析 READY、Chunk READY 的法规。
4. 按法规读取全部 Chunk，使用当前 Embedding 配置重新生成向量并写入。
5. 比较 PostgreSQL Chunk 数量与 Elasticsearch 文档数量。
6. 如果传入 --alias，在验证成功后一次性把 Alias 切换到新索引。

脚本不会修改 regulation.index_status。该状态描述正常 API 索引任务，不应该被
一次运维迁移污染。脚本失败时也不会自动删除目标索引，方便检查失败现场；旧索引
和已有 Alias 始终保持不变。

示例：

    uv run python scripts/rebuild_regulation_index.py \
        --target-index auditmind-regulation-chunks-v3

构建并在成功后切换固定 Alias：

    uv run python scripts/rebuild_regulation_index.py \
        --target-index auditmind-regulation-chunks-v3 \
        --alias auditmind-regulation-chunks

使用 Alias 后，应用的 ELASTICSEARCH_REGULATION_CHUNK_INDEX 应配置为固定的
``auditmind-regulation-chunks``，以后只升级物理索引的 v3、v4 版本。
"""

from __future__ import annotations

# 该脚本必须先把 server 根目录加入 sys.path，随后才能导入 app 包。
# ruff: noqa: E402
import argparse
import asyncio
import sys
from pathlib import Path

# 直接执行 ``python scripts/xxx.py`` 时，sys.path 默认只有 scripts 目录。
# 显式加入 server 根目录，保证脚本在未把项目安装成包的空机器上也能导入 app。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from elasticsearch import NotFoundError
from sqlalchemy import select

from app.ai.embedding import get_embedding_service
from app.core.config import get_settings
from app.infrastructure.db.engine import engine
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.es_client import es_client
from app.infrastructure.regulation_vector_store import RegulationVectorStore
from app.models.regulation import (
	Regulation,
	RegulationChunkStatus,
	RegulationStatus,
)
from app.repositories.regulation_chunk_repository import (
	RegulationChunkRepository,
)
from app.services.regulation_index_service import RegulationIndexService


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Rebuild a new regulation index from PostgreSQL and optionally "
			"switch an Elasticsearch alias after verification."
		),
	)
	parser.add_argument(
		"--target-index",
		required=True,
		help=(
			"New physical index name, for example "
			"auditmind-regulation-chunks-v3. It must not already exist."
		),
	)
	parser.add_argument(
		"--alias",
		help=(
			"Optional stable alias to switch after a successful rebuild, "
			"for example auditmind-regulation-chunks."
		),
	)
	return parser.parse_args()


async def load_indexable_regulations() -> list[Regulation]:
	"""短事务读取可索引法规，Embedding 期间不占用数据库连接。"""
	async with async_session_factory() as session:
		result = await session.execute(
			select(Regulation)
			.where(
				Regulation.enabled.is_(True),
				Regulation.status == RegulationStatus.READY,
				Regulation.chunk_status == RegulationChunkStatus.READY,
			)
			.order_by(Regulation.id)
		)
		return list(result.scalars().all())


async def load_regulation_chunks(regulation_id):
	"""按法规读取 Chunk，返回后立即释放本次数据库 Session。"""
	async with async_session_factory() as session:
		repository = RegulationChunkRepository(session)
		return await repository.find_by_regulation(regulation_id)


async def ensure_target_is_new(target_index: str) -> None:
	"""目标索引必须不存在；迁移脚本绝不自动覆盖已有数据。"""
	if await es_client.client.indices.exists(index=target_index):
		raise RuntimeError(
			f"target index already exists: {target_index}. "
			"Choose a new versioned index name."
		)


async def switch_alias(*, alias: str, target_index: str) -> None:
	"""验证 Alias 没有同名物理索引后，原子切换到新索引。"""
	try:
		current = await es_client.client.indices.get_alias(name=alias)
	except NotFoundError:
		current = {}

	# Alias 名称不能与已经存在的物理索引重名。若当前项目仍把 v2 当作
	# 物理索引使用，应选择不带版本号的新 Alias 名称。
	if not current and await es_client.client.indices.exists(index=alias):
		raise RuntimeError(
			f"alias name is already used by a concrete index: {alias}"
		)

	actions = [
		{"remove": {"index": index_name, "alias": alias}}
		for index_name in current
	]
	actions.append(
		{
			"add": {
				"index": target_index,
				"alias": alias,
				"is_write_index": True,
			}
		}
	)
	await es_client.client.indices.update_aliases(actions=actions)


async def rebuild(*, target_index: str, alias: str | None) -> None:
	settings = get_settings()
	target_index = target_index.strip()
	alias = alias.strip() if alias else None

	if not target_index:
		raise ValueError("target index must not be blank")
	if alias == target_index:
		raise ValueError("alias and target index must use different names")

	await ensure_target_is_new(target_index)
	regulations = await load_indexable_regulations()
	if not regulations:
		raise RuntimeError(
			"no enabled READY regulations with READY chunks were found"
		)

	embedding = get_embedding_service()
	vector_store = RegulationVectorStore(
		client=es_client.client,
		index_name=target_index,
		dimensions=settings.AI_EMBEDDING_DIMENSIONS,
	)
	await vector_store.ensure_index()

	expected_count = 0
	for number, regulation in enumerate(regulations, start=1):
		chunks = await load_regulation_chunks(regulation.id)
		if not chunks:
			raise RuntimeError(
				f"regulation {regulation.id} is READY but has no chunks"
			)

		index_chunks = await RegulationIndexService._build_index_documents(
			embedding=embedding,
			regulation=regulation,
			chunks=chunks,
		)
		await vector_store.replace_regulation_chunks(
			regulation_id=str(regulation.id),
			chunks=index_chunks,
		)
		expected_count += len(index_chunks)
		print(
			f"[{number}/{len(regulations)}] indexed regulation "
			f"{regulation.id}: {len(chunks)} chunks"
		)

	await es_client.client.indices.refresh(index=target_index)
	actual_count = (
		await es_client.client.count(index=target_index)
	).get("count", -1)
	if actual_count != expected_count:
		raise RuntimeError(
			"index verification failed: "
			f"expected {expected_count} documents, got {actual_count}"
		)

	print(
		f"Verified {actual_count} documents in {target_index}."
	)
	if alias:
		await switch_alias(alias=alias, target_index=target_index)
		print(f"Alias {alias} now points to {target_index}.")
	else:
		print("Alias was not changed because --alias was not provided.")


async def async_main() -> int:
	args = parse_args()
	try:
		await rebuild(
			target_index=args.target_index,
			alias=args.alias,
		)
	except Exception as exc:
		print(f"Regulation index rebuild failed: {exc}", file=sys.stderr)
		return 1
	finally:
		await es_client.close()
		await engine.dispose()
	return 0


def main() -> int:
	return asyncio.run(async_main())


if __name__ == "__main__":
	raise SystemExit(main())
