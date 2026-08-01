#!/usr/bin/env python3
"""Yuanshen A2A(Agent2Agent)服务端 —— 把 Yuanshen 暴露为标准 A2A Agent。

协议:Google Agent2Agent(JSON-RPC 2.0 over HTTP),基于官方 a2a-sdk(0.3.x)。

- Agent Card 发布于  http://<host>:<port>/.well-known/agent.json
- message/send 接收远程任务 → 本地终端人工确认 → 走完整 ESP32 闭环
  (规范化 → 烧录 → 实机验证 → 归档),最终报告作为 artifact 返回
- 串口独占 + 全局状态(TODO/CURRENT_TASK_DIR/wiring)非并发安全,
  因此同一时刻只执行一个任务,占用期间新任务立即 failed(busy)
- v1 边界:非流式、无 push notification、不支持取消执行中任务、无认证

启动:
  python yuanshen.py --a2a [--a2a-host 127.0.0.1] [--a2a-port 9999]
  或  python a2a_server.py        (等价,读 A2A_HOST/A2A_PORT 环境变量)
"""

import asyncio
import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

import yuanshen
from yuanshen import console

try:
    import uvicorn
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import (
        AgentCapabilities,
        AgentCard,
        AgentSkill,
        Part,
        TextPart,
        UnsupportedOperationError,
    )
    from a2a.utils import new_agent_text_message, new_task
    from a2a.utils.errors import ServerError
except ImportError as e:                     # pragma: no cover - 依赖缺失时给出指引
    sys.exit(f"缺少 A2A 依赖: {e}\n"
             "请安装: pip install 'a2a-sdk>=0.3,<1.0' 'uvicorn>=0.30'"
             "（或重新 pip install -r requirements.txt）")


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999

# 单任务锁:串口独占 + 全局状态非并发安全,同一时刻只跑一个远程任务
_task_lock = threading.Lock()


class _AgentBusy(RuntimeError):
    """已有任务在执行,新任务立即失败。"""


class _TaskRejected(RuntimeError):
    """本地终端人工确认时拒绝了该远程任务。"""


def build_agent_card(host: str, port: int) -> AgentCard:
    """生成 Yuanshen 的 Agent Card(能力名片,供其他 Agent 发现)。"""
    url = os.getenv("A2A_BASE_URL", "").strip() or f"http://{host}:{port}/"
    return AgentCard(
        name="Yuanshen ESP32 Agent",
        description=(
            "ESP32 MicroPython 闭环开发 Agent:接收自然语言需求,自动完成"
            "代码生成、固件烧录和实机验证,返回测试报告。任务执行前会在"
            "本机终端进行人工确认;同一时刻只执行一个任务(串口独占)。"
        ),
        url=url,
        version=yuanshen.APP_VERSION,
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        skills=[
            AgentSkill(
                id="esp32-micropython-development",
                name="ESP32 闭环开发",
                description=(
                    "按自然语言需求完成 ESP32 MicroPython 开发全流程:"
                    "需求与接线规范化、代码编写、烧录为板上 main.py、实机测试。"
                ),
                tags=["esp32", "micropython", "embedded", "hardware"],
                examples=[
                    "用 GPIO4 的 LED 做一个每秒闪烁的呼吸灯",
                    "按键控制蜂鸣器播放音阶,并实机验证",
                ],
            ),
            AgentSkill(
                id="hardware-closed-loop-validation",
                name="硬件实机验证",
                description=(
                    "通过串口长连接在真实开发板上执行并验证程序,"
                    "音频任务可使用麦克风闭环验收。"
                ),
                tags=["validation", "serial", "audio", "closed-loop"],
                examples=["读取板上传感器值并汇报", "验证 LED 闪烁频率是否正确"],
            ),
        ],
    )


def _new_a2a_project_dir(user_text: str) -> Path:
    """为远程任务创建独立项目目录(~/.yuanshen/projects/<时间戳>_a2a-<slug>)。"""
    slug = yuanshen.slugify(user_text)[:20] or "task"
    project_dir = yuanshen.PROJECTS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_a2a-{slug}"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "rounds").mkdir(exist_ok=True)
    # 每个远程任务从全局模板复制一份接线,确认后只改项目内副本
    if yuanshen.WIRING_FILE.exists():
        shutil.copy2(yuanshen.WIRING_FILE, project_dir / "wiring.md")
    else:
        (project_dir / "wiring.md").write_text("（无接线）\n")
    return project_dir


def _run_task_sync(user_text: str) -> str:
    """同步执行远程任务(在 worker 线程中运行),返回最终报告文本。"""
    if not _task_lock.acquire(blocking=False):
        raise _AgentBusy("Yuanshen 正在执行其他任务(串口独占),请稍后重试")
    try:
        console.print("\n" + "━" * 50, style="orange1")
        console.print("[bold magenta]🌐 收到 A2A 远程任务[/bold magenta]")
        console.print(f"[magenta]{user_text}[/magenta]")
        console.print("[dim]需在下方人工确认规范化需求后才会执行硬件操作。[/dim]\n")

        # 先建项目目录并设为当前任务目录:规范化阶段读项目接线副本,
        # 确认后写回的也是项目副本,不污染全局 wiring.md
        project_dir = _new_a2a_project_dir(user_text)
        yuanshen.CURRENT_TASK_DIR = project_dir

        confirmed = yuanshen.confirm_normalized_task(user_text)
        if confirmed is None:
            raise _TaskRejected("本地终端未确认该远程任务(已拒绝或规范化失败)")
        requirement, _wiring, audio_required = confirmed

        run_log = yuanshen.execute_project_task(requirement, audio_required,
                                                project_dir)
        report = (run_log.get("final_text") or "(任务结束,无最终文本)").strip()
        return f"{report}\n\n---\n[项目归档] {project_dir}"
    finally:
        _task_lock.release()
        console.print("[dim]🌐 A2A 远程任务结束,等待下一个任务…[/dim]\n")


class YuanshenAgentExecutor(AgentExecutor):
    """A2A 协议适配器:把 A2A message/send 桥接到 Yuanshen 的同步任务管线。"""

    async def execute(self, context: RequestContext,
                      event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise ServerError(error=UnsupportedOperationError())
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        user_text = context.get_user_input().strip()
        if not user_text:
            await updater.failed(new_agent_text_message(
                "任务内容为空:请在 message 的 text part 中描述 ESP32 开发需求。",
                task.context_id, task.id))
            return

        await updater.start_work(new_agent_text_message(
            "任务已受理,等待本机终端人工确认…", task.context_id, task.id))
        try:
            report = await asyncio.to_thread(_run_task_sync, user_text)
        except _TaskRejected as e:
            await updater.reject(new_agent_text_message(
                str(e), task.context_id, task.id))
            return
        except Exception as e:
            await updater.failed(new_agent_text_message(
                f"任务执行失败: {e}", task.context_id, task.id))
            return

        await updater.add_artifact(
            [Part(root=TextPart(text=report))], name="final_report")
        await updater.complete(new_agent_text_message(
            "ESP32 开发任务已完成,最终报告见 final_report artifact。",
            task.context_id, task.id))

    async def cancel(self, context: RequestContext,
                     event_queue: EventQueue) -> None:
        # v1 不支持取消执行中任务(Agent 循环无中断点)
        raise ServerError(error=UnsupportedOperationError())


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """构建并启动 A2A HTTP 服务(阻塞,Ctrl+C 退出)。"""
    agent_card = build_agent_card(host, port)
    handler = DefaultRequestHandler(
        agent_executor=YuanshenAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )
    app = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)

    base = f"http://{host}:{port}"
    console.print(
        f"[bold cyan]🌐 Yuanshen A2A 服务端已启动[/bold cyan]\n"
        f"  Agent Card: {base}/.well-known/agent.json\n"
        f"  JSON-RPC:   {base}/  (message/send, tasks/get)\n"
        f"  模式: 非流式 | 单任务串行 | [bold]远程任务需本机终端人工确认[/bold]\n"
        f"  [dim]注意: 无认证机制,请勿绑定到公网地址;Ctrl+C 停止服务[/dim]"
    )
    uvicorn.run(app.build(), host=host, port=port, log_level="warning")


def bootstrap(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """初始化(在 yuanshen 模块状态上)并启动 A2A 服务。

    A2A 任务全部经由本模块 import 的 yuanshen 模块状态运行,
    因此 load_api_key/init_mcp 必须在这里完成,而不是在调用方的
    __main__ 里(否则远程任务会用到未初始化的模块副本)。"""
    if not yuanshen.load_api_key():
        console.print(yuanshen.key_guidance())
        sys.exit(1)
    yuanshen.init_mcp()
    serve(host, port)


def main() -> None:
    """直接运行入口:python a2a_server.py(等价于 yuanshen.py --a2a)。"""
    host = os.getenv("A2A_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = int(os.getenv("A2A_PORT", str(DEFAULT_PORT)))
    bootstrap(host, port)


if __name__ == "__main__":
    main()
