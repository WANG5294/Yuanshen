"""小型共享工具函数。"""
import json
import re


def _text_of(response) -> str:
    """从统一响应中提取文本内容。"""
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


def _serialize_content(content):
    """assistant 消息里的 anthropic 对象 → 可 JSON 化的 dict。"""
    return _jsonable_content(content)


def slugify(text: str) -> str:
    """将文本转为文件系统友好的短横线名。"""
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:30].strip("-").lower() or "project"
