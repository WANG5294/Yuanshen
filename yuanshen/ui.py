"""终端 UI 渲染、输入与动画效果。"""
import re
import sys
import time

from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

try:
    from prompt_toolkit import PromptSession, prompt as terminal_prompt
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.completion import WordCompleter
    _HAS_PT = True
except ImportError:
    PromptSession = None
    terminal_prompt = None
    _HAS_PT = False

from yuanshen.config import APP_VERSION, ESP32_PORT, YUANSHEN_DIR, console
from yuanshen.config import API_KEY
from yuanshen.models import current_model_alias, current_model_config, has_key
from yuanshen.todos import MAINLINE, TODO


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


BANNER_ART = r"""
██╗░░░██╗██╗░░░░░██╗░░░░░░█████╗░███╗░░██╗░██████╗██╗░░██╗███████╗███╗░░██╗
╚██╗░██╔╝██║░░░░░██║░░░░░██╔══██╗████╗░██║██╔════╝██║░░██║██╔════╝████╗░██║
░╚████╔╝░██║░░░░░██║░░░░░███████║██╔██╗██║╚█████╗░███████║█████╗░░██╔██╗██║
░░╚██╔╝░░██║░░░░░██║░░░░░██╔══██║██║╚████║░╚═══██╗██╔══██║██╔══╝░░██║╚████║
░░░██║░░░███████╗███████╗██║░░██║██║░╚███║██████╔╝██║░░██║███████╗██║░╚███║
░░░╚═╝░░░╚══════╝╚══════╝╚═╝░░╚═╝╚═╝░░╚══╝╚═════╝░╚═╝░░╚═╝╚══════╝╚═╝░░╚══╝
"""


def _model_names() -> list:
    """返回当前注册的模型别名列表（延迟获取）。"""
    from yuanshen.config import MODEL_REGISTRY
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
    if not text.strip():
        return
    if not sys.stdout.isatty():
        print(text)
        return

    # 按标点/换行分割成自然段
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


# =============================================================================
# 新增：品牌化启动页
# =============================================================================

def show_startup_panel(has_key_loaded: bool) -> None:
    """启动页：品牌 Banner + 状态徽章分栏。"""
    art = Text(BANNER_ART, style="bold cyan")
    # 为 Banner 做青→橙的简单渐变（按行）
    banner_lines = BANNER_ART.strip("\n").splitlines()
    gradient_styles = ["cyan", "cyan", "bright_cyan", "yellow", "orange1", "red"]
    art_grad = Text()
    for i, line in enumerate(banner_lines):
        style = gradient_styles[min(i, len(gradient_styles) - 1)]
        art_grad.append(line + "\n", style=f"bold {style}")

    # 状态徽章
    if has_key_loaded:
        key_badge = Text("✓ API Key 已配置", style="bold green")
    else:
        key_badge = Text("⚠ API Key 未配置", style="bold yellow")

    model_text = Text()
    cfg = current_model_config()
    model_text.append(f"模型: ", style="dim")
    model_text.append(current_model_alias(), style="bold cyan")
    model_text.append(f" ({cfg['provider']})", style="dim")

    port_text = Text()
    port_text.append("串口: ", style="dim")
    port_text.append(ESP32_PORT, style="cyan")
    if ESP32_PORT == "auto":
        port_text.append("（自动探测）", style="dim")

    audio_text = Text()
    audio_text.append("音频: ", style="dim")
    mode = TODO.audio_degraded_reason or "可用"
    audio_text.append(mode, style="cyan" if not TODO.audio_degraded_reason else "yellow")

    status_grid = Columns([
        Panel(key_badge, border_style="green" if has_key_loaded else "yellow"),
        Panel(model_text, border_style="cyan"),
        Panel(port_text, border_style="cyan"),
    ], equal=True)

    console.print(Panel(
        Group(art_grad, status_grid),
        border_style="cyan",
        subtitle=f"ESP32 MicroPython Agent v{APP_VERSION}",
    ))

    if not has_key_loaded:
        guide = Text()
        guide.append("提示：使用 ", style="dim")
        guide.append("/api-key", style="bold yellow")
        guide.append(" 命令输入 Key，或编辑 ", style="dim")
        guide.append(str(YUANSHEN_DIR / ".env"), style="yellow")
        console.print(guide)


# =============================================================================
# 新增：需求确认页
# =============================================================================

def show_confirm_panels(requirement: str, wiring: str, audio_required: bool,
                        original_wiring: str = "") -> None:
    """左右分栏展示待确认的需求与接线。"""
    top = Text()
    if audio_required:
        top.append("🎤 本任务需要麦克风音频闭环验证", style="bold magenta")
        console.print(top)
        console.print()

    req_panel = Panel(
        Markdown(requirement),
        title="📋 待确认：规范化需求",
        border_style="cyan",
        padding=(1, 2),
    )
    wiring_body = wiring
    if original_wiring and original_wiring != wiring:
        # 简单高亮变更：以原始为准，标记新增/删除行
        try:
            import difflib
            orig_lines = original_wiring.splitlines()
            new_lines = wiring.splitlines()
            diff = list(difflib.unified_diff(orig_lines, new_lines, lineterm=""))
            if diff:
                # 只取有变更的部分作为提示，但仍显示完整 wiring
                wiring_body = "```diff\n" + "\n".join(diff[:30]) + "\n```\n\n" + wiring
        except Exception:
            pass
    wiring_panel = Panel(
        Markdown(wiring_body),
        title="🔌 待确认：规范化接线",
        border_style="green",
        padding=(1, 2),
    )
    console.print(Columns([req_panel, wiring_panel]))


def show_confirm_menu() -> None:
    """显示菜单式确认选项。"""
    console.print()
    console.print("[bold]请选择操作：[/bold]")
    console.print("  [cyan][1][/cyan] 确认并启动")
    console.print("  [cyan][2][/cyan] 提出修改意见（将重新优化）")
    console.print("  [cyan][3][/cyan] 切换串口")
    console.print("  [cyan][4][/cyan] 取消任务")


# =============================================================================
# 新增：主线进度条
# =============================================================================

def render_progress_bar(items: list = None) -> None:
    """渲染常驻主线进度条。"""
    if items is None:
        items = TODO.items or [{"content": s, "status": "pending"} for s in MAINLINE]
    if not sys.stdout.isatty():
        return
    parts = []
    for item in items:
        content = item.get("content", "")
        status = item.get("status", "pending")
        if status == "completed":
            parts.append(f"[bold green]✓ {content}[/bold green]")
        elif status == "in_progress":
            parts.append(f"[bold blue]▶ {content}[/bold blue]")
        else:
            parts.append(f"[dim]○ {content}[/dim]")
    bar = "  →  ".join(parts)
    console.print(Panel(bar, title="主线进度", border_style="dim", padding=(0, 2)))


# =============================================================================
# 新增：轮次结果卡片
# =============================================================================

def show_round_card(round_no: int, elapsed: int, tool_names: str,
                    purpose: str, status: str, details: list = None) -> None:
    """以卡片形式展示一轮结果（替代单行覆盖）。"""
    style_map = {"成功": "green", "失败": "red", "跳过": "yellow", "执行中": "blue"}
    border = style_map.get(status, "white")
    content = Text()
    content.append(f"工具：{tool_names}\n", style="yellow")
    content.append(f"目的：{purpose}\n", style="white")
    if details:
        content.append("\n", style="")
        for label, kind, outcome, brief in details:
            outcome_style = {"success": "green", "failed": "red", "skipped": "yellow"}.get(outcome, "white")
            content.append(f"  • {label}（{kind}）", style="white")
            content.append(f" → {outcome}\n", style=outcome_style)
            if brief:
                content.append(f"     {brief}\n", style="dim")
    console.print(Panel(
        content,
        title=f"第 {round_no} 轮｜{elapsed}s｜{status}",
        border_style=border,
        padding=(0, 2),
    ))


# =============================================================================
# 新增：最终报告
# =============================================================================

def render_final_report(task_dir, run_log: dict, requirement: str = "") -> None:
    """模板化展示任务最终报告。"""
    completed = TODO.is_complete()
    summary = "任务已完成" if completed else "任务未完成或部分完成"
    summary_style = "green" if completed else "yellow"

    console.print(Panel(
        f"[bold {summary_style}]{summary}[/bold {summary_style}]",
        title="📋 摘要",
        border_style=summary_style,
    ))

    console.print(Panel(
        f"[dim]{task_dir}[/dim]\n[dim]main.py[/dim]",
        title="💾 烧录位置",
        border_style="cyan",
    ))

    evidence = []
    if TODO.deployed_main:
        evidence.append(f"[green]✓[/green] 已部署为板上 main.py")
    else:
        evidence.append(f"[red]✗[/red] 尚未部署为板上 main.py")
    if TODO.device_verified:
        evidence.append(f"[green]✓[/green] 已执行板上 main.py 验证")
    else:
        evidence.append(f"[red]✗[/red] 尚无实机执行证据")
    if TODO.requires_audio_validation():
        if TODO.audio_degraded_reason:
            evidence.append(f"[yellow]⚠[/yellow] 音频：{TODO.audio_degraded_reason}")
        else:
            evidence.append(f"[green]✓[/green] 音频闭环通过")
    console.print(Panel(
        "\n".join(evidence),
        title="🔍 验证结果",
        border_style="green" if completed else "yellow",
    ))

    if not completed:
        console.print(Panel(
            "1. 检查串口连接与权限\n"
            "2. 查看 /work 环境探测\n"
            "3. 查看任务目录 rounds/ 逐轮快照",
            title="💡 下一步建议",
            border_style="dim",
        ))


# =============================================================================
# 新增：/work 健康仪表盘
# =============================================================================

def show_work_dashboard(ports, mpremote, board, mic, skills_count,
                        audio_mode, api_ok, port_hint):
    """环境探测结果的健康仪表盘。"""
    table = Table(title="🔍 当前环境能力探测", border_style="dim")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("状态")
    table.add_column("修复建议")

    def row(status_ok: bool, label: str, detail: str, fix: str = ""):
        badge = "[green]✓[/green]" if status_ok else "[red]✗[/red]"
        table.add_row(label, f"{badge} {detail}", fix)

    row(bool(ports), "串口设备",
        ", ".join(ports) if ports else "未发现",
        "" if ports else ("检查 USB 线" if sys.platform == "win32" else "检查 USB 线 / dialout 权限"))
    row(bool(mpremote), "mpremote", mpremote or "未安装",
        "" if mpremote else "pip install mpremote")
    row(board, "板子 REPL 响应", "可交互" if board else "无响应",
        "" if board else "检查板子是否接通、程序是否占用串口")
    row(board, "上传/运行/删除", "可用" if board else "依赖上面三项",
        "")
    mic_ok = mic and mic.startswith("麦克风正常")
    row(mic_ok, "麦克风闭环",
        mic.splitlines()[0] if mic else "未注册",
        "" if mic_ok else "VirtualBox 请勾选音频输入；其他系统检查录音设备")
    row(skills_count > 0, "技能知识库",
        f"{skills_count} 个分块" if skills_count else "无",
        "")
    row(api_ok, "大模型 API",
        current_model_alias() if api_ok else "缺少 Key",
        "" if api_ok else "使用 /api-key 设置")
    row(False, "固件烧录", "安全红线，永久禁止", "")

    console.print(table)


# =============================================================================
# 新增：/help 命令菜单
# =============================================================================

def show_help(has_project: bool = False):
    """分组展示可用命令。"""
    table = Table(title="Yuanshen 命令列表", border_style="cyan")
    table.add_column("分组", style="bold")
    table.add_column("命令", style="cyan")
    table.add_column("说明")

    env_cmds = ["/work", "/tool", "/skill", "/model", "/api-key"]
    hw_cmds = ["/wiring", "/port", "/audio", "/doc"]
    proj_cmds = ["/new", "/history"]
    other_cmds = ["/help", "/exit"]

    table.add_row("环境", ", ".join(env_cmds), "检查环境、查看工具/技能/切换模型和 Key")
    table.add_row("硬件", ", ".join(hw_cmds), "查看接线、串口、音频模式、导入文档")
    if has_project:
        table.add_row("项目", ", ".join(proj_cmds), "创建新项目、浏览历史项目（当前已有项目）")
    else:
        table.add_row("项目", ", ".join(proj_cmds), "创建新项目、浏览历史项目")
    table.add_row("其他", ", ".join(other_cmds), "显示帮助、退出程序")

    console.print(table)
    if not has_project:
        console.print("[dim]提示：输入 /new 项目名 开始 ESP32 开发。[/dim]")
