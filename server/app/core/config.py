from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MinerUCloudSettings(BaseSettings):
    MINERU_PROVIDER: Literal["local", "cloud"] = "local"
    MINERU_CLOUD_API_BASE_URL: str = "https://mineru.net"
    MINERU_CLOUD_API_TOKEN: SecretStr = SecretStr("")
    MINERU_CLOUD_MODEL_VERSION: Literal["pipeline", "vlm"] = "vlm"
    MINERU_CLOUD_LANGUAGE: str = "ch"

    @field_validator("MINERU_CLOUD_API_BASE_URL")
    @classmethod
    def validate_mineru_cloud_api_base_url(cls, value: str) -> str:
        origin = value.strip().rstrip("/")
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MinerU cloud API base URL must be an HTTPS origin")
        return origin

    @model_validator(mode="after")
    def validate_mineru_configuration(self) -> "MinerUCloudSettings":
        if (
            self.MINERU_PROVIDER == "cloud"
            and not self.MINERU_CLOUD_API_TOKEN.get_secret_value().strip()
        ):
            raise ValueError("MINERU_CLOUD_API_TOKEN is required for cloud MinerU")
        return self


class Settings(MinerUCloudSettings):
    """从环境变量和本地 .env 加载应用配置；敏感值使用 SecretStr。"""

    APP_NAME: str = "AuditMind AI"
    # 同时保留 stdout JSON 日志，并将应用日志写入轮转文件，供本地运行的
    # FastAPI 被容器内的 Grafana Alloy 采集。生产容器可设置为空字符串，
    # 只通过 Docker stdout 收集，避免重复写入 Loki。
    LOG_FILE_PATH: str = "logs/auditmind.jsonl"
    LOG_FILE_MAX_BYTES: int = Field(
        default=20 * 1024 * 1024,
        ge=1024 * 1024,
        le=1024 * 1024 * 1024,
    )
    LOG_FILE_BACKUP_COUNT: int = Field(default=5, ge=1, le=100)

    # 未显式配置时必须按生产环境处理，防止开发辅助能力失败开放。
    ENVIRONMENT: str = "production"

    # 浏览器客户端只能从明确列出的来源访问 API。空列表表示不开放
    # 跨域访问，适合通过同源反向代理部署的生产环境。
    CORS_ALLOWED_ORIGINS: list[str] = Field(default_factory=list)

    # Public readiness probes must fail within a small, bounded interval and
    # coalesce bursts instead of fanning out to every dependency per request.
    HEALTH_CHECK_TIMEOUT_SECONDS: float = Field(default=2.0, ge=0.1, le=10.0)
    HEALTH_CACHE_TTL_SECONDS: float = Field(default=2.0, ge=0.0, le=30.0)

    DATABASE_URL: str

    REDIS_URL: str
    # Redis 承担分布式锁，连接或读写不能无限等待。网络半开时必须在有限时间
    # 内抛出异常，让调用方保留原状态并由锁 TTL 完成最终恢复。
    REDIS_CONNECT_TIMEOUT_SECONDS: float = Field(default=3.0, ge=0.1, le=30.0)
    REDIS_SOCKET_TIMEOUT_SECONDS: float = Field(default=5.0, ge=0.1, le=60.0)
    REDIS_HEALTH_CHECK_INTERVAL_SECONDS: int = Field(default=30, ge=1, le=300)

    # Dramatiq 的 TimeLimit 使用毫秒，但配置文件统一使用秒。完整流水线
    # 还包含 MinerU 之后的分块、索引和模型调用，因此必须长于内部等待超时。
    DRAMATIQ_AUDIT_PIPELINE_TIME_LIMIT_SECONDS: int = Field(
        default=7200,
        ge=600,
        le=172800,
    )
    DRAMATIQ_REGULATION_PIPELINE_TIME_LIMIT_SECONDS: int = Field(
        default=7200,
        ge=600,
        le=172800,
    )

    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: SecretStr
    MINIO_BUCKET: str = "auditmind-documents"
    MINIO_SECURE: bool = False
    MINIO_CONNECT_TIMEOUT_SECONDS: int = 5
    MINIO_READ_TIMEOUT_SECONDS: int = 120
    MINIO_HEALTH_TIMEOUT_SECONDS: float = Field(default=2.0, ge=0.1, le=10.0)

    ELASTICSEARCH_URL: str
    # FastAPI 使用最小权限 API Key；elastic 管理员密码只属于基础设施。
    ELASTICSEARCH_API_KEY: SecretStr
    # 法规全文 Chunk 用于知识问答和原文搜索。
    ELASTICSEARCH_REGULATION_CHUNK_INDEX: str = "auditmind-regulation-chunks-v2"
    # 原子规则使用独立索引供逐页审计直接召回，避免先命中大段 Chunk
    # 再间接映射规则造成漏召回和候选噪声。
    # 规则索引与法规 Chunk 索引一样显式使用版本名；重建后更新配置并重启。
    ELASTICSEARCH_REGULATION_RULE_INDEX: str = "auditmind-regulation-rules-v1"

    MINERU_BASE_URL: str = "http://127.0.0.1:8000"
    # 状态查询需要快速失败；大文件上传和结果下载只限制连接建立及连续
    # 无响应时间，不能用一个短 total 覆盖整个流式传输。
    MINERU_CONNECT_TIMEOUT_SECONDS: float = Field(default=10.0, ge=0.1, le=60.0)
    MINERU_STATUS_TIMEOUT_SECONDS: float = Field(default=30.0, ge=1.0, le=300.0)
    MINERU_STREAM_IDLE_TIMEOUT_SECONDS: float = Field(default=120.0, ge=1.0, le=1800.0)
    # 兼容尚未完成配置迁移的部署；该旧总超时不再用于任何请求。
    MINERU_TIMEOUT_SECONDS: float | None = Field(default=None, ge=1.0)
    # high 模式才会真正启用 hybrid 的图片/图表分析；公式和表格保持开启。
    MINERU_BACKEND: Literal[
        "pipeline",
        "vlm-engine",
        "hybrid-engine",
        "vlm-http-client",
        "hybrid-http-client",
    ] = "hybrid-http-client"
    # MinerU API 在容器中运行时，通过宿主机地址访问本地模型服务；
    # 生产环境替换成 GPU 机器的内网 IP 即可。
    MINERU_SERVER_URL: str = "http://host.docker.internal:30000"
    MINERU_EFFORT: Literal["medium", "high"] = "high"
    MINERU_PARSE_METHOD: Literal["auto", "txt", "ocr"] = "auto"
    MINERU_FORMULA_ENABLE: bool = True
    MINERU_TABLE_ENABLE: bool = True
    MINERU_IMAGE_ANALYSIS: bool = True
    # MinerU ZIP 下载大小和解压后内容的双重限制，避免异常结果耗尽磁盘或内存。
    MINERU_MAX_RESULT_ARCHIVE_SIZE: int = 500 * 1024 * 1024
    MINERU_MAX_RESULT_UNCOMPRESSED_SIZE: int = 1024 * 1024 * 1024
    MINERU_MAX_RESULT_IMAGE_SIZE: int = 20 * 1024 * 1024
    MINERU_MAX_RESULT_IMAGES: int = 1000
    # 同一文档同步 MinerU 结果时使用 Redis 租约，后台会自动续租。
    DOCUMENT_PARSE_SYNC_LOCK_TTL_SECONDS: int = 300
    # 文档在登记 MinerU task_id 或完成 Markdown 解析前崩溃时，允许后续
    # 流水线接管没有外部任务可恢复的孤儿 PARSING 状态。
    DOCUMENT_PARSE_STALE_SECONDS: int = Field(default=3600, ge=60, le=86400)
    # 法规处理和删除统一使用一把总锁；后台轮询 MinerU，并串联 Chunk、
    # 索引和规则构建。
    # 超时只结束本轮后台任务，不把仍在 MinerU 处理的法规标记为失败；
    # 再次调用统一接口会从当前状态继续。
    REGULATION_PIPELINE_LOCK_TTL_SECONDS: int = 300
    # 全量规则索引重建可能明显长于单条法规流水线，使用独立最大租约时间。
    REGULATION_RULE_INDEX_MAINTENANCE_MAX_HOLD_SECONDS: int = Field(
        default=86400,
        ge=300,
        le=604800,
    )
    ASSISTANT_CONVERSATION_LOCK_TTL_SECONDS: int = Field(default=180, ge=3, le=3600)
    ASSISTANT_CONVERSATION_CACHE_TTL_SECONDS: int = Field(default=60, ge=1, le=3600)
    ASSISTANT_TURN_TIMEOUT_SECONDS: int = Field(default=600, ge=30, le=1800)
    ASSISTANT_AGENT_MAX_MODEL_CALLS: int = Field(default=8, ge=1, le=32)
    ASSISTANT_AGENT_MAX_TOOL_CALLS: int = Field(default=12, ge=1, le=64)
    ASSISTANT_AGENT_ACTION_TTL_SECONDS: int = Field(default=900, ge=60, le=86400)
    ASSISTANT_AGENT_TOOL_RESULT_MAX_CHARACTERS: int = Field(default=20_000, ge=1_000, le=100_000)
    ASSISTANT_AGENT_MAX_CONCURRENT_RUNS: int = Field(default=8, ge=1, le=8)  # checkpoint 池为 10
    REGULATION_PIPELINE_POLL_INTERVAL_SECONDS: float = Field(
        default=2.0,
        ge=0.1,
        le=60.0,
    )
    REGULATION_PIPELINE_WAIT_TIMEOUT_SECONDS: int = Field(
        default=3600,
        ge=1,
        le=86400,
    )
    # XXL-Job 等内部调度器调用维护接口时使用；为空表示禁用维护接口。
    SCHEDULER_ACCESS_TOKEN: SecretStr = SecretStr("")
    # 后台进程异常退出后，各阶段超过以下时间可由维护任务标记为 FAILED。
    REGULATION_PARSE_STALE_SECONDS: int = Field(default=7200, ge=60, le=86400)
    REGULATION_CHUNK_STALE_SECONDS: int = Field(default=1800, ge=60, le=86400)
    REGULATION_RULE_STALE_SECONDS: int = Field(default=7200, ge=60, le=86400)

    JWT_SECRET_KEY: SecretStr = Field(min_length=32)
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    JWT_ISSUER: str = "audit-mind"
    JWT_AUDIENCE: str = "audit-mind-api"

    # default 30 minutes
    JWT_EXPIRATION_DELTA: int = 30
    # Refresh token 使用 HttpOnly Cookie，Redis 只保存轮换后的摘要。
    JWT_REFRESH_EXPIRATION_DAYS: int = Field(default=7, ge=1, le=90)
    AUTH_REFRESH_COOKIE_NAME: str = Field(
        default="auditmind-refresh-token",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    AUTH_COOKIE_SECURE: bool = True
    AUTH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    AI_BASE_URL: str
    AI_API_KEY: SecretStr
    AI_MODEL: str
    AI_TIMEOUT_SECONDS: int = 120
    AI_MAX_RETRIES: int = 2

    # 安全分类与查询理解可使用更快、更便宜的独立模型。任一配置留空时，
    # 对应项回退到主文本模型，便于本地开发和渐进式上线。
    AI_GUARD_BASE_URL: str = ""
    AI_GUARD_API_KEY: SecretStr = SecretStr("")
    AI_GUARD_MODEL: str = ""
    AI_GUARD_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)
    AI_QUERY_REWRITE_BASE_URL: str = ""
    AI_QUERY_REWRITE_API_KEY: SecretStr = SecretStr("")
    AI_QUERY_REWRITE_MODEL: str = ""
    AI_QUERY_REWRITE_TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=180)

    # Rerank 是可选的检索质量增强能力。所有 Provider（包括第一方百炼）
    # 都通过 ``auditmind.rerankers`` entry point 注册，核心不识别供应商名称。
    AI_RERANK_PROVIDER: str = ""
    AI_RERANK_URL: str = ""
    AI_RERANK_API_KEY: SecretStr = SecretStr("")
    AI_RERANK_MODEL: str = ""
    AI_RERANK_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)
    AI_RERANK_CANDIDATE_COUNT: int = Field(default=30, ge=1, le=100)
    AI_RERANK_TOP_N: int = Field(default=10, ge=1, le=50)
    AI_RERANK_OPTIONS: dict[str, Any] = Field(default_factory=dict)

    # 多模态视觉模型可以使用与文本模型不同的服务商。
    # 三项任一为空时关闭视觉补充，不影响 MinerU 主解析流程。
    AI_VISION_MODEL_BASE_URL: str = ""
    AI_VISION_API_KEY: SecretStr = SecretStr("")
    AI_VISION_MODEL: str = ""
    AI_VISION_MAX_IMAGE_SIZE: int = 10 * 1024 * 1024

    # Embedding 模型可以独立于文本、视觉模型配置。
    # 配置为空时应用仍可启动，但调用向量功能会明确报错。
    AI_EMBEDDING_BASE_URL: str = ""
    AI_EMBEDDING_API_KEY: SecretStr = SecretStr("")
    AI_EMBEDDING_MODEL: str = ""

    # 必须与模型真实输出维度一致，后续 ES dense_vector mapping 会使用它。
    AI_EMBEDDING_DIMENSIONS: int = Field(
        default=1024,
        ge=1,
        le=4096,
    )

    # LangChain 每批发送给 Embedding 服务的文本数量。
    AI_EMBEDDING_BATCH_SIZE: int = Field(
        default=32,
        ge=1,
        le=256,
    )

    # 索引任务超过该时间仍为 PROCESSING 时，视为进程异常退出，允许重试。
    REGULATION_INDEX_STALE_SECONDS: int = Field(
        default=3600,
        ge=60,
        le=86400,
    )

    # 知识库文件上传最大size
    REGULATION_MAX_FILE_SIZE: int = 100 * 1024 * 1024
    # 文本知识直接进入规则流水线；限制字符数避免单次请求占用过多内存。
    REGULATION_MAX_TEXT_LENGTH: int = 500_000
    # 用户端上传最大size
    DOCUMENT_MAX_FILE_SIZE: int = 100 * 1024 * 1024
    # 除显式大文件上传接口外，所有 HTTP 请求体都在框架解析前受此限制。
    # 较小默认值可阻止超大 JSON/Form 被完整读入内存。
    REQUEST_BODY_MAX_BYTES: int = Field(
        default=10 * 1024 * 1024,
        ge=64 * 1024,
        le=100 * 1024 * 1024,
    )
    # Markdown 会直接进入内存解析，必须使用独立且更严格的上限，不能沿用
    # 适合流式 PDF 上传的 100 MB 配置。
    AUDIT_MARKDOWN_MAX_BYTES: int = Field(
        default=5 * 1024 * 1024,
        ge=1,
        le=20 * 1024 * 1024,
    )

    # 进程异常退出后，超过该时间的 RUNNING 审核允许重新领取。
    AUDIT_TASK_STALE_SECONDS: int = Field(
        default=3600,
        ge=60,
        le=86400,
    )
    # 审计总流水线持有自动续租锁，并在后台轮询 MinerU 状态。
    AUDIT_PIPELINE_LOCK_TTL_SECONDS: int = Field(default=300, ge=3, le=3600)
    AUDIT_PIPELINE_POLL_INTERVAL_SECONDS: float = Field(default=2.0, ge=0.1, le=60.0)
    AUDIT_PIPELINE_WAIT_TIMEOUT_SECONDS: int = Field(default=3600, ge=1, le=86400)
    AUDIT_PAGE_STALE_SECONDS: int = Field(default=600, ge=60, le=86400)

    @field_validator("CORS_ALLOWED_ORIGINS")
    @classmethod
    def validate_cors_allowed_origins(
        cls,
        origins: list[str],
    ) -> list[str]:
        """规范化可信来源并拒绝通配符或包含路径的配置。"""
        normalized: list[str] = []

        for raw_origin in origins:
            origin = raw_origin.strip().rstrip("/")
            parsed = urlsplit(origin)

            if origin == "*":
                raise ValueError("CORS wildcard origin is not allowed")

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be absolute HTTP(S) origins without a path")

            if origin not in normalized:
                normalized.append(origin)

        return normalized

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret_key(cls, secret: SecretStr) -> SecretStr:
        """拒绝长度合规但可预测的示例值和低多样性签名密钥。"""
        value = secret.get_secret_value()
        normalized = value.strip().lower()
        known_placeholders = {
            "change_me",
            "changeme",
            "replace-with-a-random-32-byte-secret",
            "your-jwt-secret-key-goes-here",
        }
        if normalized in known_placeholders or len(set(value)) < 8:
            raise ValueError("JWT secret must be a diverse, non-placeholder value")
        return secret

    @field_validator("SCHEDULER_ACCESS_TOKEN")
    @classmethod
    def validate_scheduler_access_token(cls, token: SecretStr) -> SecretStr:
        """允许留空禁用接口；启用时要求足够长的随机 Token。"""
        value = token.get_secret_value()
        if value and len(value) < 32:
            raise ValueError("scheduler access token must contain at least 32 characters")
        return token

    @model_validator(mode="after")
    def validate_optional_rerank_configuration(self) -> "Settings":
        """Validate only provider-neutral orchestration invariants."""
        provider = self.AI_RERANK_PROVIDER.strip()
        provider_values_configured = any(
            (
                self.AI_RERANK_URL.strip(),
                self.AI_RERANK_API_KEY.get_secret_value().strip(),
                self.AI_RERANK_MODEL.strip(),
                self.AI_RERANK_OPTIONS,
            )
        )
        if not provider and provider_values_configured:
            raise ValueError(
                "AI_RERANK_PROVIDER is required when reranker settings are configured"
            )
        if provider and self.AI_RERANK_TOP_N > self.AI_RERANK_CANDIDATE_COUNT:
            raise ValueError(
                "AI_RERANK_TOP_N must be less than or equal to "
                "AI_RERANK_CANDIDATE_COUNT"
            )
        return self

    @model_validator(mode="after")
    def validate_dramatiq_pipeline_time_limits(self) -> "Settings":
        """Worker 总时限必须给内部轮询结束和状态落库预留缓冲时间。"""
        minimum_audit_limit = self.AUDIT_PIPELINE_WAIT_TIMEOUT_SECONDS + 300
        if self.DRAMATIQ_AUDIT_PIPELINE_TIME_LIMIT_SECONDS < minimum_audit_limit:
            raise ValueError(
                "DRAMATIQ_AUDIT_PIPELINE_TIME_LIMIT_SECONDS must be at least "
                "AUDIT_PIPELINE_WAIT_TIMEOUT_SECONDS + 300"
            )

        minimum_regulation_limit = self.REGULATION_PIPELINE_WAIT_TIMEOUT_SECONDS + 300
        if self.DRAMATIQ_REGULATION_PIPELINE_TIME_LIMIT_SECONDS < minimum_regulation_limit:
            raise ValueError(
                "DRAMATIQ_REGULATION_PIPELINE_TIME_LIMIT_SECONDS must be at least "
                "REGULATION_PIPELINE_WAIT_TIMEOUT_SECONDS + 300"
            )
        return self

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


@lru_cache
def get_settings() -> Settings:
    """每个进程只解析一次配置，供客户端和依赖复用同一份设置。"""
    # 必填字段由 pydantic-settings 在运行时从环境读取，Pyright 无法静态感知。
    return Settings()  # pyright: ignore[reportCallIssue]
