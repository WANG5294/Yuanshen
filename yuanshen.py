#!/usr/bin/env python3
"""Yuanshen v1.0 正式版 —— ESP32 单片机开发 agent

v1.0 将既有 v4.2 功能冻结为首个正式发布基线。
v4.2 增加板级参考与 wiring 联合理解、可降级音频验收和 /audio 会话开关。
v4.1 在 v4.0 的追加式 Prompt 架构上强化计时、终端输入和完成门禁。
v4.0 将模型输入重构为“固定 System Prompt → 追加式 User/会话历史 →
动态 TodoList”三段式结构，并为每轮模型调用保存完整快照。

继承 v3.1：
  - 普通任务先进入需求与接线规范化阶段；完整展示优化后的任务目标和
    wiring.md，用户确认后才覆盖接线并启动 agent 循环
  - 用户确认前不创建任务消息链，原始提示词不会进入 agent 循环

继承 v3：
  - system prompt 新增【工具总览】：主要功能精简版常驻（详细参数仍在
    tool prompt），模型不用翻工具列表就知道能力边界
  - system prompt 新增【当前接线】：wiring.md 的内容常驻注入，接线事实
    （外接了什么、哪些脚被占）每轮都在模型眼前
  - 输入栏分流：硬件说明文档与用户任务分开输入——
      /doc <md路径>  导入新硬件说明文档（须符合 SKILL.md 格式：
                     frontmatter 含 name/description），校验后装入
                     skills/ 立即生效，换硬件零改代码
      /wiring        查看当前接线（普通任务确认规范化结果后自动更新）
      /port          查看/切换连接 ESP32 的串口（确认规范化接线时可一并修改）
  - 名称统一为 Yuanshen

继承 v2 的核心：agent 循环与循环摘要、主线任务 TodoWrite（烧录卡点+
烧录卡点）、实机验证红线、file/ 每任务一个项目文件夹、经验提取需用户
确认、/tool /skill /work /model。任务结束仅保存最终代码与完整 user prompt。
模型可通过 MODEL 环境变量或 /model 命令切换。

运行：
  python3 yuanshen.py       （自动切换 piano_workflow/.venv 解释器；
                             .env 优先本目录）
"""

import json
import hashlib
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.status import Status
from rich.columns import Columns

console = Console()

try:
    from prompt_toolkit import PromptSession, prompt as terminal_prompt
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.completion import WordCompleter
    _HAS_PT = True
except ImportError:
    PromptSession = None
    terminal_prompt = None
    _HAS_PT = False


SLASH_COMMANDS_WORDS = ["/tool", "/skill", "/work", "/wiring", "/audio",
                  "/model", "/doc", "/api-key", "/port", "/help", "/exit",
                  "/rounds", "/new", "/history"]

SLASH_COMMANDS_META = {
    "/tool": "📦 查看本地和 MCP 工具列表",
    "/skill": "🧠 查看可用技能知识库",
    "/work": "🔍 探测当前环境能力",
    "/wiring": "🔌 查看当前接线文档",
    "/audio": "🎤 开关音频验证模式",
    "/model": "🤖 切换大语言模型",
    "/doc": "📄 导入硬件说明文档为技能",
    "/api-key": "🔑 查看/更新 API Key",
    "/port": "🔌 查看/切换连接 ESP32 的串口",
    "/new": "📁 创建新 ESP32 项目",
    "/history": "📖 浏览历史项目",
    "/help": "❓ 显示帮助",
    "/exit": "🚪 退出程序",
    "/rounds": "🔄 显示轮次信息",
}

_session = None


def _model_names() -> list:
    """返回当前注册的模型别名列表（延迟获取）。"""
    return list(MODEL_REGISTRY.keys())


def read_input(message: str) -> str:
    """读取一行终端输入；优先使用 prompt_toolkit 增强版。
    支持 slash 命令补全、历史记录（.history 文件）。"""
    global _session
    if _HAS_PT and sys.stdin.isatty() and sys.stdout.isatty():
        if _session is None:
            _session = PromptSession(
                history=FileHistory(str(YUANSHEN_DIR / ".history")),
                completer=WordCompleter(
                    SLASH_COMMANDS_WORDS,
                    meta_dict=SLASH_COMMANDS_META,
                ),
            )
        try:
            return _session.prompt(message)
        except (EOFError, KeyboardInterrupt):
            raise
    if terminal_prompt is not None and sys.stdin.isatty() and sys.stdout.isatty():
        return terminal_prompt(message)
    return input(message)


def _bootstrap_interpreter():
    """缺依赖时原地 re-exec 到可用的 venv（判据是 sys.prefix）。
    优先本目录 .venv（npm 安装场景，由 bin/yuanshen.js 创建），
    其次开发环境的 piano_workflow/.venv。"""
    try:
        import anthropic  # noqa: F401
        import dotenv     # noqa: F401
        import prompt_toolkit  # noqa: F401
        import openai     # noqa: F401
        return
    except ModuleNotFoundError:
        script = Path(__file__).resolve()
        for venv_dir in (script.parent / ".venv",
                         script.parents[1] / "piano_workflow" / ".venv"):
            venv_py = venv_dir / "bin" / "python"
            if venv_py.exists() and Path(sys.prefix).resolve() != venv_dir.resolve():
                os.execv(str(venv_py), [str(venv_py), str(script), *sys.argv[1:]])
        sys.exit("缺少 Python 依赖。请通过 npm 启动器运行（yuanshen 命令），"
                 "或手动: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")


_bootstrap_interpreter()

from dotenv import load_dotenv

# =============================================================================
# 配置与模型预设
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
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
APP_VERSION = "1.1.5"
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


class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text

    def model_dump(self):
        return {"type": self.type, "text": self.text}


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict):
        self.id = id
        self.name = name
        self.input = input

    def model_dump(self):
        return {"type": self.type, "id": self.id,
                "name": self.name, "input": self.input}


class _UnifiedResponse:
    def __init__(self, content: list, stop_reason: str):
        self.content = content
        self.stop_reason = stop_reason


def current_model_config() -> dict:
    """返回当前模型配置；未知别名回退到默认值并警告。"""
    global _current_model_alias
    alias = _current_model_alias
    if alias not in MODEL_REGISTRY:
        print(f"⚠ 未知模型 '{alias}'，回退到 {DEFAULT_MODEL_ALIAS}")
        alias = DEFAULT_MODEL_ALIAS
        _current_model_alias = alias
    return MODEL_REGISTRY[alias]


def current_model_alias() -> str:
    return _current_model_alias


def has_key() -> bool:
    cfg = current_model_config()
    val = os.getenv(cfg["api_key_env"])
    return bool(val and "sk-xxx" not in val and len(val) >= 20)


def key_guidance() -> str:
    cfg = current_model_config()
    env_file = YUANSHEN_DIR / ".env"
    return (f"缺少 {cfg['api_key_env']}。保存方法：\n"
            f"  1. 编辑 {env_file}（首次运行已自动从 .env.example 创建）\n"
            f"  2. 加一行：{cfg['api_key_env']}=你的Key\n"
            f"     获取途径：{cfg['key_hint']}\n"
            f"  3. 保存后重新运行程序，或在程序内使用 /api-key 命令")


def load_api_key() -> bool:
    """根据当前模型加载对应 API Key。"""
    global API_KEY, _clients
    yuanshen_env = YUANSHEN_DIR / ".env"
    if yuanshen_env.exists():
        load_dotenv(yuanshen_env, override=True)
    load_dotenv(SCRIPT_DIR / ".env", override=True)
    cfg = current_model_config()
    if not has_key():
        API_KEY = None
        return False
    API_KEY = os.getenv(cfg["api_key_env"])
    _clients = {}  # 切换模型/Key 后重建客户端
    return True


def _save_api_key(env_name: str, key_value: str) -> None:
    """保存键值到 ~/.yuanshen/.env 文件（覆盖或追加；API Key、串口等通用）。"""
    env_path = YUANSHEN_DIR / ".env"
    lines = []
    found = False
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if line.startswith(f"{env_name}="):
            new_lines.append(f"{env_name}={key_value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{env_name}={key_value}")
    env_path.write_text("\n".join(new_lines) + "\n")
    # 重新加载环境变量
    if env_path.exists():
        load_dotenv(env_path, override=True)
    console.print(f"[dim]已保存到 {env_path}[/dim]")


def _get_anthropic_client(cfg: dict):
    from anthropic import Anthropic
    return Anthropic(api_key=API_KEY, base_url=cfg["base_url"])


def _get_openai_client(cfg: dict):
    from openai import OpenAI
    return OpenAI(api_key=API_KEY, base_url=cfg["base_url"])


def get_client():
    cfg = current_model_config()
    provider = cfg["provider"]
    if provider not in _clients:
        factory = _get_anthropic_client if provider == "anthropic" else _get_openai_client
        _clients[provider] = factory(cfg)
    return _clients[provider]


def llm_create(model=None, system=None, messages=None, tools=None,
               max_tokens=None, **kwargs):
    """统一 LLM 调用：根据当前模型配置选择 Anthropic 或 OpenAI SDK。
    一律使用普通工具模式（模型自行决定是否调用工具）。"""
    cfg = current_model_config()
    model_name = model or cfg["api_name"]
    client = get_client()

    if cfg["provider"] == "anthropic":
        extra = {}
        if cfg.get("supports_thinking"):
            extra["thinking"] = THINKING
        resp = client.messages.create(
            model=model_name,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            **extra,
            **kwargs,
        )
        return _UnifiedResponse(resp.content, resp.stop_reason)

    # OpenAI 兼容路径（Kimi / Moonshot）
    openai_messages = []
    if system:
        openai_messages.append({"role": "system", "content": system})
    openai_messages.extend(messages or [])
    openai_tools = ([{"type": "function", "function": {
        "name": t["name"],
        "description": t.get("description", ""),
        "parameters": t.get("input_schema", t.get("parameters", {})),
    }} for t in tools] if tools else None)
    resp = client.chat.completions.create(
        model=model_name,
        messages=openai_messages,
        tools=openai_tools,
        max_tokens=max_tokens,
        **kwargs,
    )
    choice = resp.choices[0]
    msg = choice.message
    content = []
    if msg.content:
        content.append(_TextBlock(msg.content))
    if msg.tool_calls:
        for tc in msg.tool_calls:
            content.append(_ToolUseBlock(
                tc.id,
                tc.function.name,
                json.loads(tc.function.arguments),
            ))
    stop_reason = "tool_use" if msg.tool_calls else choice.finish_reason
    return _UnifiedResponse(content, stop_reason)


def switch_model(alias: str) -> bool:
    """切换当前会话使用的模型；不修改 .env。"""
    global _current_model_alias, API_KEY, _clients
    alias = alias.strip()
    if alias not in MODEL_REGISTRY:
        return False
    _current_model_alias = alias
    API_KEY = None
    _clients = {}
    return True


# =============================================================================
# 任务入口门禁：需求与 wiring 规范化、用户确认
# =============================================================================

NORMALIZE_SYSTEM = """你是 ESP32 任务需求与接线文档规范化器。你会同时收到用户需求、
当前 wiring.md 和 ESP32 板级硬件参考手册；输出会先交给用户确认，不会直接进入执行 Agent。

将用户的自然语言目标改写成完整、严谨、可测试的需求；将当前 wiring.md 改写成完整、
严谨、无歧义的纯文本接线图。必须遵守：
1. 事实优先级必须区分：
   - wiring.md 是用户实际外接、跳线和物理拓扑的权威来源，不得擅自改变；
   - ESP32 硬件参考手册是板载器件、GPIO、驱动电路、有效电平和电气约束的权威来源；
   - 用户需求是目标行为的权威来源。
2. wiring.md 提到手册中可明确对应的板载器件时，必须用手册补全其 GPIO、板载驱动、
   有效电平、跳线和关键约束；这不算擅自新增接线。不得用手册猜测用户未声明的外接导线。
3. wiring.md 与硬件手册冲突时，不得静默覆盖任一方；保留用户原始说法，并在需求末尾
   明确列出冲突和待用户确认项。
4. 为器件分配稳定名称（开关1、红色LED1等），并在需求和接线中使用完全相同的名称。
5. 需求写清每个输入状态对应的输出状态；用户和手册都未给出的参数标为“未指定”，不可猜测。
6. 接线每条导线使用“节点------器件/节点”表达；共用节点用竖线“|”对齐表示，必要时
   在括号内补充同一节点说明。GPIO 统一写作 GPIOxx，电源统一写 VCC(3.3V)、+5V、GND。
7. 若原接线存在危险、矛盾或不足以实现需求，不要静默修正；原样表达拓扑，并在需求末尾
   增加“待用户确认：……”说明。
8. 不输出分析、建议或任何额外字段。

完成后必须调用 submit_normalized_task 工具提交结果。"""


# 规范化结果通过工具调用提交：提示词约束模型调用该工具；未调用时从文本中提取 JSON 兜底
NORMALIZE_TOOL = {
    "name": "submit_normalized_task",
    "description": "提交规范化后的需求与接线文档",
    "input_schema": {
        "type": "object",
        "properties": {
            "requirement": {"type": "string",
                            "description": "确认后交给 Agent 的完整需求"},
            "wiring": {"type": "string",
                       "description": "确认后写入 wiring.md 的完整接线文档"},
            "audio_required": {"type": "boolean",
                               "description": "任务是否需要音频闭环验证"},
        },
        "required": ["requirement", "wiring", "audio_required"],
    },
}


def _validate_normalized(data: dict) -> tuple[str, str, bool]:
    """校验规范化结果字段，拒绝空字段和异常膨胀内容。"""
    requirement = data.get("requirement")
    wiring = data.get("wiring")
    audio_required = data.get("audio_required")
    if not isinstance(requirement, str) or not isinstance(wiring, str):
        raise ValueError(
            "requirement 和 wiring 必须是字符串，实际返回："
            + str(data)[:300])
    requirement = requirement.strip()
    wiring = wiring.strip()
    if not requirement or not wiring:
        raise ValueError("requirement 或 wiring 为空")
    if not isinstance(audio_required, bool):
        raise ValueError("audio_required 必须是布尔值")
    if len(requirement) > 8000 or len(wiring) > 12000:
        raise ValueError("规范化结果异常过长")
    return requirement, wiring, audio_required


def _parse_normalized_task(raw: str) -> tuple[str, str, bool]:
    """从纯文本回复中提取 JSON（工具调用的兜底路径），失败时保留现场。"""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        snippet = (raw or "").strip()[:300]
        if not snippet:
            raise ValueError("模型返回为空（可能是 API 抖动或限流）")
        raise ValueError(f"模型未返回 JSON 对象，原始回复：{snippet}")
    return _validate_normalized(json.loads(match.group(0)))


def _normalized_from_response(response) -> tuple[str, str, bool]:
    """优先取工具调用结果；模型未调用工具时降级到文本 JSON 提取。"""
    for block in response.content:
        if (getattr(block, "type", None) == "tool_use"
                and block.name == NORMALIZE_TOOL["name"]):
            return _validate_normalized(block.input)
    text = _text_of(response)
    if not text.strip() and getattr(response, "stop_reason", "") == "max_tokens":
        raise ValueError("模型输出被 max_tokens 截断（思考模式可能吃光了额度）")
    return _parse_normalized_task(text)


def normalize_task_input(user_input: str, current_wiring: str,
                         previous_requirement: str = "",
                         previous_wiring: str = "",
                         feedback: str = "") -> tuple[str, str, bool]:
    """联合硬件手册与 wiring.md 规范化；此调用没有 Agent 工具或消息链。
    结果通过工具调用提交（普通工具模式），失败自动重试，最多三次；
    重试时把上一轮的错误反馈给模型（追加催告），纠正其行为而非盲重。"""
    sections = [
        f"【用户本次原始输入】\n{user_input}",
        f"【当前 wiring.md】\n{current_wiring}",
        (f"【ESP32 板级硬件参考手册｜板载事实】\n"
         f"文件：{ESP32_REFERENCE_FILE}\n{read_esp32_reference()}"),
    ]
    if previous_requirement or previous_wiring:
        sections.extend([
            f"【上一版规范化需求】\n{previous_requirement}",
            f"【上一版规范化接线】\n{previous_wiring}",
        ])
    if feedback:
        sections.append(f"【用户修改意见】\n{feedback}")
    messages = [{"role": "user", "content": "\n\n".join(sections)}]
    last_err = None
    for attempt in (1, 2, 3):
        try:
            response = llm_create(
                model=current_model_alias(),
                max_tokens=8000,
                system=NORMALIZE_SYSTEM,
                messages=messages,
                tools=[NORMALIZE_TOOL],
            )
            return _normalized_from_response(response)
        except Exception as e:
            last_err = e
            if attempt < 3:
                console.print(f"[dim]规范化失败（{e}），自动重试…（第 {attempt + 1}/3 次）[/dim]")
                # 追加催告：把错误反馈给模型，下一轮针对性纠错
                messages.append({
                    "role": "user",
                    "content": (f"你上一轮的输出有问题：{e}\n"
                                "请重新处理上面的需求，结果必须通过调用 "
                                "submit_normalized_task 工具提交，不要直接输出文本。"),
                })
    raise last_err


def write_wiring(wiring: str):
    """确认后原子替换 wiring.md（优先项目目录）。"""
    wiring_file = WIRING_FILE
    if CURRENT_TASK_DIR is not None:
        project_wiring = CURRENT_TASK_DIR / "wiring.md"
        # 总是写入项目目录（不存在则创建）
        project_wiring.parent.mkdir(parents=True, exist_ok=True)
        wiring_file = project_wiring
    tmp = wiring_file.with_name(wiring_file.name + ".tmp")
    tmp.write_text(wiring.rstrip() + "\n")
    tmp.replace(wiring_file)


def confirm_normalized_task(user_input: str) -> tuple[str, str, bool] | None:
    """循环展示规范化草案；确认前不写 wiring、不返回 Agent 输入。"""
    original_wiring = read_wiring()
    requirement = ""
    wiring = ""
    audio_required = False
    feedback = ""
    while True:
        try:
            requirement, wiring, audio_required = normalize_task_input(
                user_input, original_wiring,
                previous_requirement=requirement,
                previous_wiring=wiring,
                feedback=feedback,
            )
        except Exception as e:
            console.print(f"[red]❌ 需求与接线规范化失败：{e}[/red]")
            console.print("[dim]本次任务未进入 Agent 循环，wiring.md 未修改。[/dim]")
            return None

        while True:
            console.print(Panel(requirement, title="📋 待确认：规范化需求",
                                border_style="cyan"))
            console.print(Panel(wiring, title="🔌 待确认：规范化接线",
                                border_style="green"))
            port_line = ESP32_PORT + ("（mpremote 自动探测）" if ESP32_PORT == "auto" else "")
            console.print(f"[dim]🔌 连接串口: {port_line}（如需修改，回答 port COM5；"
                          "任务外也可随时用 /port 命令）[/dim]")
            try:
                answer = read_input(
                    "确认以上内容？[y=确认并启动 / 输入修改意见=重新优化 / "
                    "port <串口>=切换串口 / n=取消任务]: ").strip()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer.lower().startswith("port"):
                cmd_port(answer[4:].strip())
                continue          # 串口变更不触发重新规范化，直接重显草案
            break

        if answer.lower() in ("y", "yes", "是", "确认"):
            try:
                write_wiring(wiring)
            except OSError as e:
                console.print(f"[red]❌ wiring.md 写入失败：{e}[/red]")
                console.print("[dim]本次任务未进入 Agent 循环。[/dim]")
                return None
            console.print(f"[green]✅ 已确认并更新 {WIRING_FILE.name}，即将以规范化需求启动 Agent。[/green]")
            return requirement, wiring, audio_required
        if answer.lower() in ("n", "no", "否", "取消", "cancel"):
            console.print("[yellow]已取消。本次任务未进入 Agent 循环，wiring.md 未修改。[/yellow]")
            return None
        if not answer:
            console.print("[yellow]未收到确认。请输入 y、n，或直接输入具体修改意见。[/yellow]")
            feedback = "用户未确认，请保持上一版内容并再次完整输出。"
        else:
            feedback = answer


# =============================================================================
# MCP 最小客户端（stdio JSON-RPC 2.0）—— v2 注册全部工具，含麦克风闭环
# =============================================================================

# MCP 服务器脚本与解释器：优先包内自带（npm 安装场景），其次开发环境路径
_MCP_PY = SCRIPT_DIR / "esp32_piano_mcp.py"
if not _MCP_PY.exists():
    _MCP_PY = PROJECT_ROOT / "piano_workflow" / "esp32_piano_mcp.py"
VENV_PY = SCRIPT_DIR / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not VENV_PY.exists():
    VENV_PY = SCRIPT_DIR / ".venv" / "bin" / "python"
if not VENV_PY.exists():
    VENV_PY = PROJECT_ROOT / "piano_workflow" / ".venv" / "bin" / "python"
if not VENV_PY.exists():
    VENV_PY = Path(sys.executable)
MCP_SERVERS = [
    {"name": "esp32-piano", "cmd": [str(VENV_PY), str(_MCP_PY)]},
]


class MCPClient:
    def __init__(self, name: str, cmd: list, timeout: float = 180):
        self.name = name
        self.timeout = timeout
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        self._next_id = 0
        self._queue = queue.Queue()
        self._pending = {}
        self._stderr_lines = []
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "yuanshen", "version": APP_VERSION}})
        self._notify("notifications/initialized")

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self._queue.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        self._queue.put(None)

    def _stderr_loop(self):
        if self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr_lines.append(line)
                del self._stderr_lines[:-50]

    def _send(self, msg: dict):
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        self._send(req)
        deadline = time.monotonic() + self.timeout
        while True:
            if rid in self._pending:
                msg = self._pending.pop(rid)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = "\n".join(self._stderr_lines[-5:])
                    raise TimeoutError(
                        f"MCP 调用超时: {method}" + (f"\n{detail}" if detail else "")
                    )
                try:
                    msg = self._queue.get(timeout=remaining)
                except queue.Empty:
                    detail = "\n".join(self._stderr_lines[-5:])
                    raise TimeoutError(
                        f"MCP 调用超时: {method}" + (f"\n{detail}" if detail else "")
                    )
            if msg is None:
                detail = "\n".join(self._stderr_lines[-10:])
                raise RuntimeError(
                    f"MCP 服务器 {self.name} 已退出" + (f"\n{detail}" if detail else "")
                )
            if "id" not in msg:
                continue
            if msg.get("id") != rid:
                self._pending[msg.get("id")] = msg
                continue
            if "error" in msg:
                raise RuntimeError(f"MCP 错误: {msg['error']}")
            return msg.get("result", {})

    def _notify(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def list_tools(self) -> list:
        return self._request("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict, timeout: float = None) -> str:
        # upload 等慢工具内部超时（120s+重试）可能超过默认 180s，
        # 允许按调用放大超时，避免 agent 只收到笼统的“MCP 调用超时”
        saved, self.timeout = self.timeout, timeout or self.timeout
        try:
            result = self._request("tools/call", {"name": name, "arguments": arguments})
        finally:
            self.timeout = saved
        parts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            return f"Error: {text}"
        return text or "(无输出)"


MCP_CLIENTS = {}
MCP_TOOL_DEFS = []


def init_mcp():
    for cfg in MCP_SERVERS:
        try:
            client = MCPClient(cfg["name"], cfg["cmd"])
            tools = client.list_tools()
            for t in tools:
                MCP_CLIENTS[t["name"]] = client
                MCP_TOOL_DEFS.append({
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("inputSchema",
                                          {"type": "object", "properties": {}})})
            console.print(f"[green]MCP 服务器 {cfg['name']}: 注册 {len(tools)} 个工具（含麦克风闭环）[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠ MCP 服务器 {cfg['name']} 启动失败: {e}（其工具不可用）[/yellow]")


# =============================================================================
# SkillLoader（可重载：任务结束提取的新经验立即可用）
# =============================================================================

class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = {}
        self.reload()

    def reload(self):
        self.skills = {}
        if not self.skills_dir.exists():
            return
        for skill_dir in sorted(self.skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_dir.is_dir() and skill_md.exists():
                parsed = self.parse(skill_md)
                if parsed:
                    self.skills[parsed["name"]] = parsed

    def parse(self, path: Path):
        content = path.read_text()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            return None
        frontmatter, body = match.groups()
        meta = {}
        key = None
        for line in frontmatter.strip().split("\n"):
            if ":" in line and not line.startswith((" ", "\t")):
                key, value = line.split(":", 1)
                key = key.strip()
                meta[key] = value.strip().strip("\"'")
            elif key:
                meta[key] += " " + line.strip()
        if "name" not in meta or "description" not in meta:
            return None
        return {"name": meta["name"], "description": meta["description"],
                "body": body.strip()}

    def get_descriptions(self) -> str:
        if not self.skills:
            return "(无可用技能)"
        return "\n".join(f"- {n}: {s['description']}"
                         for n, s in self.skills.items())

    def get_content(self, name: str):
        skill = self.skills.get(name)
        if not skill:
            return None
        return f"# Skill: {skill['name']}\n\n{skill['body']}"


SKILLS = SkillLoader(SKILLS_DIR)

# =============================================================================
# TodoManager（主线任务：编写代码 → 烧录代码 → 测试代码 → 完成）
# =============================================================================

MAINLINE = ["编写代码", "烧录代码", "测试代码", "完成"]


class TodoManager:
    """记住用户确认后的规范化需求（goal），维护主线任务清单。
    每个新任务自动初始化四个固定主线步骤；模型只能更新其状态。"""

    def __init__(self):
        self.goal = ""
        self.audio_required = False
        self.items = []
        self.deployed_main = False
        self.device_verified = False
        self.deployed_hash = None
        self.evidence_log = []
        self.mic_noise_rms = None
        self.audio_signal_ratio = None
        self.audio_pitch_ok = None
        self.audio_analysis_attempted = False
        self.audio_degraded_reason = ""

    def start(self, goal: str, audio_required: bool = False):
        self.goal = goal
        self.audio_required = audio_required
        self.deployed_main = False        # 本任务是否已把程序部署为板上 main.py
        self.device_verified = False
        self.deployed_hash = None
        self.evidence_log = []
        self.mic_noise_rms = None
        self.audio_signal_ratio = None
        self.audio_pitch_ok = None
        self.audio_analysis_attempted = False
        self.audio_degraded_reason = ""
        self.items = [{"content": s, "status": "pending", "activeForm": s}
                      for s in MAINLINE]

    def requires_audio_validation(self) -> bool:
        return self.audio_required

    def observe_tool(self, name: str, args: dict, output: str, ok: bool):
        """从真实工具结果维护完成门禁；代码改变后旧烧录和验证立即失效。"""
        metrics = {}
        marker = re.search(r"(?:^|\n)METRICS_JSON:(\{[^\n]+\})", output)
        if marker:
            try:
                metrics = json.loads(marker.group(1))
            except (json.JSONDecodeError, TypeError):
                metrics = {}
        target = str(args.get("path") or args.get("local_path") or "")
        if name in ("write_file", "edit_file") and Path(target).name == "main.py" and ok:
            self.deployed_main = False
            self.device_verified = False
            self.audio_signal_ratio = None
            self.audio_pitch_ok = None
            self.audio_analysis_attempted = False
            self.audio_degraded_reason = ""
            self.evidence_log.append("main.py 内容改变，旧部署与验证证据失效")
        if name == "repl_exec":
            code = str(args.get("code", ""))
            verifies_main = bool(re.search(
                r"open\s*\(\s*['\"]main\.py['\"]\s*\)", code
            ))
        elif name == "play_and_record":
            code = str(args.get("trigger_code", ""))
            verifies_main = bool(re.search(
                r"open\s*\(\s*['\"]main\.py['\"]\s*\)", code
            ))
        else:
            verifies_main = False
        if verifies_main and ok and self.deployed_main:
            self.device_verified = True
            self.evidence_log.append(
                f"{name} 已执行板上 main.py，匹配当前部署证据"
            )
        if name == "mic_check" and ok:
            if metrics.get("kind") == "mic_check":
                rms = metrics.get("rms")
                if isinstance(rms, (int, float)) and rms >= 0:
                    self.mic_noise_rms = float(rms)
                if metrics.get("valid") is False:
                    self.audio_degraded_reason = output.splitlines()[0]
            elif output.startswith(("麦克风录到全零", "麦克风信号近乎全零")):
                self.audio_degraded_reason = output.splitlines()[0]
        if name == "analyze_wav" and ok:
            self.audio_analysis_attempted = True
            if metrics.get("kind") == "analyze_wav":
                peak = metrics.get("peak")
                cents = metrics.get("cents")
                if (isinstance(peak, (int, float)) and peak >= 0
                        and self.mic_noise_rms and self.mic_noise_rms > 0):
                    self.audio_signal_ratio = float(peak) / self.mic_noise_rms
                if isinstance(cents, (int, float)):
                    self.audio_pitch_ok = abs(float(cents)) <= 10
                if metrics.get("valid") is False:
                    self.audio_degraded_reason = output.splitlines()[0]
            if AUDIO_VALIDATION_MODE == "auto":
                failure = self.audio_failure()
                if failure:
                    self.audio_degraded_reason = failure

    def audio_failure(self) -> str | None:
        if not self.audio_analysis_attempted:
            return "声音任务尚未完成一次录音分析"
        if self.audio_signal_ratio is None:
            return "录音无法计算峰值/噪声底比"
        if self.audio_signal_ratio < 5:
            return f"闭环峰值/噪声底比仅 {self.audio_signal_ratio:.2f}，要求至少 5.00"
        if self.audio_pitch_ok is False:
            return "录音基频偏差未达到 ±10 音分要求"
        if self.audio_pitch_ok is None:
            return "录音未提供可判定的目标音高结果"
        return None

    def validation_error(self) -> str | None:
        if not self.deployed_main:
            return "尚未把当前版本上传为板上 main.py"
        if not self.device_verified:
            return "尚无当前烧录版本的成功实机执行证据"
        if self.requires_audio_validation():
            if AUDIO_VALIDATION_MODE == "off":
                return None
            if AUDIO_VALIDATION_MODE == "auto" and self.audio_degraded_reason:
                return None
            failure = self.audio_failure()
            if failure:
                if AUDIO_VALIDATION_MODE == "auto" and self.audio_analysis_attempted:
                    self.audio_degraded_reason = failure
                    return None
                return failure
        return None

    def update(self, items: list) -> str:
        if len(items) != len(MAINLINE):
            raise ValueError("TodoList 必须且只能包含 4 项固定主线任务")
        validated = []
        in_progress = 0
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            active = str(item.get("activeForm", content)).strip()
            if not content:
                raise ValueError(f"第 {i} 项: content 必填")
            if content != MAINLINE[i]:
                raise ValueError(
                    f"第 {i + 1} 项必须是“{MAINLINE[i]}”，不可改名、调序或插入子项"
                )
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"第 {i} 项: status 非法")
            if status == "in_progress":
                in_progress += 1
            validated.append({"content": content, "status": status,
                              "activeForm": active})
            if self.items:
                previous = self.items[i]["status"]
                rank = {"pending": 0, "in_progress": 1, "completed": 2}
                if rank[status] < rank[previous]:
                    raise ValueError(
                        f"{content} 状态不能从 {previous} 回退到 {status}"
                    )
        if in_progress > 1:
            raise ValueError("同时只能有一个任务 in_progress")
        # 卡点：没把程序部署为板上 main.py，不允许宣称"烧录"完成
        for v in validated:
            if ("烧录" in v["content"] and v["status"] == "completed"
                    and not self.deployed_main):
                raise ValueError(
                    "烧录代码不能标记完成：本任务还没有把程序上传为板上的 main.py"
                    "（upload 时设 remote_name='main.py'，开机自启才算烧录；"
                    "仅上传为其他文件名只是拷贝模块，不算烧录）")
        states = {step: validated[index]["status"]
                  for index, step in enumerate(MAINLINE)}
        for index, step in enumerate(MAINLINE):
            if states[step] == "completed":
                unfinished = [s for s in MAINLINE[:index]
                              if states[s] != "completed"]
                if unfinished:
                    raise ValueError(
                        f"{step}不能标记完成：前置步骤未完成：{', '.join(unfinished)}")
        if states["测试代码"] == "completed":
            error = self.validation_error()
            if error:
                raise ValueError(f"测试代码不能标记完成：{error}")
        if states["完成"] == "completed" and states["测试代码"] != "completed":
            raise ValueError("完成不能标记完成：测试代码尚未完成")
        self.items = validated
        return self.render()

    def is_complete(self) -> bool:
        return self.validation_error() is None and bool(self.items) and all(
            item["content"] == step and item["status"] == "completed"
            for item, step in zip(self.items, MAINLINE))

    def status_note(self) -> str:
        if self.requires_audio_validation() and AUDIO_VALIDATION_MODE == "off":
            return "音频验证已关闭；任务仅按设备执行证据验收。"
        if self.audio_degraded_reason:
            return f"音频验证不可用且不阻塞主任务：{self.audio_degraded_reason}"
        return "无"

    def render(self) -> str:
        if not self.items:
            return "（无任务）"
        marks = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}
        lines = [f"{marks[t['status']]} {t['content']}" for t in self.items]
        return "\n".join(lines)


TODO = TodoManager()
AUDIO_TOOL_NAMES = {
    "mic_check", "record_audio", "play_and_record", "analyze_wav", "compare_audio",
}

# =============================================================================
# Prompt 结构：固定 System → 追加式 User/会话历史 → 当前 TodoList 置底
# =============================================================================


def _platform_desc() -> str:
    """运行时探测宿主平台描述，注入系统提示词（泛用 Linux 虚拟机 / Windows 物理机等）。"""
    system = platform.system() or "未知系统"
    release = platform.release()
    machine = platform.machine()
    if system == "Windows":
        serial_hint = "串口为 COM*（如 COM5）"
        env_note = "物理机直连 USB 串口；若连接失败，优先排查串口被占用、驱动或线缆"
    elif system == "Linux":
        serial_hint = "串口为 /dev/ttyACM* 或 /dev/ttyUSB*"
        env_note = ("若在虚拟机中运行，需确认 USB 串口设备已透传给虚拟机；"
                    "物理机则检查串口权限（dialout 组）")
    elif system == "Darwin":
        serial_hint = "串口为 /dev/cu.usbserial-* 或 /dev/cu.wchusbserial-*"
        env_note = "物理机直连 USB 串口"
    else:
        serial_hint = "串口名以系统实际枚举为准"
        env_note = "按当前系统实际情况排查串口连接"
    return (f"{system} {release}（{machine}），{serial_hint}。{env_note}。")


def build_system() -> str:
    return f"""你是 Yuanshen v1.0 正式版 —— ESP32 单片机开发 agent，运行环境：{_platform_desc()}工作目录 {WORKDIR}。

【身份与固定职责】你负责把用户确认后的 ESP32 目标推进到可验证的实机结果。你必须遵守本 System Prompt、使用已声明的 Skill 与工具、沿 TodoList 推进，并在硬件证据不足时如实报告失败。

【三段式输入契约】
1. 本 System Prompt 是任务内字节级冻结的最高优先级固定前缀，包含身份、规范化任务要求、规范化接线、规则、Skill、工具/MCP 标签和安全边界。
2. User/Assistant/Tool 历史只追加、不改写，完整保留用户输入、模型输出、工具调用和工具结果，使后一轮可复用前一轮的精确输入前缀。
3. 当前 TodoList 永远是最新 User 消息的最后一段；它后面不得追加计时、要求或工具结果。以最末尾 TodoList 为当前权威状态，旧 TodoList 仅为历史快照。

【每轮可见输出】需要调用工具时，只需在文本中写出约 30–50 个汉字的“本轮目标：……”，说明工具的目的和作用，并紧接真实工具调用。程序负责实时显示轮次、累计秒数、工具和目的；工具结束后在同一状态行更新成功/失败，再开始下一轮。不得伪造轮次、秒数或工具结果；Skill 只报名称，严禁复述正文。完成全部任务、不再调用工具时，直接输出最终汇报。

【工作范围】只处理与 ESP32 单片机相关的任务：编写/调试 MicroPython 程序、上传运行、串口日志分析、麦克风闭环验证实机声音。无关请求礼貌拒绝。

【用户输入约定】本任务输入已经经过用户确认，是唯一权威的规范化需求。自动执行完整主线，绝不反问"要不要烧录/要不要运行"，也不要恢复或猜测确认前的原始措辞。

【主线任务】TodoList 必须且只能包含以下四项，名称、顺序和数量均不可改变；TodoWrite 只能更新状态（开工把当前步骤设 in_progress，做完设 completed）：
1. 编写代码 —— 在主机上写好 MicroPython 程序
2. 烧录代码 —— 把程序上传为板上的 **main.py**（upload 时设 remote_name='main.py'），让它成为开机自启的主程序，这才叫烧录；仅上传为其他文件名只是拷贝模块，不算烧录，也无法把该步标记完成
3. 测试代码 —— 验证**烧录进去的 main.py 本体**能在板上跑出预期效果：用 repl_exec 执行 exec(open('main.py').read())（或带超时的 run_script）读输出；有声音时加麦克风闭环（play_and_record + analyze_wav）。只 import 某个模块名不算烧录后的验证。若 main.py 含 while True 主循环，验证启动段后超时软复位属正常
4. 完成 —— 硬件验证通过，向用户汇报结果
代码写完不算完成，硬件验证通过才算。一次只改一个变量。末尾 TodoList 是唯一权威状态，始终沿它推进。

【工具总览】主要功能精简版（详细参数以工具定义为准）：
- 本地：bash（shell）/ read_file / write_file / edit_file（文件自动落任务文件夹）/ Skill（按需加载知识）/ TodoWrite（主线进度）
- MCP·设备通道：list_ports（列串口）/ check_port（探测串口占用）/ connect_device（建立长连接，之后设备操作零握手）/ disconnect_device（释放串口）/ upload（传文件，目标 main.py 才算烧录）/ run_script（带超时运行）/ repl_exec（板上执行代码）/ device_ls / device_rm（板上文件管理）/ soft_reset（打断死循环）。设备通道默认长连接模式：首次设备调用自动建立并持有串口，任务期间其他程序无法占用；静默超时先 check_port
- MCP·音频闭环：mic_check（录音通道自检）/ record_audio / play_and_record（软触发播放并录音）/ analyze_wav（基频/包络/哒声）/ compare_audio（录音 vs 预览对比）

【规范化任务要求】以下内容由循环外的独立大模型参考 ESP32 硬件说明、用户确认的 wiring.md 和聊天框原始任务生成，并已由用户确认；它是本工程固定不变的唯一任务目标：
{TODO.goal}

【规范化接线】以下是同一循环外流程生成、经用户确认并写入 wiring.md 的接线快照。做任何硬件操作前先逐条对照，严禁与之矛盾的假设；若其中明确标有“待用户确认”，不得擅自补全：
{read_wiring()}

【技能】涉及具体硬件模块或历史经验时，先用 Skill 工具加载对应技能，只加载与当前任务相关的分块（exp- 开头的是从过往任务提取的经验）：
{SKILLS.get_descriptions()}

【文件与审计接线】本任务专属项目文件夹：{CURRENT_TASK_DIR}
你生成的一切文件（MicroPython 程序、录音 WAV、preview、分析产物）都必须放进该文件夹：write_file / 录音 out_path 用**不带斜杠的纯文件名**即可，系统会自动落到该文件夹；读取项目已有文件（如 KEY.py）仍可用原路径。禁止往项目根目录散落文件。任务结束后保留最终代码、完整 user prompt 和 rounds/ 逐轮审计快照；可清理测试录音与中间分析产物。
程序会在每轮循环结束后，把该轮完整 System Prompt、工具定义、全部 User/Assistant/Tool 历史、模型响应、工具结果和末尾 TodoList 写入本任务的 rounds/。这些审计快照不得由模型修改、删除或摘要。

【音频验证策略】当前模式：{AUDIO_VALIDATION_MODE}
1. required：音频是强制验收项。峰值必须 ≥5×本次噪声底，目标音高偏差须在 ±10 音分；
   响度/包络需求还须 preview + compare_audio 且相关系数 ≥0.8。未达标则任务不能完成。
2. auto（默认）：做一次完整麦克风闭环。若录音无效、削波、噪声过高或指标不达标，
   将音频标记为“不可用/未验证”，停止继续调增益和反复录音；改用当前 main.py 的板上
   执行证据完成主任务，并在最终汇报明确区分“程序/设备验证通过”和“音频未验证”。
   音频基础设施失败不得拖垮或无限循环整个任务。
3. off：不调用任何麦克风工具，仅按板上执行证据验收；最终汇报注明音频验证已关闭。
4. 无论模式如何，严禁把静态代码检查或 `trigger→OK` 伪装成听觉证据；汇报必须引用已有
   数字，并且不得把“音频未验证”写成“音频通过”。

【禁止的操作 —— 安全红线】
1. 禁止擦除 Flash（erase_flash）、刷写固件（esptool）
2. 禁止危险 shell 命令：rm -rf /、sudo、shutdown、reboot、mkfs、dd 写设备
3. 文件操作仅限当前工作目录内；串口仅限系统实际存在的 ESP32 串口设备（Linux 为 /dev/ttyACM* 与 /dev/ttyUSB*，Windows 为 COM*）
4. GPIO34/35 是输入专用引脚，禁止配置为输出

【迭代限制】单个任务最多 {MAX_ITERATIONS} 轮工具调用，耗尽后输出进度报告（已完成什么、卡在哪里、下一步建议）。"""


USER_PROMPT_PREFIX = """【Yuanshen User Prompt】
以下内容是当前任务的权威输入。固定任务信息不得改写或摘要；动态状态只用于决定下一轮行动。"""


def build_user_prompt(user_input: str, round_no: int, elapsed: int,
                      previous_result: str, previous_tools: str = "无",
                      todo_update: str = "", must_stop: bool = False) -> str:
    """生成本轮尾部 User 状态；当前 TodoList 必须是最后一段。"""
    sections = [
        USER_PROMPT_PREFIX,
        "【User Rule】\n严格执行 System Prompt 中已经确认并冻结的规范化任务要求与"
        "规范化接线；本消息链只追加状态，不重写工程目标。",
        "【动态循环状态】\n"
        f"当前轮次：{round_no}/{MAX_ITERATIONS}\n"
        f"上一轮：{previous_result}\n"
        f"上一轮工具：{previous_tools}\n"
        f"任务已运行：{elapsed}秒\n"
        f"音频验证状态：{TODO.status_note()}",
        ("【当前要求】\n已完成第 "
         f"{MAX_ITERATIONS} 轮工具循环。禁止继续调用工具，直接输出最终进度报告。"
         if must_stop else
         "【当前要求】\n沿主线继续工作。需要调用工具时，先按 System Prompt "
         "规定输出本轮状态块，然后发出与状态块一致的工具调用。"),
    ]
    if todo_update:
        sections.append(f"【Todo更新】\n{todo_update}")
    sections.append(f"【TodoList｜当前唯一权威状态】\n{TODO.render()}")
    return "\n\n".join(sections)


# =============================================================================
# 工具定义与实现
# =============================================================================


def base_tools() -> list:
    return [
        {"name": "bash", "description": (
            "执行受限 shell 命令（禁止管道、重定向、变量展开、复合命令）。"
            "仅允许：python -m py_compile、ruff、mypy、mpy-cross、ls、rg、"
            "head、tail、wc、file、git status/diff/log/show/rev-parse。"
            "写文件用 write_file，查串口/烧录/板载操作用 MCP 设备工具。"),
         "input_schema": {"type": "object",
                          "properties": {"command": {"type": "string"}},
                          "required": ["command"]}},
        {"name": "read_file", "description": "读取文件内容。",
         "input_schema": {"type": "object",
                          "properties": {"path": {"type": "string"},
                                         "limit": {"type": "integer"}},
                          "required": ["path"]}},
        {"name": "write_file", "description": "写入文件（覆盖）。",
         "input_schema": {"type": "object",
                          "properties": {"path": {"type": "string"},
                                         "content": {"type": "string"}},
                          "required": ["path", "content"]}},
        {"name": "edit_file", "description": "精确替换文件中的文本。",
         "input_schema": {"type": "object",
                          "properties": {"path": {"type": "string"},
                                         "old_text": {"type": "string"},
                                         "new_text": {"type": "string"}},
                          "required": ["path", "old_text", "new_text"]}},
        {"name": "Skill",
         "description": f"加载技能获得硬件分块知识或历史经验。任务匹配时立即使用。\n\n可用技能：\n{SKILLS.get_descriptions()}",
         "input_schema": {"type": "object",
                          "properties": {"skill": {"type": "string"}},
                          "required": ["skill"]}},
        {"name": "TodoWrite",
         "description": "更新固定四项主线的状态。必须依次提交编写代码、烧录代码、测试代码、完成，不可改名、调序、删除或插入子项。",
         "input_schema": {"type": "object",
                          "properties": {"items": {
                              "type": "array",
                              "minItems": 4,
                              "maxItems": 4,
                              "items": {"type": "object",
                                        "properties": {
                                            "content": {"type": "string"},
                                            "status": {"type": "string",
                                                       "enum": ["pending", "in_progress", "completed"]},
                                            "activeForm": {"type": "string"}},
                                        "required": ["content", "status"]}}},
                          "required": ["items"]}},
    ]


def get_all_tools() -> list:
    return base_tools() + MCP_TOOL_DEFS


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    # 任务项目目录（~/.yuanshen/projects）也是合法工作区，不只 WORKDIR
    if not any(path.is_relative_to(root) for root in (WORKDIR, PROJECTS_DIR)):
        raise ValueError(f"路径越出允许目录（工作目录或项目目录）: {p}")
    return path


def task_file(p: str, for_write: bool) -> str:
    """把不带路径分隔符的纯文件名定位到当前任务文件夹。

    写入：纯文件名一律落到任务文件夹；
    读取：任务文件夹里有就用它，没有则回落原路径（项目已有文件）。"""
    if CURRENT_TASK_DIR is None or "/" in p or p.startswith("."):
        return p
    cand = CURRENT_TASK_DIR / p
    if for_write or cand.exists():
        return str(cand)
    return p


def safe_mcp_path(p: str, for_write: bool) -> Path:
    """限制 MCP 使用的主机路径；写入只能落在当前任务目录。"""
    if not p:
        raise ValueError("MCP 文件路径不能为空")
    path = safe_path(task_file(p, for_write))
    if for_write:
        if CURRENT_TASK_DIR is None:
            raise ValueError("尚未建立任务目录")
        task_root = CURRENT_TASK_DIR.resolve()
        if not path.is_relative_to(task_root):
            raise ValueError(f"MCP 写入路径必须位于当前任务目录: {p}")
    return path


ALLOWED_COMMANDS = {
    "python", "python3", "ruff", "mypy", "mpy-cross",
    "ls", "rg", "head", "tail", "wc", "file", "git",
}
SHELL_META = re.compile(r"[;&|`<>$(){}\n\r]")


def _allowed_argv(argv: list[str]) -> bool:
    executable = Path(argv[0]).name
    if executable in ("python", "python3"):
        return len(argv) >= 3 and argv[1:3] == ["-m", "py_compile"]
    if executable == "git":
        return len(argv) >= 2 and argv[1] in {
            "status", "diff", "log", "show", "rev-parse",
        }
    return executable in ALLOWED_COMMANDS


def run_bash(cmd: str) -> str:
    try:
        if SHELL_META.search(cmd):
            return "Error: bash 工具不允许 shell 管道、重定向、变量展开或复合命令"
        argv = shlex.split(cmd)
        if not argv:
            return "Error: 命令不能为空"
        executable = Path(argv[0]).name
        if not _allowed_argv(argv):
            return f"Error: 命令或参数不在允许列表中: {executable}"
        r = subprocess.run(argv, shell=False, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=60)
        output = ((r.stdout + r.stderr).strip() or "(无输出)")[:50000]
        return output if r.returncode == 0 else f"Error: 命令退出码 {r.returncode}\n{output}"
    except Exception as e:
        return f"Error: {e}"


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit:
            lines = lines[:limit]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"已写入 {len(content)} 字节到 {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: 在 {path} 中找不到要替换的文本"
        fp.write_text(text.replace(old_text, new_text, 1))
        return f"已编辑 {path}"
    except Exception as e:
        return f"Error: {e}"


def run_skill(skill_name: str) -> str:
    content = SKILLS.get_content(skill_name)
    if content is None:
        available = ", ".join(SKILLS.skills) or "无"
        return f"Error: 未知技能 '{skill_name}'。可用: {available}"
    return f'<skill-loaded name="{skill_name}">\n{content}\n</skill-loaded>\n\n请遵循以上技能说明完成任务。'


def execute_tool(name: str, args: dict) -> str:
    if name in AUDIO_TOOL_NAMES:
        if AUDIO_VALIDATION_MODE == "off":
            return "AudioSkipped: 音频验证模式为 off，本次未调用麦克风工具"
        if AUDIO_VALIDATION_MODE == "auto" and TODO.audio_degraded_reason:
            return (
                "AudioSkipped: 音频验证已降级关闭，不再重试；原因："
                f"{TODO.audio_degraded_reason}"
            )
    if name == "bash":
        return run_bash(args["command"])
    if name == "read_file":
        return run_read(task_file(args["path"], False), args.get("limit"))
    if name == "write_file":
        return run_write(task_file(args["path"], True), args["content"])
    if name == "edit_file":
        return run_edit(task_file(args["path"], False),
                        args["old_text"], args["new_text"])
    if name == "Skill":
        return run_skill(args["skill"])
    if name == "TodoWrite":
        try:
            return TODO.update(args["items"])
        except Exception as e:
            return f"Error: {e}"
    if name == "upload" and name in MCP_CLIENTS:
        try:
            args["local_path"] = str(safe_mcp_path(
                args.get("local_path", ""), for_write=False
            ))
        except Exception as e:
            return f"Error: {e}"
        dest = (args.get("remote_name") or "").strip() \
            or Path(args.get("local_path", "")).name
        if dest == "main.py":
            # 新烧录会使旧版本的实机验证证据失效，无论本次上传是否成功。
            TODO.deployed_main = False
            TODO.device_verified = False
            TODO.audio_signal_ratio = None
            TODO.audio_pitch_ok = None
            TODO.audio_analysis_attempted = False
            TODO.audio_degraded_reason = ""
            TODO.deployed_hash = None
        try:
            out = MCP_CLIENTS[name].call_tool(name, args, timeout=400)
        except Exception as e:
            return f"Error: MCP 工具 {name} 调用失败: {e}"
        if dest == "main.py" and not out.startswith("Error"):
            TODO.deployed_main = True
            try:
                source = safe_mcp_path(
                    args.get("local_path", ""), for_write=False
                )
                TODO.deployed_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                TODO.evidence_log.append(
                    f"main.py 部署哈希：{TODO.deployed_hash[:12]}"
                )
            except OSError:
                TODO.deployed_hash = None
        return out
    if name in MCP_CLIENTS:
        # 录音输出、脚本/上传的本地路径同样定位到当前任务文件夹
        for key, for_write in (
            ("out_path", True), ("local_path", False), ("path", False),
            ("recorded", False), ("reference", False),
        ):
            if isinstance(args.get(key), str):
                try:
                    args[key] = str(safe_mcp_path(args[key], for_write))
                except Exception as e:
                    return f"Error: {e}"
        try:
            return MCP_CLIENTS[name].call_tool(name, args)
        except Exception as e:
            return f"Error: MCP 工具 {name} 调用失败: {e}"
    return f"Unknown tool: {name}"


# =============================================================================
# Agent 主循环（固定前缀 + 追加历史 + 尾部 Todo；Skill 屏幕只报名称）
# =============================================================================


def _text_of(response) -> str:
    return "".join(b.text for b in response.content
                   if getattr(b, "type", None) == "text")


def _jsonable_content(content):
    """把 Anthropic 内容块转换为完整、可持久化的 JSON 数据。"""
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        if isinstance(block, dict):
            out.append(block)
        elif hasattr(block, "model_dump"):
            out.append(block.model_dump())
        else:
            out.append(str(block))
    return out


def _round_purpose(text: str, tool_names: str) -> str:
    """优先采用模型声明的本轮目标，否则生成稳定的用户可见说明。"""
    match = re.search(r"本轮目标[：:]\s*(.+)", text)
    if match:
        purpose = match.group(1).strip().splitlines()[0]
    else:
        purpose = f"执行 {tool_names}，推进当前固定主线并取得可核验结果。"
    purpose = re.sub(r"\s+", " ", purpose)
    return purpose if len(purpose) <= 50 else purpose[:49] + "…"


def _round_status_line(round_no: int, elapsed: int, tool_names: str,
                       purpose: str, status: str) -> Text:
    """生成带颜色的 Rich Text 状态行。"""
    text = Text()
    text.append(f"第 {round_no} 轮 ", style="bold cyan")
    text.append(f"({elapsed}s) ", style="dim white")
    text.append(f"[{tool_names}] ", style="yellow")
    text.append(f"{purpose} ", style="white")
    style_map = {"成功": "bold green", "失败": "bold red",
                 "跳过": "bold yellow", "执行中": "bold blue"}
    color = style_map.get(status, "white")
    text.append(f"● {status}", style=color)
    return text


def _tool_outcome(output: str) -> str:
    if output.startswith("AudioSkipped:"):
        return "skipped"
    if output.startswith(("Error", "Unknown tool")):
        return "failed"
    return "success"


def _show_round_start(round_no: int, elapsed: int, tool_names: str,
                      purpose: str) -> bool:
    """TTY 中保留当前行以便改写；日志/管道模式输出独立的执行中记录。"""
    tty = sys.stdout.isatty()
    text = _round_status_line(
        round_no, elapsed, tool_names, purpose, "执行中"
    )
    if tty:
        console.print(text, end="")
    else:
        print(text.plain, end="\n")
    sys.stdout.flush()
    return tty


def _show_round_result(round_no: int, elapsed: int, tool_names: str,
                       purpose: str, status: str, rewrite: bool) -> None:
    text = _round_status_line(round_no, elapsed, tool_names, purpose, status)
    if rewrite and sys.stdout.isatty():
        # Rich 模式：清行后打印
        console.print("\r" + " " * console.width + "\r", end="")
        console.print(text)
    else:
        console.print(text)
    sys.stdout.flush()


def _animate_markdown(text: str) -> None:
    """打字机效果：按自然段分割，节奏更自然。
    短文本直接渲染，长文本逐段增量出现。"""
    from rich.live import Live

    if not text.strip():
        return
    if not sys.stdout.isatty():
        print(text)
        return

    # 按标点/换行分割成自然段
    import re
    segments = re.split(r"(?<=[。！？\n!?])", text)
    segments = [s for s in segments if s.strip()]

    # 短文本直接显示
    if len(segments) <= 2 and len(text) < 100:
        console.print(Markdown(text))
        return

    accumulated = ""
    try:
        with Live(Markdown(""), refresh_per_second=20,
                  vertical_overflow="visible") as live:
            for seg in segments:
                accumulated += seg
                live.update(Markdown(accumulated))
                # 短段加快，长段放慢
                delay = min(0.08, max(0.02, len(seg) * 0.002))
                time.sleep(delay)
    except KeyboardInterrupt:
        # Ctrl+C：直接打印剩余文本
        console.print(Markdown(text))


def _render_assistant_text(text: str) -> None:
    """渲染助手文本：短文本直接显示，长文本用打字机效果。"""
    text = text.strip()
    if not text:
        return
    if not sys.stdout.isatty():
        print(text)
        return
    if text.count("\n") < 3 and len(text) < 200:
        console.print(Markdown(text))
    else:
        _animate_markdown(text)


def write_round_snapshot(call_no: int, elapsed: int, system: str, tools: list,
                         messages: list, response_content,
                         status: str, purpose: str) -> Path:
    """每次模型循环结束立即写完整、不可变的 Markdown 快照。"""
    if CURRENT_TASK_DIR is None:
        raise RuntimeError("尚未建立任务目录，无法保存逐轮快照")
    rounds_dir = CURRENT_TASK_DIR / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    path = rounds_dir / f"round-{call_no:04d}.md"
    lines = [
        f"# Yuanshen v1.0｜第 {call_no} 轮完整快照",
        "",
        "## 元数据",
        "",
        f"- 累计时间：{elapsed} 秒",
        f"- 结果：{status}",
        f"- 作用和目的：{purpose}",
        f"- 模型：{current_model_alias()}",
        "",
        "## System Prompt",
        "",
        "~~~text",
        system,
        "~~~",
        "",
        "## Tools",
        "",
        "~~~json",
        json.dumps(tools, ensure_ascii=False, indent=2),
        "~~~",
        "",
        "## 完整 User / Assistant / Tool 历史",
        "",
    ]
    for index, message in enumerate(messages, 1):
        lines.extend([
            f"### 消息 {index}｜{message.get('role', 'unknown')}",
            "",
            "~~~json",
            json.dumps(_jsonable_content(message.get("content", "")),
                       ensure_ascii=False, indent=2),
            "~~~",
            "",
        ])
    lines.extend([
        "## 本轮模型原始响应",
        "",
        "~~~json",
        json.dumps(_jsonable_content(response_content),
                   ensure_ascii=False, indent=2),
        "~~~",
        "",
        "## 本轮结束时 TodoList",
        "",
        "~~~text",
        TODO.render(),
        "~~~",
        "",
    ])
    path.write_text("\n".join(lines))
    return path


def agent_loop(messages: list, run_log: dict, task_start: float):
    """执行追加式 Agent 循环，并在每轮结束后落盘完整审计快照。"""
    SKILLS.reload()                 # 对话中新写入的经验技能即时可见
    system = build_system()         # 单次任务内冻结，循环中禁止重建
    tools = get_all_tools()         # MCP/工具定义同样在单次任务内冻结
    run_log["system_prompt"] = system
    prompt_input = run_log.get("prompt_input", TODO.goal)
    round_no = 0
    call_no = 0
    while True:
        call_no += 1
        response = llm_create(model=current_model_alias(), system=system,
                              messages=messages, tools=tools, max_tokens=8000)

        # 用户端输出由程序统一渲染；模型中间分析和工具原始结果不上屏。
        if response.stop_reason != "tool_use":
            elapsed = int(time.monotonic() - task_start)
            completed = TODO.is_complete()
            status = "成功" if completed else "失败"
            purpose = ("完成全部 Todo 并向用户输出最终结果。"
                       if completed else
                       "TodoList 尚未全部完成，输出当前进度和失败原因。")
            _show_round_result(
                round_no + 1, elapsed, "无", purpose, status, False
            )
            for block in response.content:
                if getattr(block, "type", None) == "text" and block.text.strip():
                    _render_assistant_text(block.text)
            messages.append({"role": "assistant", "content": response.content})
            run_log["final_text"] = _text_of(response)
            run_log["elapsed"] = elapsed
            write_round_snapshot(
                call_no, run_log["elapsed"], system, tools, messages,
                response.content, status, purpose,
            )
            return

        text = _text_of(response).strip()

        # 工具轮次的思考过程用灰色显示
        if text and sys.stdout.isatty():
            lines = text.split("\n")
            short_text = "\n".join(lines[:6])  # 最多显示6行
            if len(lines) > 6:
                short_text += "\n[dim]…（思考中）[/dim]"
            console.print(f"[dim]{short_text}[/dim]")

        round_no += 1
        tool_calls = [b for b in response.content if b.type == "tool_use"]

        if round_no > MAX_ITERATIONS:
            results = [{"type": "tool_result", "tool_use_id": tc.id,
                        "content": "(已达最大迭代次数，工具未执行)"}
                       for tc in tool_calls]
            results.append({"type": "text",
                            "text": f"已达最大迭代次数 {MAX_ITERATIONS}。请停止调用工具，"
                                    "直接输出进度报告：1) 已完成什么 2) 卡在哪里 3) 下一步建议。"})
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})
            report = llm_create(model=current_model_alias(), system=system,
                                messages=messages, max_tokens=4000)
            for block in report.content:
                if getattr(block, "type", None) == "text":
                    _render_assistant_text(block.text)
            messages.append({"role": "assistant", "content": report.content})
            run_log["final_text"] = _text_of(report)
            run_log["elapsed"] = int(time.monotonic() - task_start)
            run_log["hit_limit"] = True
            write_round_snapshot(
                call_no, run_log["elapsed"], system, tools, messages,
                report.content, "失败",
                f"达到 {MAX_ITERATIONS} 轮上限，停止工具执行并输出进度报告。",
            )
            return

        results = []
        todo_before = TODO.render()
        used = []                             # [(标签, 类别, 成功?, 概要)]
        planned = []
        for tc in tool_calls:
            kind = "MCP" if tc.name in MCP_CLIENTS else "本地"
            label = (f"Skill({tc.input.get('skill', '?')})"
                     if tc.name == "Skill" else tc.name)
            planned.append((tc, label, kind))
        tool_names = "、".join(
            f"{label}（{kind}）" for _, label, kind in planned
        ) or "无"
        purpose = _round_purpose(text, tool_names)
        shown_elapsed = int(time.monotonic() - task_start)
        rewrite_status = _show_round_start(
            round_no, shown_elapsed, tool_names, purpose
        )
        for tc, label, kind in planned:
            output = execute_tool(tc.name, tc.input)     # 输出内容不上屏
            outcome = _tool_outcome(output)
            ok = outcome == "success"
            TODO.observe_tool(tc.name, tc.input, output, ok)
            # Skill 的输出是技能正文：循环状态里只报名称，严禁把正文带进摘要
            if tc.name == "Skill":
                brief = "技能说明已注入（正文不进摘要）" if ok else output[:80]
            else:
                brief = output[:80].replace("\n", " ")
            used.append((label, kind, outcome, brief))
            results.append({"type": "tool_result", "tool_use_id": tc.id,
                            "content": output})

        elapsed = int(time.monotonic() - task_start)
        run_log["rounds"].append(
            {"round": round_no, "elapsed_s": elapsed,
             "tools": [{"tool": n, "kind": k, "outcome": outcome,
                        "ok": outcome == "success", "brief": b}
                       for n, k, outcome, b in used]})
        all_ok = all(outcome == "success" for _, _, outcome, _ in used)
        labels = {"success": "成功", "failed": "失败", "skipped": "跳过"}
        detail = "；".join(
            f"{n}（{k}）→{labels[outcome]}（{brief}）"
            for n, k, outcome, brief in used
        )
        todo_after = TODO.render()
        todo_update = ""
        if todo_after != todo_before:
            todo_update = f"更新前：\n{todo_before}\n\n更新后：\n{todo_after}"
        previous_result = (
            f"{'成功' if all_ok else '失败'}——第 {round_no} 轮：{detail}"
        )
        next_round = min(round_no + 1, MAX_ITERATIONS)
        results.append({
            "type": "text",
            "text": build_user_prompt(
                prompt_input, next_round, elapsed, previous_result,
                previous_tools=tool_names, todo_update=todo_update,
                must_stop=round_no >= MAX_ITERATIONS,
            ),
        })
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})
        status = ("成功" if all_ok else
                  "跳过" if all(o == "skipped" for _, _, o, _ in used)
                  else "失败")
        _show_round_result(
            round_no, elapsed, tool_names, purpose, status, rewrite_status
        )
        write_round_snapshot(
            call_no, elapsed, system, tools, messages,
            response.content, status, purpose,
        )


# =============================================================================
# 最终产物保存 + 经验提取为 skill
# =============================================================================


def _serialize_content(content):
    """assistant 消息里的 anthropic 对象 → 可 JSON 化的 dict。"""
    return _jsonable_content(content)


def render_userprompt_md(user_input: str, system_prompt: str,
                         messages: list) -> str:
    """完整保存任务 Prompt 与消息链，不摘要、不截断工具反馈。"""
    lines = [
        "# Yuanshen 完整任务记录",
        "",
        "## 用户确认后的规范化需求",
        "",
        user_input,
        "",
        "## System Prompt（本任务内冻结）",
        "",
        system_prompt,
        "",
        "## 完整多轮消息链",
        "",
    ]
    for index, message in enumerate(messages, 1):
        role = message.get("role", "unknown")
        content = _serialize_content(message.get("content", ""))
        lines.extend([
            f"### 消息 {index}｜{role}",
            "",
            "~~~json",
            json.dumps(content, ensure_ascii=False, indent=2),
            "~~~",
            "",
        ])
    return "\n".join(lines)


def render_flow_md(user_input: str, run_log: dict) -> str:
    lines = [f"# 任务流程记录",
             f"",
             f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 模型：{current_model_alias()}",
             f"- 用户需求：{user_input}",
             f"- 总耗时：{run_log.get('elapsed', '?')} 秒，"
             f"共 {len(run_log['rounds'])} 轮工具调用"
             + ("（达到迭代上限）" if run_log.get("hit_limit") else ""),
             f"",
             f"## 逐轮流程", ""]
    if not run_log["rounds"]:
        lines.append("（本任务未调用工具，直接回答）")
    for r in run_log["rounds"]:
        lines.append(f"### 第 {r['round']} 轮（第 {r['elapsed_s']} 秒）")
        for t in r["tools"]:
            mark = "✓" if t["ok"] else "✗"
            lines.append(f"- {mark} {t['tool']}：{t['brief']}")
        lines.append("")
    lines += ["## 主线任务最终状态", "", "```", TODO.render(), "```", "",
              "## 最终回复", "", run_log.get("final_text", "(无)")]
    return "\n".join(lines)


EXTRACT_SYSTEM = """你是经验提取器。阅读一次 ESP32 单片机 agent 的任务流程记录，判断其中是否有值得沉淀为技能的**特殊经验**——即"遇到某种情况应该怎么做"的可复用结论（如：某种报错的真实原因与解法、某个工具的正确用法、某类硬件现象的排查顺序）。

只提取满足全部条件的经验：1) 流程中真实发生过；2) 下次遇到同类情况能直接复用；3) 不是常识、不是现有技能已覆盖的内容。

有则输出严格 JSON（不要代码块包裹）：
{"name": "exp-英文短横线小写名", "description": "一句话：什么情况下加载本技能", "body": "Markdown 正文：现象 → 原因 → 应对步骤，控制在 300 字内"}

无则只输出：NONE"""


def _save_exp_skill(name: str, desc: str, body: str, source: str):
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"
        f"\n> 来源：{source} 任务提取，已经用户确认\n")
    SKILLS.reload()                 # 下一个任务立即可用


def extract_skill(flow_md: str, run_dir: Path):
    """从流程记录提取经验技能，保存前需用户确认。

    返回 (结果说明, pending_context)：pending_context 非空表示用户选择
    "保留上下文继续对话修改"，将拼进下一轮用户消息。"""
    try:
        existing = "\n现有技能（勿重复提取）：\n" + SKILLS.get_descriptions()
        resp = llm_create(model=current_model_alias(), max_tokens=1500,
                          system=EXTRACT_SYSTEM,
                          messages=[{"role": "user",
                                     "content": flow_md[:8000] + existing}])
        raw = _text_of(resp).strip()
    except Exception as e:
        return f"经验提取调用失败：{e}", None

    if raw.upper().startswith("NONE"):
        return "本轮无特殊经验可提取", None

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
        name = data["name"].strip()
        desc = data["description"].strip()
        body = data["body"].strip()
        assert re.fullmatch(r"exp-[a-z0-9-]{3,40}", name)
    except Exception:
        return "经验提取输出无法解析", None

    candidate = f"名称：{name}\n适用：{desc}\n正文：\n{body}"

    # ---- 用户确认步骤 ----
    print(f"\n📝 提取到候选经验：\n  名称：{name}\n  适用：{desc}\n"
          f"  ---- 正文 ----\n{body}\n  --------------")
    try:
        ans = read_input("确认保存该经验为技能？[y=保存 / n=不保存]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"                   # 无交互环境默认不保存
    if ans in ("y", "yes", "是"):
        _save_exp_skill(name, desc, body, run_dir.name)
        return f"已保存经验技能：{name}（{desc}）", None

    try:
        ans2 = read_input("是否保留本轮上下文历史，继续对话修改（程序或该经验）？"
                          "[y=继续对话 / n=放弃保存]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans2 = "n"
    if ans2 in ("y", "yes", "是"):
        pending = (
            "【候选经验待修改】用户对下面这条自动提取的经验暂不确认，"
            "选择在原有记忆基础上继续多轮对话（可能要求进一步修改程序，"
            "或修改这条经验本身）。之后若用户表示满意并要求保存经验，"
            f"用 write_file 将修改后的经验写入 "
            f"{SKILLS_DIR}/<名称>/SKILL.md（保留 name/description "
            f"frontmatter 格式）。\n{candidate}")
        return "候选经验未保存，已带入上下文，可继续对话修改", pending

    return "候选经验未保存（用户不确认）", None


def new_task_dir(user_input: str) -> Path:
    """每个新任务在 file/ 下开一个专属项目文件夹。"""
    slug = re.sub(r"[^\w一-鿿]+", "-", user_input)[:24].strip("-") or "task"
    task_dir = FILES_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def write_requirement(task_dir: Path, requirement: str) -> Path:
    """保存用户确认后的任务要求，作为主体 Agent 的权威输入文档。"""
    path = task_dir / "requirement.md"
    path.write_text("# 用户确认后的任务要求\n\n" + requirement.rstrip() + "\n")
    return path


CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".c", ".h", ".cpp", ".hpp", ".ino", ".rs",
    ".go", ".java", ".sh", ".html", ".css",
}


def archive_run(user_input: str, task_dir: Path, messages: list,
                system_prompt: str) -> Path:
    """保存最终产物、完整消息链及 rounds/ 中的全部逐轮快照。"""
    transcript = render_userprompt_md(user_input, system_prompt, messages)
    (task_dir / "userprompt.md").write_text(transcript)
    return task_dir


# =============================================================================
# 斜杠命令：/tool /skill /work
# =============================================================================


def cmd_tool():
    table = Table(title="可用工具", border_style="dim")
    table.add_column("类型", style="cyan", no_wrap=True)
    table.add_column("名称", style="green")
    table.add_column("描述")

    for t in base_tools():
        table.add_row("本地", t["name"], t["description"].splitlines()[0])

    if MCP_TOOL_DEFS:
        for t in MCP_TOOL_DEFS:
            desc = t["description"].strip().splitlines()
            table.add_row("MCP", t["name"], desc[0] if desc else "(无描述)")
    else:
        table.add_row("MCP", "(无)", "MCP 服务器未启动")

    console.print(table)


def cmd_skill():
    table = Table(title="可用技能（exp- 开头为自动提取的经验）", border_style="dim")
    table.add_column("名称", style="cyan")
    table.add_column("描述")
    if not SKILLS.skills:
        table.add_row("(无)", "")
    for n, s in SKILLS.skills.items():
        table.add_row(n, s["description"])
    console.print(table)


def cmd_work():
    import glob
    ports = []
    try:
        from serial.tools import list_ports  # pyserial（mpremote 的依赖）
        ports = sorted(p.device for p in list_ports.comports())
    except Exception:
        ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")

    table = Table(title="当前环境能力探测", border_style="dim")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("状态")

    port_hint = "检查USB线" if sys.platform == "win32" else "检查USB线/dialout权限"
    port_status = (f"[green]✓[/green] {', '.join(ports)}"
                   if ports else f"[red]✗[/red] 未发现（{port_hint}）")
    table.add_row("串口设备", port_status)

    mp = shutil.which("mpremote")
    mp_status = f"[green]✓[/green] {mp}" if mp else "[red]✗[/red] 未安装"
    table.add_row("mpremote", mp_status)

    board = False
    if ports and mp:
        try:
            r = subprocess.run(["mpremote", "connect", ports[0], "exec", "print('pong')"],
                               capture_output=True, text=True, timeout=8)
            board = "pong" in r.stdout
        except Exception:
            pass
    board_status = ("[green]✓[/green] 可交互"
                    if board else "[red]✗[/red] 无响应（板子未接/被程序占用）")
    table.add_row("板子 REPL 响应", board_status)
    table.add_row("上传/运行/删除", ("[green]✓[/green] 可用"
                                   if board else "[red]✗[/red] 依赖上面三项"))

    mic = "[red]✗[/red] 探测失败"
    if "mic_check" in MCP_CLIENTS:
        try:
            out = MCP_CLIENTS["mic_check"].call_tool("mic_check", {})
            mic = (("[green]✓[/green] " + out.splitlines()[0])
                   if out.startswith("麦克风正常")
                   else "[red]✗[/red] 录到全零（VirtualBox 设备→音频→勾选音频输入）")
        except Exception as e:
            mic = f"[red]✗[/red] {rich_escape(str(e))}"
    else:
        mic = "[red]✗[/red] MCP 工具未注册"
    table.add_row("麦克风闭环", mic)
    table.add_row("音频验收模式", AUDIO_VALIDATION_MODE)
    table.add_row("技能知识库", (f"[green]✓[/green] {len(SKILLS.skills)} 个分块"
                                if SKILLS.skills else "[red]✗[/red] 无"))

    cfg = current_model_config()
    api_status = (f"[green]✓[/green] {current_model_alias()}"
                  if has_key() else f"[red]✗[/red] 缺 {cfg['api_key_env']}（使用 /api-key 设置）")
    table.add_row("大模型 API", api_status)
    table.add_row("固件烧录", "[red]✗[/red] 安全红线，永久禁止")

    console.print(table)


def cmd_wiring():
    console.print(Panel(read_wiring(), title=f"当前接线（{WIRING_FILE}）",
                        border_style="green"))
    console.print("[dim]普通任务开始前会同时规范化需求与本文件，并在你确认后覆盖更新；"
                  "也可直接编辑该文件，下一次规范化会读取最新内容。[/dim]")


def cmd_port(arg: str = ""):
    """查看或切换连接 ESP32 的串口（持久化到 ~/.yuanshen/.env）。"""
    global ESP32_PORT
    arg = arg.strip()
    if not arg:
        current = ESP32_PORT + ("（mpremote 自动探测）" if ESP32_PORT == "auto" else "")
        console.print(f"[dim]当前串口: {current}[/dim]")
        try:
            from serial.tools import list_ports as _lp
            ports = [f"{p.device}  {p.description}" for p in _lp.comports()]
            console.print("[dim]可用串口: " + ("\n  " + "\n  ".join(ports) if ports
                                              else "未发现") + "[/dim]")
        except ImportError:
            pass
        try:
            arg = read_input(
                "输入串口（如 COM5 / /dev/ttyACM0，auto=自动探测，直接回车取消）: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]未修改串口。[/dim]")
            return
        if not arg:
            console.print("[yellow]已取消[/yellow]")
            return
    ESP32_PORT = arg
    os.environ["ESP32_PORT"] = arg
    _save_api_key("ESP32_PORT", arg)
    # 热更新已运行的 MCP 服务器（esp32_piano_mcp 的 set_port 工具）
    client = MCP_CLIENTS.get("set_port")
    if client is not None:
        try:
            console.print(f"[dim]{client.call_tool('set_port', {'port': arg})}[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠ 已保存，但热更新 MCP 服务器失败：{e}（重启后生效）[/yellow]")
    console.print(f"[green]✅ 串口已切换为: {arg}"
                  + ("（自动探测）" if arg == "auto" else "") + "[/green]")


def cmd_audio(arg: str = ""):
    """查看或切换当前会话的音频验收模式。"""
    global AUDIO_VALIDATION_MODE
    choice = arg.strip().lower()
    if not choice:
        current = "开" if AUDIO_VALIDATION_MODE != "off" else "关"
        try:
            choice = read_input(
                f"音频验证当前：{current}（{AUDIO_VALIDATION_MODE}）。"
                "请选择 [on=开 / off=关 / required=严格]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]未修改音频验证模式。[/dim]")
            return
    aliases = {
        "on": "auto", "开": "auto", "开启": "auto", "auto": "auto",
        "off": "off", "关": "off", "关闭": "off",
        "required": "required", "strict": "required", "严格": "required",
    }
    mode = aliases.get(choice)
    if mode is None:
        console.print("[yellow]用法：/audio [on|off|required][/yellow]")
        return
    AUDIO_VALIDATION_MODE = mode
    labels = {"auto": "已开启（失败时降级，不阻塞主任务）",
              "off": "已关闭（跳过麦克风工具）",
              "required": "严格模式（音频失败会阻塞任务）"}
    console.print(f"[green]✅ 音频验证{labels[mode]}。本设置仅对当前运行会话生效。[/green]")


def cmd_doc(arg: str):
    """导入硬件说明文档为技能。文档须符合 SKILL.md 格式：
    frontmatter 含 name（kebab-case）与 description（何时加载）。"""
    path = Path(arg.strip()).expanduser()
    if not arg.strip():
        console.print("[yellow]用法：/doc <硬件说明md路径>[/yellow]\n"
                      "文档格式要求（缺一不可）：\n"
                      "  ---\n  name: 短横线小写英文名\n"
                      "  description: 一句话说明什么任务该加载本技能\n"
                      "  ---\n  正文（Markdown）")
        return
    if not path.exists():
        console.print(f"[red]文件不存在: {path}[/red]")
        return
    parsed = SKILLS.parse(path)
    if parsed is None:
        console.print(f"[red]格式不合规：{path.name} 缺少 frontmatter 或 name/description。[/red]\n"
                      "要求开头为：\n  ---\n  name: xxx\n  description: xxx\n  ---")
        return
    name = parsed["name"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", name):
        console.print(f"[red]name 不合规：'{name}'（需小写字母/数字/短横线）[/red]")
        return
    dest = SKILLS_DIR / name / "SKILL.md"
    if dest.exists():
        console.print(f"[yellow]技能 '{name}' 已存在，将覆盖更新。[/yellow]")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    SKILLS.reload()
    console.print(f"[green]✅ 已导入技能 '{name}'：{parsed['description']}[/green]\n"
                  f"   存放于 {dest}，立即生效（/skill 可查看）")


def cmd_model(arg: str = ""):
    """查看或切换当前会话使用的大模型。"""
    arg = arg.strip()
    if not arg:
        table = Table(title="可用模型（Tab 补全选择）", border_style="dim")
        table.add_column("状态", style="green", no_wrap=True)
        table.add_column("别名", style="cyan")
        table.add_column("提供者")
        table.add_column("Base URL")
        for alias, cfg in MODEL_REGISTRY.items():
            marker = "●" if alias == current_model_alias() else "○"
            table.add_row(marker, alias, cfg["provider"], cfg["base_url"])
        console.print(table)
        console.print(f"[dim]当前：{current_model_alias()}[/dim]")
        # 模型选择：临时切换 completer 以支持模型名 Tab 补全
        alias = ""
        if _session is not None:
            try:
                from prompt_toolkit.completion import WordCompleter
                model_comp = WordCompleter(list(MODEL_REGISTRY.keys()))
                old_comp = _session.completer
                _session.completer = model_comp
                try:
                    alias = _session.prompt("选择模型（Tab 补全）: ").strip()
                finally:
                    _session.completer = old_comp
            except (EOFError, KeyboardInterrupt):
                return
        if not alias:
            try:
                alias = read_input("选择模型: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if not alias:
            return
        arg = alias
    if switch_model(arg):
        if load_api_key():
            cfg = current_model_config()
            masked = API_KEY[:3] + "*" * 6 + API_KEY[-2:]
            console.print(f"[green]✅ 已切换为 {current_model_alias()} ({cfg['provider']}) — Key: {masked}[/green]")
        else:
            cfg = current_model_config()
            console.print(f"[yellow]⚠ 模型 '{arg}' 缺少 {cfg['api_key_env']}[/yellow]")
            try:
                new_key = read_input(f"请输入 {cfg['api_key_env']}（直接回车取消）: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if new_key:
                _save_api_key(cfg['api_key_env'], new_key)
                load_api_key()
                masked = API_KEY[:3] + "*" * 6 + API_KEY[-2:]
                console.print(f"[green]✅ Key 已保存，{arg} 可用。Key: {masked}[/green]")
    else:
        available = ", ".join(MODEL_REGISTRY)
        console.print(f"[red]❌ 未知模型 '{rich_escape(arg)}'。可用：{available}[/red]")


def cmd_exit():
    """退出 Yuanshen。"""
    console.print("[dim]👋 已退出 Yuanshen。[/dim]")
    sys.exit(0)


def slugify(text: str) -> str:
    """将文本转为文件系统友好的短横线名。"""
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:30].strip("-").lower() or "project"


def _display_path(p: Path) -> str:
    """尽量显示相对 SCRIPT_DIR 的短路径；不在其下（如 ~/.yuanshen/projects）则显示绝对路径。"""
    try:
        return str(p.relative_to(SCRIPT_DIR))
    except ValueError:
        return str(p)


def cmd_new_project(arg: str = ""):
    """新建 ESP32 项目。"""
    name = arg.strip()
    if not name:
        try:
            name = read_input("📁 项目名称: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
    if not name:
        console.print("[yellow]已取消[/yellow]")
        return None

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_dir = PROJECTS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slugify(name)}"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "rounds").mkdir(exist_ok=True)
    # 复制全局 wiring.md 到项目目录
    if WIRING_FILE.exists():
        shutil.copy2(WIRING_FILE, project_dir / "wiring.md")
    else:
        (project_dir / "wiring.md").write_text("（无接线）\n")

    console.print(f"[green]✅ 项目 '{name}' 已创建[/green]")
    console.print(f"[dim]   {_display_path(project_dir)}[/dim]")
    console.print("[cyan]请输入你的 ESP32 需求，Agent 会先输出接线文档，确认后开始实施。[/cyan]")
    return project_dir


def cmd_history():
    """浏览历史项目。"""
    projects = sorted(PROJECTS_DIR.iterdir()) if PROJECTS_DIR.exists() else []
    if not projects:
        console.print("[yellow]暂无历史项目。[/yellow]")
        return

    console.print("[bold]历史项目：[/bold]")
    for i, p in enumerate(projects, 1):
        req_file = p / "requirement.md"
        req_preview = req_file.read_text().splitlines()[0][:60] if req_file.exists() else "(无需求)"
        console.print(f"  [dim]{i}.[/dim] [cyan]{p.name}[/cyan] — {req_preview}")

    try:
        choice = read_input("选择项目编号（回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    if choice.isdigit() and 1 <= int(choice) <= len(projects):
        selected = projects[int(choice) - 1]
        req_text = (selected / "requirement.md").read_text() if (
            selected / "requirement.md").exists() else "（无记录）"
        console.print(Panel(req_text, title=f"📖 {selected.name}", border_style="cyan"))
    else:
        console.print("[yellow]已取消[/yellow]")


def handle_free_chat(user_input: str):
    """自由对话模式：直接调用 LLM 回复，不启动项目流程。"""
    console.print("[dim]自由对话模式 — 输入 /new 创建项目开始 ESP32 开发。[/dim]")
    try:
        response = llm_create(
            model=current_model_alias(),
            max_tokens=2000,
            system="你是一个 ESP32 开发助手。用户处于自由对话模式。"
                   "简短回答即可。如果需要开始项目，请提示用户使用 /new 命令。",
            messages=[{"role": "user", "content": user_input}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text.strip():
                if sys.stdout.isatty():
                    _render_assistant_text(block.text)
                else:
                    print(block.text)
    except Exception as e:
        console.print(f"[red]❌ {e}[/red]")


def cmd_api_key(arg: str = ""):
    """查看或更新当前模型的 API Key。"""
    arg = arg.strip()
    cfg = current_model_config()
    env_name = cfg["api_key_env"]

    if arg:
        _save_api_key(env_name, arg)
        load_api_key()
        masked = API_KEY[:3] + "*" * 6 + API_KEY[-2:]
        console.print(f"[green]✅ {env_name} 已更新: {masked}[/green]")
        return

    current = API_KEY[:3] + "****" + API_KEY[-2:] if API_KEY else "（未设置）"
    console.print(f"[dim]当前 {env_name}: {current}[/dim]")
    try:
        new_key = read_input(f"输入新的 {env_name}（直接回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if new_key:
        _save_api_key(env_name, new_key)
        load_api_key()
        masked = API_KEY[:3] + "*" * 6 + API_KEY[-2:]
        console.print(f"[green]✅ {env_name} 已更新: {masked}[/green]")
    else:
        console.print("[yellow]已取消[/yellow]")


COMMANDS = {"/tool": cmd_tool, "/skill": cmd_skill,
            "/work": cmd_work, "/wiring": cmd_wiring,
            "/audio": cmd_audio, "/model": cmd_model,
            "/api-key": cmd_api_key, "/port": cmd_port, "/exit": cmd_exit}


# =============================================================================
# 主 REPL
# =============================================================================


BANNER_ART = r"""
██╗░░░██╗██╗░░░░░██╗░░░░░░█████╗░███╗░░██╗░██████╗██╗░░██╗███████╗███╗░░██╗
╚██╗░██╔╝██║░░░░░██║░░░░░██╔══██╗████╗░██║██╔════╝██║░░██║██╔════╝████╗░██║
░╚████╔╝░██║░░░░░██║░░░░░███████║██╔██╗██║╚█████╗░███████║█████╗░░██╔██╗██║
░░╚██╔╝░░██║░░░░░██║░░░░░██╔══██║██║╚████║░╚═══██╗██╔══██║██╔══╝░░██║╚████║
░░░██║░░░███████╗███████╗██║░░██║██║░╚███║██████╔╝██║░░██║███████╗██║░╚███║
░░░╚═╝░░░╚══════╝╚══════╝╚═╝░░╚═╝╚═╝░░╚══╝╚═════╝░╚═╝░░╚═╝╚══════╝╚═╝░░╚══╝
"""


def main():
    has_key_loaded = load_api_key()
    init_mcp()
    
    # ASCII art 启动画面
    art = Text(BANNER_ART, style="cyan")
    model_line = Text()
    if has_key_loaded:
        cfg = current_model_config()
        masked = API_KEY[:3] + "*" * 6 + API_KEY[-2:]
        model_line.append(f"模型: {current_model_alias()} ({cfg['provider']}) — Key: {masked}", style="bold yellow")
    else:
        cfg = current_model_config()
        model_line.append(f"⚠ 暂无可用 Key（存好 {cfg['api_key_env']} 后重新运行）", style="yellow")
    
    console.print(Panel(
        Text.assemble(art, "\n", model_line),
        border_style="cyan",
        subtitle=f"ESP32 MicroPython Agent v{APP_VERSION}",
    ))

    pending_context = None
    current_project_dir = None           # 当前项目目录
    while True:
        try:
            user_input = read_input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break
        
        # --- 斜杠命令分发 ---
        if user_input in COMMANDS:
            COMMANDS[user_input]()
            print()
            continue
        if user_input.startswith("/audio "):
            cmd_audio(user_input[7:])
            print()
            continue
        if user_input.startswith("/port "):
            cmd_port(user_input[6:])
            print()
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            cmd_model(user_input[6:])
            print()
            continue
        if user_input == "/doc" or user_input.startswith("/doc "):
            cmd_doc(user_input[4:])
            print()
            continue
        if user_input == "/new" or user_input.startswith("/new "):
            prefix = "/new"
            arg = user_input[len(prefix):].strip()
            proj = cmd_new_project(arg)
            if proj:
                current_project_dir = proj
            print()
            continue
        if user_input == "/history":
            cmd_history()
            print()
            continue
        if user_input.startswith("/"):
            console.print(f"[red]未知命令 {rich_escape(user_input)}[/red]。可用: "
                          f"[yellow]{', '.join(list(COMMANDS) + ['/audio on|off|required', '/model <alias>', '/doc <md路径>', '/port <串口>', '/new 项目名', '/history'])}[/yellow]\n")
            continue
        if not API_KEY:
            console.print(key_guidance() + "\n")
            continue

        # 用户输入回显（绿色）
        console.print(f"[bold green]◉ {user_input}[/bold green]")

        if current_project_dir:
            # 项目模式：规范化 → 确认 → 实施（沿用原有流程）
            confirmed = confirm_normalized_task(user_input)
            if confirmed is None:
                print()
                continue
            task_start = time.monotonic()
            normalized_requirement, _normalized_wiring, audio_required = confirmed

            content = normalized_requirement
            if pending_context:
                content = (f"{pending_context}\n\n"
                           f"【用户确认后的本轮规范化需求】{normalized_requirement}")
                pending_context = None
            TODO.start(normalized_requirement, audio_required)
            global CURRENT_TASK_DIR
            CURRENT_TASK_DIR = current_project_dir
            write_requirement(CURRENT_TASK_DIR, normalized_requirement)
            messages = [{
                "role": "user",
                "content": build_user_prompt(
                    content, round_no=1, elapsed=0,
                    previous_result="任务开始", previous_tools="无",
                ),
            }]
            console.print(f"[dim][项目实施][/dim] {_display_path(CURRENT_TASK_DIR)}")
            run_log = {"rounds": [], "prompt_input": content}
            try:
                agent_loop(messages, run_log, task_start)
            except Exception as e:
                console.print(f"[red]Error: {rich_escape(str(e))}[/red]")
                run_log.setdefault("final_text", f"(异常中止: {e})")
            console.print("\n[dim]⏳ 正在保存最终代码与完整 user prompt，并提取经验…[/dim]")
            try:
                flow_md = render_flow_md(normalized_requirement, run_log)
                run_dir = archive_run(
                    normalized_requirement, CURRENT_TASK_DIR, messages,
                    run_log.get("system_prompt", ""),
                )
                note, pending = extract_skill(flow_md, run_dir)
                if pending:
                    pending_context = pending
                console.print(f"[归档] {_display_path(run_dir)} | {note}")
                console.print("✅ [green]最终代码与完整 user prompt 已保存，可以继续提问或退出。[/green]")
            except Exception as e:
                console.print(f"[red][归档失败][/red] {e}")
            console.print("\n" + "━" * 50, style="orange1")
            print()
        else:
            # 无项目：自由对话模式
            handle_free_chat(user_input)
            console.print("\n" + "━" * 50, style="orange1")
            print()


if __name__ == "__main__":
    main()
