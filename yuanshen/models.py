"""大语言模型客户端与 API Key 管理。"""
import json
import os

from dotenv import load_dotenv

import yuanshen.config as _cfg


class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text

    def model_dump(self):
        return {"type": self.type, "text": self.text}


class _ThinkingBlock:
    """推理模型的 thinking 块。多轮对话必须原样回传给 API（含 signature）。"""
    type = "thinking"

    def __init__(self, thinking: str, signature: str = ""):
        self.thinking = thinking
        self.signature = signature

    def model_dump(self):
        d = {"type": self.type, "thinking": self.thinking}
        if self.signature:
            d["signature"] = self.signature
        return d


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
    alias = _cfg._current_model_alias
    if alias not in _cfg.MODEL_REGISTRY:
        print(f"⚠ 未知模型 '{alias}'，回退到 {_cfg.DEFAULT_MODEL_ALIAS}")
        alias = _cfg.DEFAULT_MODEL_ALIAS
        _cfg._current_model_alias = alias
    return _cfg.MODEL_REGISTRY[alias]


def current_model_alias() -> str:
    return _cfg._current_model_alias


def has_key() -> bool:
    cfg = current_model_config()
    val = os.getenv(cfg["api_key_env"])
    return bool(val and "sk-xxx" not in val and len(val) >= 20)


def key_guidance() -> str:
    cfg = current_model_config()
    env_file = _cfg.YUANSHEN_DIR / ".env"
    return (f"缺少 {cfg['api_key_env']}。保存方法：\n"
            f"  1. 编辑 {env_file}（首次运行已自动从 .env.example 创建）\n"
            f"  2. 加一行：{cfg['api_key_env']}=你的Key\n"
            f"     获取途径：{cfg['key_hint']}\n"
            f"  3. 保存后重新运行程序，或在程序内使用 /api-key 命令")


def load_api_key() -> bool:
    """根据当前模型加载对应 API Key。

    优先级：~/.yuanshen/.env > 项目目录 .env（兼容旧版）。
    当用户数据目录已存在 .env 时，不再被项目目录 .env 覆盖，避免 /api-key
    保存后又被旧 Key 覆盖。"""
    yuanshen_env = _cfg.YUANSHEN_DIR / ".env"
    if yuanshen_env.exists():
        load_dotenv(yuanshen_env, override=True)
    else:
        # 仅在新用户首次运行时 fallback 到项目目录 .env
        load_dotenv(_cfg.SCRIPT_DIR / ".env", override=True)
    cfg = current_model_config()
    if not has_key():
        _cfg.API_KEY = None
        return False
    _cfg.API_KEY = os.getenv(cfg["api_key_env"])
    _cfg._clients = {}  # 切换模型/Key 后重建客户端
    return True


def _save_api_key(env_name: str, key_value: str) -> None:
    """保存键值到 ~/.yuanshen/.env 文件（覆盖或追加；API Key、串口等通用）。"""
    env_path = _cfg.YUANSHEN_DIR / ".env"
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
    _cfg.console.print(f"[dim]已保存到 {env_path}[/dim]")


def _get_anthropic_client(cfg: dict):
    from anthropic import Anthropic
    return Anthropic(api_key=_cfg.API_KEY, base_url=cfg["base_url"])


def _get_openai_client(cfg: dict):
    from openai import OpenAI
    return OpenAI(api_key=_cfg.API_KEY, base_url=cfg["base_url"])


def get_client():
    cfg = current_model_config()
    provider = cfg["provider"]
    if provider not in _cfg._clients:
        factory = _get_anthropic_client if provider == "anthropic" else _get_openai_client
        _cfg._clients[provider] = factory(cfg)
    return _cfg._clients[provider]


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
            extra["thinking"] = _cfg.THINKING
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
    alias = alias.strip()
    if alias not in _cfg.MODEL_REGISTRY:
        return False
    _cfg._current_model_alias = alias
    _cfg.API_KEY = None
    _cfg._clients = {}
    return True


def llm_create_stream(model=None, system=None, messages=None, tools=None,
                      max_tokens=None, **kwargs):
    """流式 LLM 调用生成器（真正的 token 级流式）。

    产出事件元组：
      ("text", str)                     文本增量
      ("thinking_start", idx, sig)      thinking 块开始（签名可能后到）
      ("thinking_delta", idx, text)     thinking 文本增量
      ("thinking_signature", idx, sig)  thinking 签名
      ("tool_use_start", idx, id, name) 工具调用开始
      ("tool_use_input", idx, partial)  工具参数 JSON 增量
      ("stop", stop_reason)             结束（reason: tool_use / end_turn / ...）
    """
    cfg = current_model_config()
    model_name = model or cfg["api_name"]
    client = get_client()

    if cfg["provider"] == "anthropic":
        extra = {}
        if cfg.get("supports_thinking"):
            extra["thinking"] = _cfg.THINKING
        with client.messages.create(
            model=model_name,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            stream=True,
            **extra,
            **kwargs,
        ) as stream:
            for chunk in stream:
                t = chunk.type
                if t == "content_block_start":
                    cb = chunk.content_block
                    if cb.type == "tool_use":
                        yield ("tool_use_start", chunk.index, cb.id, cb.name)
                    elif cb.type == "thinking":
                        yield ("thinking_start", chunk.index,
                               getattr(cb, "signature", "") or "")
                elif t == "content_block_delta":
                    d = chunk.delta
                    if d.type == "text_delta":
                        yield ("text", d.text)
                    elif d.type == "input_json_delta":
                        yield ("tool_use_input", chunk.index, d.partial_json)
                    elif d.type == "thinking_delta":
                        yield ("thinking_delta", chunk.index, d.thinking)
                        sig = getattr(d, "signature", None)
                        if sig:
                            yield ("thinking_signature", chunk.index, sig)
                    else:
                        # 其他 delta 类型（可能携带 thinking signature）
                        sig = getattr(d, "signature", None)
                        if sig:
                            yield ("thinking_signature", chunk.index, sig)
                elif t == "message_delta":
                    sr = getattr(chunk.delta, "stop_reason", None)
                    if sr:
                        yield ("stop", sr)

    else:
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
        stream = client.chat.completions.create(
            model=model_name,
            messages=openai_messages,
            tools=openai_tools,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )
        stop_reason = None
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta and delta.content:
                yield ("text", delta.content)
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if tc.id:
                        yield ("tool_use_start", idx, tc.id,
                               getattr(tc.function, "name", "") or "")
                    if tc.function and tc.function.arguments:
                        yield ("tool_use_input", idx, tc.function.arguments)
            if choice.finish_reason:
                stop_reason = choice.finish_reason
        if stop_reason:
            yield ("stop", "tool_use" if stop_reason == "tool_calls"
                   else stop_reason)
