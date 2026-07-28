# Yuanshen UI 改造方案 —— 从终端日志流到现代 AI CLI

> 参考目标：Cursor / Claude Code / Aider 的交互体验

---

## 现状诊断

当前 `yuanshen.py` 所有用户可见输出都依赖裸 `print`：

| 位置 | 现状 |
|------|------|
| `main()` 启动信息 | 普通 `print` |
| 每轮状态行 | 手动拼接字符串 + `\r\033[2K` 覆盖 |
| 工具列表 `/tool` | 纯文本列表 |
| 最终回复 | `print(block.text)` |
| 输入 | `read_input("You: ")` |

**缺少**：颜色/面板、Markdown 渲染、代码高亮、执行中 spinner、命令补全、流式输出。

---

## 改造路线图

```
阶段一 ─── Rich + prompt_toolkit ─── ROI 最高，不改架构
    │
    ▼
阶段二 ─── 流式输出 ─── 模型回复像打字一样出现
    │
    ▼
阶段三 ─── 全 TUI (Textual) ─── 左右分栏、异步 App、大改
```

---

## 阶段一：Rich + prompt_toolkit 快速改造

**目标**：只加 `rich` 依赖，保留现有同步流程，大幅提升观感。

**新增依赖**：

```
rich>=13.0
```

### 详细改动清单

#### 1. 启动画面

**现状**：

```python
print(f"{banner}\n{'='*60}\n工作目录: {WORK_DIR}\n技能数: {len(skills)}")
```

**改后**（`rich.console.Console` + `rich.panel.Panel`）：

```python
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

banner_text = Text("✨ Yuanshen v1.0", style="bold cyan")
banner_text.append("\nESP32 MicroPython Agent", style="green)
console.print(Panel(banner_text, subtitle=f"工作目录: {WORK_DIR}"))
```

#### 2. 每轮状态行（核心体验提升）

**现状**：

```python
print(f"第 {round_no} 轮｜第 {elapsed} 秒｜工具：{tool_names}｜目的和作用：{purpose} …… {status}")
```

**改后**：

```python
from rich.text import Text

def show_round_result(round_no, elapsed, tool_names, purpose, status):
    text = Text()
    text.append(f"第 {round_no} 轮 ", style="bold cyan")
    text.append(f"({elapsed}s) ", style="dim")
    text.append(f"[{tool_names}] ", style="yellow")
    text.append(f"{purpose} ", style="white")
    color = {"成功": "green", "失败": "red", "跳过": "yellow", "执行中": "blue"}[status]
    text.append(f"● {status}", style=f"bold {color}")
    console.print(text)
```

**效果**：

```
第 3 轮 (18s) [upload（MCP）] 把程序上传为板上 main.py ● 成功
```

#### 3. 执行中 spinner

用 `rich.status.Status` 替代静态状态行：

```python
from rich.status import Status

with Status(f"第 {round_no} 轮: {purpose}...", spinner="dots") as status:
    result = await execute_round()
    status.update(f"完成: {result}")
```

#### 4. 成功/失败/跳过 标签

```python
from rich.markup import escape

def tag(status):
    style = {"成功": "green", "失败": "red", "跳过": "yellow"}
    return f"[{style[status]}]{escape(status)}[/{style[status]}]"
```

#### 5. 最终回复 —— Markdown 渲染 + 代码高亮

**现状**：

```python
print(block.text)
```

**改后**：

```python
from rich.markdown import Markdown

md = Markdown(block.text)
console.print(md)
```

#### 6. 结构化输出 —— `/work`、`/tool`、`/skill` 用表格

```python
from rich.table import Table

table = Table(title="可用技能")
table.add_column("名称", style="cyan")
table.add_column("描述")
table.add_column("路径", style="dim")
for s in skills:
    table.add_row(s.name, s.description, s.path)
console.print(table)
```

#### 7. 需求确认页

```python
from rich.panel import Panel
from rich.columns import Columns

left = Panel("**规范化需求**\n" + requirements, title="需求")
right = Panel("**接线说明**\n" + wiring, title="接线")
console.print(Columns([left, right]))
```

#### 8. 输入框 —— prompt_toolkit 增强

`prompt_toolkit` 已在 `requirements.txt`，添加 slash 命令补全和历史：

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter

commands = WordCompleter(["/work", "/tool", "/skill", "/help", "/exit", "/rounds"])
session = PromptSession(history=FileHistory(".history"))

def read_input(prompt="You: "):
    return session.prompt(prompt, completer=commands)
```

#### 9. 非 TTY 环境兼容

```python
def console():
    if sys.stdout.isatty():
        from rich.console import Console
        return Console()
    # 管道/日志：输出纯文本
    import sys
    return sys.stdout
```

---

## 阶段二：流式输出

**目标**：模型回复像打字一样逐字出现，而非静等全返回再打印。

### 改动点

#### 流式 API

```python
def llm_create_stream(messages):
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=messages,
        stream=True,
    )
    for chunk in response:
        if chunk.type == "content_block_delta":
            yield chunk.delta.text
```

#### 实时渲染

```python
from rich.live import Live
from rich.markdown import Markdown

text = ""
with Live(Markdown(text), refresh_per_second=10) as live:
    for chunk in llm_create_stream(messages):
        text += chunk
        live.update(Markdown(text))
```

#### 工具调用块

流式检测 `tool_use` delta，收集完整参数后再执行 MCP 调用，执行期间暂停流式更新。

---

## 阶段三：全 TUI（Textual）

**适用时机**：阶段一、二做完后，用户量增长、需要复杂交互时再做。

### 预期布局

```
┌──────────────────────────────────────────────────┐
│  状态栏：连接状态 │ 轮次 │ 耗时 │ 模型           │
├──────────────────────┬───────────────────────────┤
│                      │                           │
│   聊天记录区          │   工具日志区              │
│   (Markdown 历史)    │   (MCP 调用过程)          │
│                      │                           │
│                      │                           │
├──────────────────────┴───────────────────────────┤
│  输入框  /work, /tool, /help ...                 │
└──────────────────────────────────────────────────┘
```

### 技术方案

- 用 `textual` 重写主循环，从同步脚本 → 异步 App
- Screen 管理：聊天 Screen + 调试 Screen
- Widget：`RichLog`（聊天）+ `Tree`（文件）+ `DataTable`（工具结果）
- 热重载：修改 skills/ 后自动刷新

### 注意事项

- 改动面大，会重构主循环 `agent_loop()` 和 `llm_create()`
- 需要处理异步 MCP 调用与 Textual 事件循环的兼容
- 保留 CLI fallback（`--no-tui` 回退到阶段一）

---

## 最小可落地改动（阶段一核心代码片段）

将现有状态行：

```python
print(f"第 {round_no} 轮｜第 {elapsed} 秒｜工具：{tool_names}｜目的和作用：{purpose} …… {status}")
```

替换为：

```python
from rich.console import Console
from rich.text import Text

console = Console()

text = Text()
text.append(f"第 {round_no} 轮 ", style="bold cyan")
text.append(f"({elapsed}s) ", style="dim")
text.append(f"[{tool_names}] ", style="yellow")
text.append(f"{purpose} ", style="white")
color = {"成功": "green", "失败": "red", "跳过": "yellow", "执行中": "blue"}[status]
text.append(f"● {status}", style=f"bold {color}")
console.print(text)
```

---

## 依赖变更

| 阶段 | 新增依赖 |
|------|----------|
| 一 | `rich>=13.0` |
| 二 | 无（用现有 Anthropic SDK 的 stream 模式） |
| 三 | `textual>=1.0` |
