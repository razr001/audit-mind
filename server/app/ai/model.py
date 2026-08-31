from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import get_settings


def create_chat_model() -> ChatOpenAI:
    """创建文本审核使用的语言模型。"""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.AI_MODEL,
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=settings.AI_TIMEOUT_SECONDS,
        max_retries=settings.AI_MAX_RETRIES,
        temperature=0.1,
    )


def create_guard_model() -> ChatOpenAI:
    """创建无工具权限、低随机性的输入/上下文/输出安全分类模型。"""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.AI_GUARD_MODEL or settings.AI_MODEL,
        api_key=(
            settings.AI_GUARD_API_KEY
            if settings.AI_GUARD_API_KEY.get_secret_value()
            else settings.AI_API_KEY
        ),
        base_url=settings.AI_GUARD_BASE_URL or settings.AI_BASE_URL,
        timeout=settings.AI_GUARD_TIMEOUT_SECONDS,
        max_retries=settings.AI_MAX_RETRIES,
        temperature=0,
    )


def create_query_rewrite_model() -> ChatOpenAI:
    """创建只负责意图识别、指代消解和检索语句生成的模型。"""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.AI_QUERY_REWRITE_MODEL or settings.AI_MODEL,
        api_key=(
            settings.AI_QUERY_REWRITE_API_KEY
            if settings.AI_QUERY_REWRITE_API_KEY.get_secret_value()
            else settings.AI_API_KEY
        ),
        base_url=settings.AI_QUERY_REWRITE_BASE_URL or settings.AI_BASE_URL,
        timeout=settings.AI_QUERY_REWRITE_TIMEOUT_SECONDS,
        max_retries=settings.AI_MAX_RETRIES,
        temperature=0,
    )


def create_vision_model() -> ChatOpenAI:
    """创建法规图片分析使用的多模态模型。"""
    settings = get_settings()
    return ChatOpenAI(
        model=settings.AI_VISION_MODEL,
        api_key=settings.AI_VISION_API_KEY,
        base_url=settings.AI_VISION_MODEL_BASE_URL,
        timeout=settings.AI_TIMEOUT_SECONDS,
        max_retries=settings.AI_MAX_RETRIES,
        temperature=0,
    )


@lru_cache
def get_chat_model() -> ChatOpenAI:
    """按需创建并复用主文本模型，避免模块导入触发 AI 客户端初始化。"""
    return create_chat_model()


@lru_cache
def get_guard_model() -> ChatOpenAI:
    """按需创建并复用护栏模型；配置错误只影响调用护栏的业务。"""
    return create_guard_model()


@lru_cache
def get_query_rewrite_model() -> ChatOpenAI:
    """按需创建并复用查询理解模型。"""
    return create_query_rewrite_model()
