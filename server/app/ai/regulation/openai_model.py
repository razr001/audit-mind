import openai
from langextract.providers.openai import OpenAILanguageModel


class AuditMindOpenAILanguageModel(OpenAILanguageModel):
    """为 LangExtract 的 OpenAI Provider 补充传输层超时与重试配置。

    LangExtract 1.6 尚未通过 Provider 构造器暴露 OpenAI 客户端超时设置。
    兼容代码集中在本类中，未来官方开放客户端配置后可整体移除。
    """

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        super().__init__(
            model_id=model_id,
            api_key=api_key,
            base_url=base_url,
        )
        # 父类已经创建默认客户端；替换它之前保留引用并及时关闭，避免
        # 构造每个抽取器时泄漏一个 HTTP 连接池。
        default_client = self._client
        try:
            self._client = openai.OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        finally:
            default_client.close()

    def close(self) -> None:
        self._client.close()
