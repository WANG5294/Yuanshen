"""本地工具定义与执行、MCP 工具路由。"""
import hashlib
import re
import shlex
import subprocess
from pathlib import Path

from yuanshen.config import (
    CURRENT_TASK_DIR,
    ESP32_PORT,
    PROJECTS_DIR,
    WORKDIR,
)
from yuanshen.mcp_client import MCP_CLIENTS
from yuanshen.skills import SKILLS
from yuanshen.todos import AUDIO_TOOL_NAMES, TODO


ALLOWED_COMMANDS = {
    "python", "python3", "ruff", "mypy", "mpy-cross",
    "ls", "rg", "head", "tail", "wc", "file", "git",
}
SHELL_META = re.compile(r"[;&|`<>$(){})\n\r]")


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
    return (f'<skill-loaded name="{skill_name}">\n{content}\n</skill-loaded>\n\n'
            f"请遵循以上技能说明完成任务。")


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
    from yuanshen.mcp_client import MCP_TOOL_DEFS
    return base_tools() + MCP_TOOL_DEFS


def execute_tool(name: str, args: dict) -> str:
    if name in AUDIO_TOOL_NAMES:
        from yuanshen.config import AUDIO_VALIDATION_MODE
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
