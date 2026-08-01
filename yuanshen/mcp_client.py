"""MCP 最小客户端（stdio JSON-RPC 2.0）—— v2 注册全部工具，含麦克风闭环。"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from yuanshen.config import APP_VERSION, PROJECT_ROOT, SCRIPT_DIR, console

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
