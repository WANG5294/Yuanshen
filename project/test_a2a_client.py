#!/usr/bin/env python3
"""Yuanshen A2A 服务端冒烟测试(不进 npm 包,开发验证用)。

场景(服务端需以管道喂入确认答案运行,见 README A2A 小节):
  1. GET /.well-known/agent.json  校验 Agent Card
  2. message/send 任务 A(终端答 y)   → 期望 completed,含 final_report artifact
  3. A 执行期间并发任务 B           → 期望 failed(busy,串口独占)
  4. A 结束后任务 C(终端无输入=EOF) → 期望 rejected(未确认)

用法: python project/test_a2a_client.py [base_url]
"""

import json
import sys
import threading
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9999").rstrip("/")
TIMEOUT = httpx.Timeout(900.0, connect=10.0)


def rpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": method}
    if params is not None:
        payload["params"] = params
    r = httpx.post(BASE + "/", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


def send(text):
    return rpc("message/send", {
        "message": {
            "role": "user",
            "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": text}],
        }
    })


def state_of(task):
    return task.get("status", {}).get("state", "?")


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    results = []

    # 1. Agent Card
    card = httpx.get(BASE + "/.well-known/agent.json", timeout=10).json()
    results.append(check("Agent Card 名称", card.get("name") == "Yuanshen ESP32 Agent"))
    results.append(check("Agent Card 含 ESP32 技能",
                         any("esp32" in s.get("id", "") for s in card.get("skills", []))))
    caps = card.get("capabilities", {})
    results.append(check("声明非流式", caps.get("streaming") is False, json.dumps(caps)))

    # 2+3. 任务 A(确认执行) + 执行期间并发任务 B(busy)
    holder = {}

    def run_a():
        holder["a"] = send("读取 ESP32 板子当前 REPL 是否可用,直接报告即可,"
                           "不需要写程序。")

    t = threading.Thread(target=run_a)
    t.start()
    time.sleep(3)                       # 等 A 拿到任务锁(规范化 LLM 调用期间锁已持有)
    b = send("并发探测任务")
    results.append(check("并发任务 busy 失败", state_of(b) == "failed",
                         state_of(b)))
    t.join()
    a = holder["a"]
    results.append(check("任务 A 完成", state_of(a) == "completed", state_of(a)))
    artifacts = a.get("artifacts") or []
    has_report = any(ar.get("name") == "final_report" for ar in artifacts)
    results.append(check("任务 A 含 final_report artifact", has_report))
    if has_report:
        text = artifacts[0]["parts"][0].get("text", "")
        print("  --- final_report 摘要 ---")
        print("  " + text[:300].replace("\n", "\n  "))

    # 3b. tasks/get 回查 A
    got = rpc("tasks/get", {"id": a["id"]})
    results.append(check("tasks/get 回查", got.get("id") == a["id"]))

    # 4. 任务 C:终端 stdin 已 EOF,确认视为 n → rejected
    c = send("这个任务不应被执行")
    results.append(check("未确认任务被拒绝", state_of(c) == "rejected", state_of(c)))

    print()
    if all(results):
        print(f"全部 {len(results)} 项检查通过 ✅")
        return 0
    print(f"{results.count(False)}/{len(results)} 项检查失败 ❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
