"""Agent 闭环：需求规范化、确认、主循环、归档、经验提取。"""
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.markup import escape as rich_escape

import yuanshen.config as _cfg
from yuanshen.config import (
    APP_VERSION,
    ESP32_REFERENCE_FILE,
    FILES_DIR,
    MAX_ITERATIONS,
    MODEL_REGISTRY,
    PROJECTS_DIR,
    SKILLS_DIR,
    WIRING_FILE,
    YUANSHEN_DIR,
    read_esp32_reference,
    read_wiring,
    console,
)
from yuanshen.mcp_client import MCP_CLIENTS
from yuanshen.config import API_KEY
from yuanshen.models import (
    _save_api_key,
    _TextBlock,
    _ThinkingBlock,
    _ToolUseBlock,
    _UnifiedResponse,
    current_model_alias,
    current_model_config,
    llm_create,
    llm_create_stream,
    load_api_key,
)
from yuanshen.prompts import build_system, build_user_prompt, render_flow_md, render_userprompt_md
from yuanshen.skills import SKILLS
from yuanshen.todos import TODO
from yuanshen.tools import execute_tool, get_all_tools
from yuanshen.ui import (
    _animate_markdown,
    _render_assistant_text,
    _round_purpose,
    _show_round_result,
    _tool_outcome,
    read_input,
    render_final_report,
    render_progress_bar,
    show_confirm_menu,
    show_confirm_panels,
    show_round_card,
)
from yuanshen.utils import _jsonable_content, _text_of, slugify


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
    if _cfg.CURRENT_TASK_DIR is not None:
        project_wiring = _cfg.CURRENT_TASK_DIR / "wiring.md"
        project_wiring.parent.mkdir(parents=True, exist_ok=True)
        wiring_file = project_wiring
    tmp = wiring_file.with_name(wiring_file.name + ".tmp")
    tmp.write_text(wiring.rstrip() + "\n")
    tmp.replace(wiring_file)


def switch_port(arg: str) -> None:
    """查看或切换连接 ESP32 的串口（持久化到 ~/.yuanshen/.env）。"""
    arg = arg.strip()
    if not arg:
        current = _cfg.ESP32_PORT + ("（mpremote 自动探测）" if _cfg.ESP32_PORT == "auto" else "")
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
    _cfg.ESP32_PORT = arg
    os.environ["ESP32_PORT"] = arg
    _save_api_key("ESP32_PORT", arg)
    client = MCP_CLIENTS.get("set_port")
    if client is not None:
        try:
            console.print(f"[dim]{client.call_tool('set_port', {'port': arg})}[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠ 已保存，但热更新 MCP 服务器失败：{e}（重启后生效）[/yellow]")
    console.print(f"[green]✅ 串口已切换为: {arg}"
                  + ("（自动探测）" if arg == "auto" else "") + "[/green]")


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
            show_confirm_panels(requirement, wiring, audio_required, original_wiring)
            port_line = _cfg.ESP32_PORT + ("（mpremote 自动探测）" if _cfg.ESP32_PORT == "auto" else "")
            console.print(f"[dim]🔌 连接串口: {port_line}[/dim]")
            show_confirm_menu()
            try:
                answer = read_input(
                    "请输入选项 [1-4] 或修改意见: ").strip()
            except (EOFError, KeyboardInterrupt):
                answer = "4"
            if answer == "3" or answer.lower().startswith("port"):
                port_arg = answer[4:].strip() if answer.lower().startswith("port") else ""
                switch_port(port_arg)
                continue
            break

        if answer in ("1", "y", "yes", "是", "确认"):
            try:
                write_wiring(wiring)
            except OSError as e:
                console.print(f"[red]❌ wiring.md 写入失败：{e}[/red]")
                console.print("[dim]本次任务未进入 Agent 循环。[/dim]")
                return None
            console.print(f"[green]✅ 已确认并更新 {WIRING_FILE.name}，即将以规范化需求启动 Agent。[/green]")
            return requirement, wiring, audio_required
        if answer in ("4", "n", "no", "否", "取消", "cancel"):
            console.print("[yellow]已取消。本次任务未进入 Agent 循环，wiring.md 未修改。[/yellow]")
            return None
        if not answer or answer == "2":
            if not answer:
                console.print("[yellow]未收到确认。请输入 1-4，或直接输入具体修改意见。[/yellow]")
                feedback = "用户未确认，请保持上一版内容并再次完整输出。"
            else:
                console.print("[dim]请填写修改意见：[/dim]")
                try:
                    feedback = read_input("修改意见: ").strip()
                except (EOFError, KeyboardInterrupt):
                    feedback = "用户要求重新优化。"
        else:
            feedback = answer


def write_round_snapshot(call_no: int, elapsed: int, system: str, tools: list,
                         messages: list, response_content,
                         status: str, purpose: str) -> Path:
    """每次模型循环结束立即写完整、不可变的 Markdown 快照。"""
    if _cfg.CURRENT_TASK_DIR is None:
        raise RuntimeError("尚未建立任务目录，无法保存逐轮快照")
    rounds_dir = _cfg.CURRENT_TASK_DIR / "rounds"
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
        # ---- 真正流式调用：token 边到边实时渲染 + 工具块重建 ----
        from rich.live import Live
        from rich.markdown import Markdown
        tty = sys.stdout.isatty()
        text_parts = []
        thinking_blocks = {}   # index -> {"text": str, "sig": str}
        tool_start = {}      # index -> (id, name)
        tool_inputs = {}     # index -> partial json 累积
        stop_reason = None
        stream_ok = True
        live = Live(Markdown(""), refresh_per_second=20,
                    vertical_overflow="visible") if tty else None
        if live:
            live.start()
        try:
            for ev in llm_create_stream(model=current_model_alias(), system=system,
                                        messages=messages, tools=tools, max_tokens=8000):
                kind = ev[0]
                if kind == "text":
                    text_parts.append(ev[1])
                    if live:
                        live.update(Markdown("".join(text_parts)))
                elif kind == "thinking_start":
                    thinking_blocks.setdefault(ev[1], {"text": "", "sig": ev[2]})
                elif kind == "thinking_delta":
                    thinking_blocks.setdefault(ev[1], {"text": "", "sig": ""})["text"] += ev[2]
                elif kind == "thinking_signature":
                    thinking_blocks.setdefault(ev[1], {"text": "", "sig": ""})["sig"] = ev[2]
                elif kind == "tool_use_start":
                    tool_start[ev[1]] = (ev[2], ev[3])
                    tool_inputs[ev[1]] = ""
                elif kind == "tool_use_input":
                    tool_inputs[ev[1]] = tool_inputs.get(ev[1], "") + ev[2]
                elif kind == "stop":
                    stop_reason = ev[1]
        except Exception:
            stream_ok = False
        finally:
            if live:
                live.stop()

        if not stream_ok:
            # 流式失败：回退到非流式完整调用
            response = llm_create(model=current_model_alias(), system=system,
                                  messages=messages, tools=tools, max_tokens=8000)
        else:
            # 由流式事件重建统一响应（thinking 块必须保留，多轮回传含 signature）
            content = []
            for idx in sorted(thinking_blocks):
                info = thinking_blocks[idx]
                if info["text"].strip() or info["sig"]:
                    content.append(_ThinkingBlock(info["text"], info["sig"]))
            text = "".join(text_parts)
            if text.strip():
                content.append(_TextBlock(text))
            for idx in sorted(tool_start):
                t_id, t_name = tool_start[idx]
                raw = tool_inputs.get(idx, "").strip()
                try:
                    t_input = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    t_input = {}
                content.append(_ToolUseBlock(t_id, t_name, t_input))
            if not content:
                content.append(_TextBlock(text))
            response = _UnifiedResponse(content, stop_reason or "end_turn")

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
            # 流式成功且 TTY 时文本已实时显示；否则回退渲染（打字机/纯文本）
            if not stream_ok or not tty:
                for block in response.content:
                    if getattr(block, "type", None) == "text" and block.text.strip():
                        _render_assistant_text(block.text)
            messages.append({"role": "assistant", "content": _jsonable_content(response.content)})
            run_log["final_text"] = _text_of(response)
            run_log["elapsed"] = elapsed
            write_round_snapshot(
                call_no, run_log["elapsed"], system, tools, messages,
                response.content, status, purpose,
            )
            return

        text = _text_of(response).strip()

        # 工具轮次思考灰显示（仅非流式回退；流式时文本已实时显示）
        if not stream_ok and text and sys.stdout.isatty():
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
            messages.append({"role": "assistant", "content": _jsonable_content(response.content)})
            messages.append({"role": "user", "content": results})
            # 达上限进度报告：流式渲染（独立 Live）
            report_parts = []
            r_live = (Live(Markdown(""), refresh_per_second=20,
                           vertical_overflow="visible") if tty else None)
            if r_live:
                r_live.start()
            try:
                for rev in llm_create_stream(model=current_model_alias(), system=system,
                                             messages=messages, max_tokens=4000):
                    if rev[0] == "text":
                        report_parts.append(rev[1])
                        if r_live:
                            r_live.update(Markdown("".join(report_parts)))
            except Exception:
                pass
            finally:
                if r_live:
                    r_live.stop()
            report_text = "".join(report_parts)
            if not report_text.strip():
                # 流式失败/空：回退非流式
                report = llm_create(model=current_model_alias(), system=system,
                                    messages=messages, max_tokens=4000)
                report_text = _text_of(report)
                if not tty or not report_text.strip():
                    _render_assistant_text(report_text)
            elif not tty:
                print(report_text)
            report = _UnifiedResponse([_TextBlock(report_text)], "end_turn")
            messages.append({"role": "assistant", "content": _jsonable_content(report.content)})
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
        used = []                             # [(标签, 类别, outcome, 概要)]
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
        # 显示进度条
        render_progress_bar()
        for tc, label, kind in planned:
            output = execute_tool(tc.name, tc.input)     # 输出内容不上屏
            outcome = _tool_outcome(output)
            ok = outcome == "success"
            TODO.observe_tool(tc.name, tc.input, output, ok)
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
        status = ("成功" if all_ok else
                  "跳过" if all(o == "skipped" for _, _, o, _ in used)
                  else "失败")
        # 使用卡片展示本轮结果
        show_round_card(round_no, elapsed, tool_names, purpose, status, used)
        todo_after = TODO.render()
        todo_update = ""
        if todo_after != todo_before:
            todo_update = f"更新前：\n{todo_before}\n\n更新后：\n{todo_after}"
        previous_result = f"{'成功' if all_ok else '失败'}——第 {round_no} 轮"
        next_round = min(round_no + 1, MAX_ITERATIONS)
        results.append({
            "type": "text",
            "text": build_user_prompt(
                prompt_input, next_round, elapsed, previous_result,
                previous_tools=tool_names, todo_update=todo_update,
                must_stop=round_no >= MAX_ITERATIONS,
            ),
        })
        messages.append({"role": "assistant", "content": _jsonable_content(response.content)})
        messages.append({"role": "user", "content": results})
        write_round_snapshot(
            call_no, elapsed, system, tools, messages,
            response.content, status, purpose,
        )


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

    print(f"\n📝 提取到候选经验：\n  名称：{name}\n  适用：{desc}\n"
          f"  ---- 正文 ----\n{body}\n  --------------")
    try:
        ans = read_input("确认保存该经验为技能？[y=保存 / n=不保存]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
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


def archive_run(user_input: str, task_dir: Path, messages: list,
                system_prompt: str) -> Path:
    """保存最终产物、完整消息链及 rounds/ 中的全部逐轮快照。"""
    transcript = render_userprompt_md(user_input, system_prompt, messages)
    (task_dir / "userprompt.md").write_text(transcript)
    return task_dir


def execute_project_task(requirement: str, audio_required: bool,
                         project_dir: Path, content: str = None) -> dict:
    """以已确认的规范化需求跑完整 Agent 闭环并归档，返回 run_log。"""
    task_start = time.monotonic()
    content = content or requirement
    TODO.start(requirement, audio_required)
    _cfg.CURRENT_TASK_DIR = project_dir
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "rounds").mkdir(exist_ok=True)
    if not (project_dir / "wiring.md").exists():
        if WIRING_FILE.exists():
            shutil.copy2(WIRING_FILE, project_dir / "wiring.md")
        else:
            (project_dir / "wiring.md").write_text("（无接线）\n")
    write_requirement(project_dir, requirement)
    messages = [{
        "role": "user",
        "content": build_user_prompt(
            content, round_no=1, elapsed=0,
            previous_result="任务开始", previous_tools="无",
        ),
    }]
    console.print(f"[dim][项目实施][/dim] {_display_path(project_dir)}")
    run_log = {"rounds": [], "prompt_input": content}
    try:
        agent_loop(messages, run_log, task_start)
    except Exception as e:
        console.print(f"[red]Error: {rich_escape(str(e))}[/red]")
        run_log.setdefault("final_text", f"(异常中止: {e})")
    console.print("\n[dim]⏳ 正在保存最终代码与完整 user prompt，并提取经验…[/dim]")
    pending = None
    try:
        flow_md = render_flow_md(requirement, run_log)
        run_dir = archive_run(
            requirement, project_dir, messages,
            run_log.get("system_prompt", ""),
        )
        note, pending = extract_skill(flow_md, run_dir)
        console.print(f"[归档] {_display_path(run_dir)} | {note}")
        console.print("✅ [green]最终代码与完整 user prompt 已保存，可以继续提问或退出。[/green]")
        render_final_report(run_dir, run_log)
    except Exception as e:
        console.print(f"[red][归档失败][/red] {e}")
    run_log["pending_skill_context"] = pending
    return run_log


def _display_path(p: Path) -> str:
    """尽量显示相对 SCRIPT_DIR 的短路径；不在其下则显示绝对路径。"""
    from yuanshen.config import SCRIPT_DIR
    try:
        return str(p.relative_to(SCRIPT_DIR))
    except ValueError:
        return str(p)


def run_a2a_mode():
    """A2A 服务端模式：不启动 REPL，终端仅用于远程任务的人工确认。"""
    def _arg(flag: str):
        if flag in sys.argv and sys.argv.index(flag) + 1 < len(sys.argv):
            return sys.argv[sys.argv.index(flag) + 1]
        return None

    host = _arg("--a2a-host") or os.getenv("A2A_HOST", "127.0.0.1")
    port = int(_arg("--a2a-port") or os.getenv("A2A_PORT", "9999"))
    try:
        import a2a_server
    except ImportError as e:
        console.print(f"[red]缺少 A2A 依赖: {e}[/red]")
        console.print("请安装: [yellow]pip install 'a2a-sdk>=0.3,<1.0' "
                      "'uvicorn>=0.30'[/yellow]（或重新 pip install -r requirements.txt）")
        sys.exit(1)
    a2a_server.bootstrap(host, port)
