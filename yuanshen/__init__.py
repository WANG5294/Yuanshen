"""Yuanshen ESP32 Agent 包。

保持对旧版 `import yuanshen` 的兼容：A2A 服务端通过本模块访问核心符号。
"""
from yuanshen.config import (
    APP_VERSION,
    CURRENT_TASK_DIR,
    PROJECTS_DIR,
    WIRING_FILE,
    console,
)
from yuanshen.mcp_client import init_mcp
from yuanshen.models import key_guidance, load_api_key
from yuanshen.agent import confirm_normalized_task, execute_project_task
from yuanshen.utils import slugify

__all__ = [
    "console",
    "APP_VERSION",
    "slugify",
    "PROJECTS_DIR",
    "WIRING_FILE",
    "CURRENT_TASK_DIR",
    "confirm_normalized_task",
    "execute_project_task",
    "load_api_key",
    "key_guidance",
    "init_mcp",
]
