import re

from app.ai.agent.schemas import AgentIntent
from app.ai.regulation_qa.input_policy import detect_input_policy_violation

_DRAFT_PATTERN = re.compile(r"(?:起草|拟定|撰写|编写|生成|写).{0,12}(?:合同|协议|制度|合规说明)")
_REVIEW_PATTERN = re.compile(r"(?:审查|审核|检查|评审|修改).{0,12}(?:合同|协议|制度|文书)")
_DELETE_PATTERN = re.compile(r"(?:删除|移除|清理).{0,12}(?:法规|知识|文档|审计任务)")
_WRITE_PATTERN = re.compile(
    r"(?:新增|添加|上传|录入|创建|新建|重试|重新执行|处理).{0,18}"
    r"(?:法规|知识|制度|文档|审计|任务)"
)
_READ_PATTERN = re.compile(
    r"(?:查询|查看|列出|有哪些|多少(?:条|个)?|总数|统计|进度|状态|结果|详情).{0,18}"
    r"(?:法规|规则|知识|文档|审计|任务|问题)"
)
_RULE_COUNT_PATTERNS = (
    re.compile(r"(?:现在|当前)?(?:系统(?:中|里)?|我(?:可访问的)?)?(?:一共|共有|有)?多少(?:条|个)?(?:法规)?规则"),
    re.compile(r"(?:现在|当前)?(?:系统(?:中|里)?|我(?:可访问的)?)?(?:法规)?规则(?:的)?总数(?:是)?多少"),
)
def is_rule_count_question(question: str) -> bool:
    """识别不带筛选条件的规则总数问题，复杂统计仍交给 Agent 规划。"""

    normalized = question.strip().rstrip("？?。！!").strip()
    return any(pattern.fullmatch(normalized) for pattern in _RULE_COUNT_PATTERNS)
def classify_agent_intent(question: str) -> AgentIntent:
    """用确定性规则选择工具集合；它不是权限判断器。

    匹配顺序很重要：安全违规和删除意图必须最先处理；合同起草属于允许的
    内容生成能力，不能因为出现“写”字而误判成系统写操作。
    """

    normalized = " ".join(question.strip().split())
    # 输入策略违规直接标记为不支持，后续不会向模型暴露任何业务工具。
    if detect_input_policy_violation(normalized) is not None:
        return AgentIntent.UNSUPPORTED
    if _DELETE_PATTERN.search(normalized):
        return AgentIntent.SYSTEM_DELETE
    # “写一份合同”只是生成文本，不会修改系统数据，因此先于 SYSTEM_WRITE 匹配。
    if _DRAFT_PATTERN.search(normalized):
        return AgentIntent.DRAFT_LEGAL_DOCUMENT
    if _REVIEW_PATTERN.search(normalized):
        return AgentIntent.REVIEW_LEGAL_DOCUMENT
    if _WRITE_PATTERN.search(normalized):
        return AgentIntent.SYSTEM_WRITE
    if _READ_PATTERN.search(normalized):
        return AgentIntent.SYSTEM_READ
    return AgentIntent.REGULATION_QA
