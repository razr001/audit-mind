# Dramatiq CLI 会从该模块寻找 Broker。
from app.infrastructure.task_broker import task_broker as broker

# 导入 Actor 模块才能完成任务注册。
from app.tasks import audit_tasks as audit_tasks
from app.tasks import regulation_tasks as regulation_tasks

__all__ = ["broker"]
