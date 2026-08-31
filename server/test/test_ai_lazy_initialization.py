import importlib
from unittest.mock import patch


def test_chat_models_are_not_created_when_module_is_imported() -> None:
    """导入 API 模块不能因为外部 AI 客户端初始化失败而阻止应用启动。"""
    import app.ai.model as model_module

    with patch("langchain_openai.ChatOpenAI") as chat_model_class:
        reloaded = importlib.reload(model_module)

        chat_model_class.assert_not_called()

        first = reloaded.get_chat_model()
        second = reloaded.get_chat_model()

        assert first is second
        chat_model_class.assert_called_once()

    # 不把测试用 Mock 留在模块级缓存中，避免污染同进程中的后续测试。
    importlib.reload(reloaded)


def test_reranker_is_created_only_when_requested() -> None:
    """Reranker 插件加载失败应发生在检索业务调用时，而不是模块导入时。"""
    import app.ai.reranking.factory as factory_module

    with patch(
        "app.ai.reranking.registry.create_registered_reranker"
    ) as registered_factory:
        reloaded = importlib.reload(factory_module)

        registered_factory.assert_not_called()

    sentinel = object()
    with patch.object(reloaded, "create_reranker", return_value=sentinel) as create:
        reloaded.get_reranker.cache_clear()

        first = reloaded.get_reranker()
        second = reloaded.get_reranker()

        assert first is sentinel
        assert second is sentinel
        create.assert_called_once()

    importlib.reload(reloaded)
