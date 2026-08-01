#!/usr/bin/env python3
"""a2a_client_mcp.py - Yuanshen A2A 客户端桥(MCP 服务器)

把 Yuanshen 的 A2A 服务端封装成 MCP 工具,让 Kimi Code 等 MCP 客户端
可以像调用本地工具一样,把 ESP32 开发任务委派给 Yuanshen Agent:

  你 → Kimi Code ──MCP──> 本桥 ──A2A(message/send)──> Yuanshen ──> ESP32

运行方式(与 esp32_piano_mcp.py 同一惯例):
  1. MCP stdio 模式(默认,供 Kimi Code 挂载):
       python a2a_client_mcp.py
  2. CLI 模式(单独测试,无需 mcp 包):
       python a2a_client_mcp.py yuanshen_agent_card
       python a2a_client_mcp.py yuanshen_esp32_task "用 GPIO33 红灯每秒闪烁"

前置条件:Yuanshen A2A 服务端已在运行(yuanshen --a2a),
且其终端有人值守——远程任务需人工确认后才触碰硬件。

环境变量:
  YUANSHEN_A2A_URL   A2A 服务端地址,默认 http://127.0.0.1:9999
"""

import json
import os
import sys
import uuid

import httpx

BASE = os.environ.get("YUANSHEN_A2A_URL", "http://127.0.0.1:9999").rstrip("/")
# ESP32 任务含烧录与实机测试,可能数分钟;MCP 客户端侧超时也要相应放大
# (Kimi Code 用 mcp.json 的 toolTimeoutMs,此处是桥内 HTTP 超时)
TASK_TIMEOUT = httpx.Timeout(900.0, connect=10.0)
CARD_TIMEOUT = 10.0


def _connect_hint(e: Exception) -> str:
    return (f"无法连接 Yuanshen A2A 服务端({BASE}): {e}\n"
            "请先启动服务端: yuanshen --a2a(或 python yuanshen.py --a2a)")


def _first_text(parts) -> str:
    for p in parts or []:
        if p.get("kind") == "text" and p.get("text"):
            return p["text"]
    return ""


def _status_message(task: dict) -> str:
    return _first_text((task.get("status") or {}).get("message", {}).get("parts"))


def yuanshen_agent_card() -> str:
    """获取 Yuanshen ESP32 Agent 的 A2A 能力名片(名称、技能、端点、模式)。

    用于确认服务端在线、了解它能接什么任务;下发任务前建议先调用。
    """
    try:
        r = httpx.get(f"{BASE}/.well-known/agent.json", timeout=CARD_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return _connect_hint(e)
    card = r.json()
    skills = "\n".join(f"- {s.get('id')}: {s.get('description', '')}"
                       for s in card.get("skills", []))
    return (f"名称: {card.get('name')}\n"
            f"版本: {card.get('version')} | 协议: A2A {card.get('protocolVersion')}\n"
            f"端点: {card.get('url')}\n"
            f"能力: {json.dumps(card.get('capabilities', {}), ensure_ascii=False)}\n"
            f"说明: {card.get('description')}\n"
            f"技能:\n{skills}")


def yuanshen_esp32_task(requirement: str) -> str:
    """把 ESP32 硬件开发任务委派给 Yuanshen Agent 执行,返回实机测试报告。

    任务会在 Yuanshen 本机终端等待人工确认后才执行(无人确认会一直被拒);
    同一时刻只执行一个任务,busy 时返回失败信息,应稍后重试;
    任务含代码生成、烧录和实机验证,可能运行数分钟,本调用会阻塞到结束。

    requirement: 自然语言开发需求,如 "按键控制蜂鸣器播放音阶,并实机验证"
    """
    if not requirement.strip():
        return "需求为空:请用自然语言描述要做什么,如 'GPIO33 红灯每秒闪烁'"
    payload = {
        "jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": "message/send",
        "params": {"message": {
            "role": "user", "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": requirement}],
        }},
    }
    try:
        r = httpx.post(BASE + "/", json=payload, timeout=TASK_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return _connect_hint(e)

    data = r.json()
    if "error" in data:
        return f"A2A 协议错误: {json.dumps(data['error'], ensure_ascii=False)}"
    task = data.get("result") or {}
    state = (task.get("status") or {}).get("state", "unknown")
    detail = _status_message(task)

    if state == "completed":
        for ar in task.get("artifacts") or []:
            if ar.get("name") == "final_report":
                text = _first_text(ar.get("parts"))
                if text:
                    return text
        return detail or "任务已完成,但未返回 final_report artifact。"
    if state == "rejected":
        return (f"任务被拒绝(Yuanshen 本机操作员未确认): {detail}")
    if state == "failed":
        return (f"任务执行失败: {detail}\n"
                "提示: 若提示串口独占/busy,说明有别的任务在跑,请稍后重试。")
    return f"任务异常结束(state={state}): {detail or json.dumps(task, ensure_ascii=False)[:500]}"


# =============================================================================
# 入口: MCP 服务器 或 命令行
# =============================================================================

TOOL_FUNCS = [yuanshen_agent_card, yuanshen_esp32_task]


def main():
    if len(sys.argv) > 1:                        # CLI 模式
        name = sys.argv[1]
        funcs = {f.__name__: f for f in TOOL_FUNCS}
        if name not in funcs:
            sys.exit("可用工具: " + ", ".join(funcs))
        print(funcs[name](*sys.argv[2:]))
        return

    try:                                          # MCP stdio 模式
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.exit("MCP 模式需要: pip install mcp\n"
                 "或用 CLI 模式: python a2a_client_mcp.py <工具名> [参数...]")
    server = FastMCP("yuanshen-a2a")
    for f in TOOL_FUNCS:
        server.tool()(f)
    server.run()


if __name__ == "__main__":
    main()
