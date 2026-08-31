"""从 PostgreSQL 重建独立的法规规则 Elasticsearch 索引。

这个脚本用于首次启用规则级检索，或者规则索引的 Mapping、Embedding 模型、
向量维度发生不兼容变化时进行迁移。审计召回的目标是 RegulationRule，而不是
法规全文 Chunk，因此该索引与 ``auditmind-regulation-chunks-*`` 完全独立。

脚本遵守以下安全原则：

1. PostgreSQL 是规则事实数据源，脚本不会从旧 ES 索引复制数据。
2. 目标物理索引必须不存在，防止误覆盖当前线上索引。
3. 只重建 enabled 且 rule_status=READY 的法规。
4. 每条 RegulationRule 生成一个 ES 文档和一个 Embedding。
5. 全部写入后核对 PostgreSQL 与新索引的规则数量。
6. 脚本不修改 regulation.rule_status，不污染正常业务任务状态。
7. 全程持有 Redis 维护租约，避免多个重建进程并发执行，并暂停新审计任务。

首次构建示例：

    uv run python scripts/rebuild_regulation_rule_index.py \
        --target-index auditmind-regulation-rules-v1

以后升级 Mapping 时创建新版本：

    uv run python scripts/rebuild_regulation_rule_index.py \
        --target-index auditmind-regulation-rules-v2

构建成功后，把 ``ELASTICSEARCH_REGULATION_RULE_INDEX`` 更新为新版本并重启应用。
"""

from __future__ import annotations

# 该脚本必须先把 server 根目录加入 sys.path，随后才能导入 app 包。
# ruff: noqa: E402
import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.embedding import get_embedding_service
from app.core.config import get_settings
from app.infrastructure.db.engine import engine
from app.infrastructure.db.session import async_session_factory
from app.infrastructure.es_client import es_client
from app.infrastructure.redis_client import redis_client
from app.infrastructure.redis_lock import run_with_lease_guard
from app.infrastructure.regulation_pipeline_lock import (
    acquire_regulation_rule_index_maintenance_lease,
)
from app.infrastructure.regulation_rule_vector_store import RegulationRuleVectorStore
from app.models.regulation import Regulation, RegulationRuleStatus
from app.repositories.regulation_rule_repository import RegulationRuleRepository
from app.services.regulation_rule_index_service import RegulationRuleIndexService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild a versioned RegulationRule index from PostgreSQL.",
    )
    parser.add_argument(
        "--target-index",
        required=True,
        help="A new physical index name, for example auditmind-regulation-rules-v2.",
    )
    return parser.parse_args()


async def load_indexable_regulations() -> list[Regulation]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Regulation)
            .where(
                Regulation.enabled.is_(True),
                Regulation.rule_status == RegulationRuleStatus.READY,
            )
            .order_by(Regulation.id)
        )
        return list(result.scalars().all())


async def load_rules(regulation_id) -> list:
    async with async_session_factory() as session:
        return await RegulationRuleRepository(session).find_by_regulation(regulation_id)


async def ensure_target_is_new(target_index: str) -> None:
    if await es_client.client.indices.exists(index=target_index):
        raise RuntimeError(
            f"target index already exists: {target_index}. Choose a new versioned name."
        )


async def rebuild(*, target_index: str) -> None:
    settings = get_settings()
    target_index = target_index.strip()
    if not target_index:
        raise ValueError("target index must not be blank")

    await ensure_target_is_new(target_index)
    regulations = await load_indexable_regulations()
    if not regulations:
        raise RuntimeError("no enabled regulations with READY rules were found")

    store = RegulationRuleVectorStore(
        client=es_client.client,
        index_name=target_index,
        dimensions=settings.AI_EMBEDDING_DIMENSIONS,
    )
    service = RegulationRuleIndexService(
        embedding=get_embedding_service(),
        vector_store=store,
    )
    await store.ensure_index()

    expected_count = 0
    for number, regulation in enumerate(regulations, start=1):
        rules = await load_rules(regulation.id)
        if not rules:
            raise RuntimeError(f"regulation {regulation.id} is READY but has no rules")
        documents = await service.build_documents(regulation=regulation, rules=rules)
        await service.replace_regulation_rules(
            regulation_id=regulation.id,
            documents=documents,
        )
        expected_count += len(documents)
        print(
            f"[{number}/{len(regulations)}] indexed regulation "
            f"{regulation.id}: {len(rules)} rules"
        )

    await es_client.client.indices.refresh(index=target_index)
    actual_count = (await es_client.client.count(index=target_index)).get("count", -1)
    if actual_count != expected_count:
        raise RuntimeError(
            f"index verification failed: expected {expected_count}, got {actual_count}"
        )
    print(f"Verified {actual_count} rules in {target_index}.")


async def async_main() -> int:
    args = parse_args()
    try:
        # 重建期间阻止新审计读取正在维护的规则查询副本。租约丢失会取消旧
        # 重建执行者，避免多个运维进程同时维护规则索引。
        async with acquire_regulation_rule_index_maintenance_lease() as lease:
            if lease is None:
                raise RuntimeError("regulation rule index maintenance is already running")
            await run_with_lease_guard(
                lease,
                rebuild(target_index=args.target_index),
            )
    except Exception as exc:
        print(f"Regulation rule index rebuild failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await redis_client.close()
        await es_client.close()
        await engine.dispose()
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
