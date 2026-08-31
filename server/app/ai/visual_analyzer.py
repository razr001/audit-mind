import base64
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.ai.model import create_vision_model
from app.core.config import get_settings

settings = get_settings()


class RegulationVisualAnalyzer:
    """使用多模态模型分析 MinerU 截取出的法规局部图片。"""

    def __init__(self, model: BaseChatModel) -> None:
        # 视觉模型只补充图片语义，不重复 MinerU 已完成的 OCR、公式和
        # 表格解析，因此无需让模型生成容易截断的结构化 JSON。
        self.model = model

    async def analyze(
        self,
        *,
        image_data: bytes,
        content_type: str,
        nearby_text: str,
    ) -> str:
        """结合图片附近原文生成一句客观的图片描述。"""
        if not image_data:
            raise ValueError("image data must not be empty")

        if len(image_data) > settings.AI_VISION_MAX_IMAGE_SIZE:
            raise ValueError("image is too large for visual analysis")

        if not content_type.startswith("image/"):
            raise ValueError("unsupported visual content type")

        encoded_image = base64.b64encode(image_data).decode("ascii")
        image_url = f"data:{content_type};base64,{encoded_image}"

        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "请用一句不超过 300 字的中文，客观描述这张图片"
                        "表达的主要内容。\n"
                        "不要输出 JSON、Markdown、字段名或项目列表。\n"
                        "不要重复图片附近原文，不要推测无法确认的信息。\n"
                        "如果无法识别，直接返回：无法确认图片内容。\n"
                        f"图片附近原文：\n{nearby_text[:400]}"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                    },
                },
            ],
        )

        result = await self.model.ainvoke([message])
        description = self._extract_text(result.content)
        if not description:
            raise RuntimeError("visual model returned an empty description")

        return description

    @staticmethod
    def _extract_text(content: object) -> str:
        """兼容模型返回纯字符串或 LangChain 标准文本内容块。"""
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()


@lru_cache
def get_regulation_visual_analyzer() -> RegulationVisualAnalyzer | None:
    """配置完整时创建视觉客户端，否则关闭可选的视觉补充能力。"""
    if not (
        settings.AI_VISION_MODEL_BASE_URL.strip()
        and settings.AI_VISION_API_KEY.get_secret_value().strip()
        and settings.AI_VISION_MODEL.strip()
    ):
        return None
    return RegulationVisualAnalyzer(create_vision_model())
