"""Yuanshen 配置、全局状态与底层常量。"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

console = Console()

SCRIPT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SCRIPT_DIR.parent                # Yuanshen/ → 项目根目录

# 用户数据目录 ~/.yuanshen/（npm 全局安装后项目/配置/历史存于此）
_YUANSHEN_HOME = Path.home() / ".yuanshen"
YUANSHEN_DIR = _YUANSHEN_HOME
try:
    YUANSHEN_DIR.mkdir(parents=True, exist_ok=True)
    (YUANSHEN_DIR / "projects").mkdir(exist_ok=True)
except OSError:
    # 降级：home 不可写时用脚本目录（容器/只读环境）
    YUANSHEN_DIR = SCRIPT_DIR / ".yuanshen"
    YUANSHEN_DIR.mkdir(parents=True, exist_ok=True)
    (YUANSHEN_DIR / "projects").mkdir(exist_ok=True)

# 优先读取 ~/.yuanshen/.env，其次项目目录 .env（兼容旧版）
yuanshen_env = YUANSHEN_DIR / ".env"
if yuanshen_env.exists():
    load_dotenv(yuanshen_env)
else:
    load_dotenv(SCRIPT_DIR / ".env")
    # 首次运行：从 .env.example 复制模板
    if not yuanshen_env.exists() and (SCRIPT_DIR / ".env.example").exists():
        import shutil
        shutil.copy2(SCRIPT_DIR / ".env.example", yuanshen_env)
        print(f"📁 已创建 {yuanshen_env}，请编辑填入 API Key")
        print(f"   或使用程序内 /api-key 命令设置\n")

# httpx 不支持 socks:// 代理 scheme（Clash 等会设 all_proxy=socks://...），
# 否则创建 API 客户端时报 "Unknown scheme for proxy URL"。
# API 流量走 http(s)_proxy 已足够，直接摘掉 all_proxy。
for _k in ("ALL_PROXY", "all_proxy"):
    if os.environ.get(_k, "").startswith("socks://"):
        del os.environ[_k]

WORKDIR = Path.cwd()
SKILLS_DIR = SCRIPT_DIR / "skills"
FILES_DIR = SCRIPT_DIR / "file"         # v4 兼容：旧版 task 目录
PROJECTS_DIR = YUANSHEN_DIR / "projects"   # 项目目录 ~/.yuanshen/projects/
WIRING_FILE = SCRIPT_DIR / "wiring.md"  # 默认接线模板（项目本地有副本）
ESP32_REFERENCE_FILE = (
    SCRIPT_DIR / "docs/reference/修正ESP32_D0WD_硬件开发手册.md"
)
CURRENT_TASK_DIR = None                 # 当前任务的项目文件夹（main 里逐任务设置）
MAX_ITERATIONS = 100
APP_VERSION = "1.2.2"
AUDIO_VALIDATION_MODE = os.getenv("AUDIO_VALIDATION_MODE", "auto").strip().lower()
if AUDIO_VALIDATION_MODE not in ("auto", "required", "off"):
    AUDIO_VALIDATION_MODE = "auto"

# 连接 ESP32 的串口："auto" 由 mpremote 自动探测；可用 ESP32_PORT 环境变量
# 或 /port 命令指定（如 COM5、/dev/ttyACM0），MCP 服务器进程继承该环境变量。
ESP32_PORT = os.getenv("ESP32_PORT", "auto").strip() or "auto"


def read_wiring() -> str:
    wiring_file = WIRING_FILE
    if CURRENT_TASK_DIR is not None:
        project_wiring = CURRENT_TASK_DIR / "wiring.md"
        if project_wiring.exists():
            wiring_file = project_wiring
    try:
        return wiring_file.read_text().strip()
    except OSError:
        return "（wiring.md 不存在——接线情况未知，涉及外接硬件时先向用户确认）"


def read_esp32_reference() -> str:
    """读取板级硬件事实，供规范化模型与 wiring.md 联合推理。"""
    try:
        content = ESP32_REFERENCE_FILE.read_text().strip()
    except OSError as e:
        raise RuntimeError(
            f"无法读取 ESP32 硬件参考文件 {ESP32_REFERENCE_FILE}: {e}"
        ) from e
    if not content:
        raise RuntimeError(f"ESP32 硬件参考文件为空: {ESP32_REFERENCE_FILE}")
    return content


THINKING = {"type": "disabled"}     # Anthropic 思考参数；非 Anthropic 官方端点不传

MODEL_REGISTRY = {
    "deepseek-v4-pro": {
        "provider": "anthropic",
        "api_name": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key_env": "DEEPSEEK_API_KEY",
        "key_hint": "platform.deepseek.com → API Keys",
        "supports_thinking": False,
    },
    "deepseek-v4-flash": {
        "provider": "anthropic",
        "api_name": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key_env": "DEEPSEEK_API_KEY",
        "key_hint": "platform.deepseek.com → API Keys",
        "supports_thinking": False,
    },
    "kimi-k3": {
        "provider": "openai",
        "api_name": "kimi-k3",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "key_hint": "platform.moonshot.cn → API Key",
        "supports_thinking": False,
    },
    "kimi-k2.7": {
        "provider": "openai",
        "api_name": "kimi-k2.7",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "key_hint": "platform.moonshot.cn → API Key",
        "supports_thinking": False,
    },
}

DEFAULT_MODEL_ALIAS = "deepseek-v4-pro"
_current_model_alias = os.getenv("MODEL", DEFAULT_MODEL_ALIAS).strip()
API_KEY = None
_clients = {}  # provider -> client instance
