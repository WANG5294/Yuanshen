#!/usr/bin/env python3
"""Yuanshen v1.0 正式版 —— ESP32 单片机开发 agent

入口文件。核心实现已迁移到 yuanshen/ 包：
- config      配置与全局状态
- models      大模型客户端与 API Key
- mcp_client  MCP 最小客户端
- skills      Skill 知识库
- todos       主线任务状态
- prompts     System/User Prompt 构建
- tools       本地工具与 MCP 路由
- ui          终端 UI 渲染
- agent       需求规范化、Agent 循环、归档、经验提取
"""

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table


def _bootstrap_interpreter():
    """缺依赖时原地 re-exec 到可用的 venv。
    优先本目录 .venv（npm 安装场景），其次开发环境的 piano_workflow/.venv。"""
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

# 核心包导入
import yuanshen.config as _cfg
from yuanshen.config import (
    FILES_DIR,
    MODEL_REGISTRY,
    PROJECTS_DIR,
    SKILLS_DIR,
    WIRING_FILE,
    console,
    read_wiring,
)
from yuanshen.mcp_client import MCP_CLIENTS, MCP_TOOL_DEFS
from yuanshen.models import (
    _save_api_key,
    current_model_alias,
    current_model_config,
    has_key,
    key_guidance,
    load_api_key,
    switch_model,
)
from yuanshen.skills import SKILLS
from yuanshen.todos import TODO
from yuanshen.tools import base_tools
from yuanshen.ui import (
    SLASH_COMMANDS_META,
    SLASH_COMMANDS_WORDS,
    _render_assistant_text,
    read_input,
    show_help,
    show_startup_panel,
    show_work_dashboard,
)
from yuanshen.agent import (
    confirm_normalized_task,
    execute_project_task,
    run_a2a_mode,
    switch_port,
)
from yuanshen.utils import slugify


# =============================================================================
# 斜杠命令
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
        from serial.tools import list_ports
        ports = sorted(p.device for p in list_ports.comports())
    except Exception:
        ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")

    mp = shutil.which("mpremote")
    board = False
    if ports and mp:
        try:
            import subprocess
            r = subprocess.run(["mpremote", "connect", ports[0], "exec", "print('pong')"],
                               capture_output=True, text=True, timeout=8)
            board = "pong" in r.stdout
        except Exception:
            pass

    mic = ""
    if "mic_check" in MCP_CLIENTS:
        try:
            out = MCP_CLIENTS["mic_check"].call_tool("mic_check", {})
            mic = out
        except Exception as e:
            mic = f"Error: {e}"
    else:
        mic = "MCP 工具未注册"

    port_hint = "检查USB线" if sys.platform == "win32" else "检查USB线/dialout权限"
    show_work_dashboard(
        ports=ports,
        mpremote=mp,
        board=board,
        mic=mic,
        skills_count=len(SKILLS.skills),
        audio_mode=_cfg.AUDIO_VALIDATION_MODE,
        api_ok=has_key(),
        port_hint=port_hint,
    )


def cmd_wiring():
    console.print(Panel(read_wiring(), title=f"当前接线（{WIRING_FILE}）",
                        border_style="green"))
    console.print("[dim]普通任务开始前会同时规范化需求与本文件，并在你确认后覆盖更新；"
                  "也可直接编辑该文件，下一次规范化会读取最新内容。[/dim]")


def cmd_port(arg: str = ""):
    """查看或切换连接 ESP32 的串口。"""
    switch_port(arg)


def cmd_audio(arg: str = ""):
    """查看或切换当前会话的音频验收模式。"""
    choice = arg.strip().lower()
    if not choice:
        current = "开" if _cfg.AUDIO_VALIDATION_MODE != "off" else "关"
        try:
            choice = read_input(
                f"音频验证当前：{current}（{_cfg.AUDIO_VALIDATION_MODE}）。"
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
    _cfg.AUDIO_VALIDATION_MODE = mode
    labels = {"auto": "已开启（失败时降级，不阻塞主任务）",
              "off": "已关闭（跳过麦克风工具）",
              "required": "严格模式（音频失败会阻塞任务）"}
    console.print(f"[green]✅ 音频验证{labels[mode]}。本设置仅对当前运行会话生效。[/green]")


def cmd_doc(arg: str):
    """导入硬件说明文档为技能。"""
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
        alias = ""
        from yuanshen.ui import _session
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
            console.print(f"[green]✅ 已切换为 {current_model_alias()} ({current_model_config()['provider']})[/green]")
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
                console.print(f"[green]✅ Key 已保存，{arg} 可用。[/green]")
    else:
        available = ", ".join(MODEL_REGISTRY)
        console.print(f"[red]❌ 未知模型 '{rich_escape(arg)}'。可用：{available}[/red]")


def cmd_api_key(arg: str = ""):
    """查看或更新当前模型的 API Key。"""
    arg = arg.strip()
    cfg = current_model_config()
    env_name = cfg["api_key_env"]

    if arg:
        _save_api_key(env_name, arg)
        load_api_key()
        key = _cfg.API_KEY
        masked = key[:3] + "*" * 6 + key[-2:] if key else "（未设置）"
        console.print(f"[green]✅ {env_name} 已更新: {masked}[/green]")
        return

    key = _cfg.API_KEY
    current = key[:3] + "****" + key[-2:] if key else "（未设置）"
    console.print(f"[dim]当前 {env_name}: {current}[/dim]")
    try:
        new_key = read_input(f"输入新的 {env_name}（直接回车取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if new_key:
        _save_api_key(env_name, new_key)
        load_api_key()
        key = _cfg.API_KEY
        masked = key[:3] + "*" * 6 + key[-2:] if key else "（未设置）"
        console.print(f"[green]✅ {env_name} 已更新: {masked}[/green]")
    else:
        console.print("[yellow]已取消[/yellow]")


def cmd_exit():
    """退出 Yuanshen。"""
    console.print("[dim]👋 已退出 Yuanshen。[/dim]")
    sys.exit(0)


def _display_path(p: Path) -> str:
    """尽量显示相对 SCRIPT_DIR 的短路径。"""
    from yuanshen.config import SCRIPT_DIR
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
        from yuanshen.models import llm_create_stream
        from rich.live import Live
        from rich.markdown import Markdown
        tty = sys.stdout.isatty()
        parts = []
        live = (Live(Markdown(""), refresh_per_second=20,
                     vertical_overflow="visible") if tty else None)
        if live:
            live.start()
        try:
            for ev in llm_create_stream(
                model=current_model_alias(),
                max_tokens=2000,
                system="你是一个 ESP32 开发助手。用户处于自由对话模式。"
                       "简短回答即可。如果需要开始项目，请提示用户使用 /new 命令。",
                messages=[{"role": "user", "content": user_input}],
            ):
                if ev[0] == "text":
                    parts.append(ev[1])
                    if live:
                        live.update(Markdown("".join(parts)))
        except Exception as e:
            console.print(f"[red]❌ {e}[/red]")
        finally:
            if live:
                live.stop()
        text = "".join(parts)
        if text.strip() and not tty:
            print(text)
    except Exception as e:
        console.print(f"[red]❌ {e}[/red]")


COMMANDS = {
    "/tool": cmd_tool,
    "/skill": cmd_skill,
    "/work": cmd_work,
    "/wiring": cmd_wiring,
    "/audio": cmd_audio,
    "/model": cmd_model,
    "/api-key": cmd_api_key,
    "/port": cmd_port,
    "/help": None,  # 动态判断，见主循环
    "/exit": cmd_exit,
}


# =============================================================================
# 主 REPL
# =============================================================================


def main():
    if "--a2a" in sys.argv:
        run_a2a_mode()
        return
    has_key_loaded = load_api_key()
    from yuanshen.mcp_client import init_mcp
    init_mcp()

    show_startup_panel(has_key_loaded)

    pending_context = None
    current_project_dir = None
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
        if user_input in COMMANDS and COMMANDS[user_input] is not None:
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
        if user_input == "/help":
            show_help(has_project=current_project_dir is not None)
            print()
            continue
        if user_input.startswith("/"):
            console.print(f"[red]未知命令 {rich_escape(user_input)}[/red]。可用: "
                          f"[yellow]{', '.join(list(COMMANDS) + ['/audio on|off|required', '/model <alias>', '/doc <md路径>', '/port <串口>', '/new 项目名', '/history'])}[/yellow]\n")
            continue
        if not _cfg.API_KEY:
            console.print(key_guidance() + "\n")
            continue

        # 用户输入回显
        console.print(f"[bold green]◉ {user_input}[/bold green]")

        if current_project_dir:
            confirmed = confirm_normalized_task(user_input)
            if confirmed is None:
                print()
                continue
            normalized_requirement, _normalized_wiring, audio_required = confirmed

            content = normalized_requirement
            if pending_context:
                content = (f"{pending_context}\n\n"
                           f"【用户确认后的本轮规范化需求】{normalized_requirement}")
                pending_context = None
            run_log = execute_project_task(
                normalized_requirement, audio_required,
                current_project_dir, content,
            )
            if run_log.get("pending_skill_context"):
                pending_context = run_log["pending_skill_context"]
            console.print("\n" + "━" * 50, style="orange1")
            print()
        else:
            handle_free_chat(user_input)
            console.print("\n" + "━" * 50, style="orange1")
            print()


if __name__ == "__main__":
    main()
