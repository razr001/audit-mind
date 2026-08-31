import re

from app.ai.regulation_qa.schemas import GuardrailReason

# 输入护栏采用“确定性策略 + 模型分类”两层设计。以下表达具有明确的执行意图，
# 不应依赖语言模型的随机分类结果，否则同一句话可能一次放行、一次拦截。
_ACTION_PATTERN = re.compile(
    r"(?:帮我|请|替我|给我)?\s*"
    r"(?:写|编写|生成|创建|修改|调试|执行|运行|调用|请求|发送|删除|新增|更新|"
    r"上传|下载|部署|安装)"
)
_TECHNICAL_ARTIFACT_PATTERN = re.compile(
    r"(?:代码|脚本|程序|命令|SQL|Shell|PowerShell|Bash|curl|"
    r"HTTP\s*请求|API|接口|端点|endpoint|Python|Java(?:Script)?|TypeScript)",
    re.IGNORECASE,
)
_ENGLISH_ACTION_PATTERN = re.compile(
    r"\b(?:write|generate|create|modify|debug|execute|run|call|invoke|send|delete|"
    r"upload|download|deploy|install)\b",
    re.IGNORECASE,
)
_ENGLISH_ARTIFACT_PATTERN = re.compile(
    r"\b(?:code|script|command|sql|shell|powershell|bash|curl|http\s+request|api|"
    r"endpoint|python|java|javascript|typescript)\b",
    re.IGNORECASE,
)
_BARE_API_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_.{}-]+(?:/[A-Za-z0-9_.{}-]+)+/?$")


def detect_input_policy_violation(question: str) -> GuardrailReason | None:
    """识别确定属于法规问答能力边界外的指向性技术操作。

    只有“操作动词”和“技术执行对象”同时出现才拦截，避免把“法规是否要求
    API 进行身份验证”这类正常法规问题误判成接口调用请求。裸 API 路径属于
    前一操作请求的常见续问形式，也直接按不支持的操作处理。
    """

    normalized = question.strip()
    if not normalized:
        return None
    if _BARE_API_PATH_PATTERN.fullmatch(normalized):
        return GuardrailReason.UNSUPPORTED_ACTION
    if _ACTION_PATTERN.search(normalized) and _TECHNICAL_ARTIFACT_PATTERN.search(normalized):
        return GuardrailReason.UNSUPPORTED_ACTION
    if _ENGLISH_ACTION_PATTERN.search(normalized) and _ENGLISH_ARTIFACT_PATTERN.search(normalized):
        return GuardrailReason.UNSUPPORTED_ACTION
    return None
