# Yuanshen v3 架构参考 —— 对照 OpenClaw / Hermes 的取舍建议（历史）

> 本文记录 v3 时期的对照分析，不描述 v4.0 当前实现。
>
> 本文档不是要把 Yuanshen 改造成 OpenClaw 或 Hermes 的缩小版。这两个项目服务
> 于完全不同的规模（多渠道消息机器人 / 跨平台常驻个人助理），大部分工程
> 投入（网关协议、多租户安全模型、33 个模型 provider、SQLite FTS5、
> Docker/Nix 打包……）对一个单机单用户的 ESP32 大作业 Agent 是过度工程化。
>
> 这里只挑出**规模无关、原则性强、成本低**的设计模式，逐条给出"要不要
> 抄、为什么、具体怎么改"。代码怎么写仍由你决定，这里只提供参照。

## 一、Yuanshen v3 现状速览（基于当时的 `yuanshen_v3.py`）

| 维度 | 现状 | 对应代码 |
|---|---|---|
| 主循环 | 单层 `while True`，顺序执行工具，靠"循环状态"文本回灌防跑偏 | `agent_loop()` :650 |
| 工具 | 6 个本地工具 + 1 个硬编码 MCP server（12 工具）= 18 个，全部平铺进一个 tools 数组 | `get_all_tools()` :500 |
| 技能 | 完整实现 Claude Skills 格式（frontmatter + 按需注入），已经做得很规范 | `SkillLoader` :280 |
| 流程护栏 | `TodoManager` 强制"烧录未部署 main.py 不能标记完成"——**程序级卡点**，不是纯 prompt 约束 | `TodoManager.update()` :355 |
| 安全 | `bash` 命令黑名单 + 工作目录围栏是代码强制；GPIO34/35、串口白名单等**只写在 system prompt 里**，靠模型自觉 | `BANNED_CMDS` :524, `safe_path()` :504 |
| 模型 | 固定 deepseek-v4-pro，不提供切换，也无重试/降级——单点故障 | `MODEL`/`BASE_URL` :92-93, `llm_create()` :~140 |
| MCP | 自己手搓的最小 stdio JSON-RPC 客户端，服务器列表硬编码一条 | `MCPClient` :183, `MCP_SERVERS` :178 |
| 上下文 | 无压缩；靠"每任务一个新对话、结束即归档"天然限制长度 | `archive_run()` :885 |

这个现状本身**已经在正确地践行"窄腰"（narrow waist）原则**——工具集小、
技能按需加载、护栏尽量做成代码而非纯文字。下面的建议是在这个基础上做
增量，不是推翻重来。

## 二、逐条对照：值得借鉴 / 不值得借鉴

### ✅ 值得借鉴（低成本，现在就能做）

**1. 安全红线从"prompt 里写着"升级为"代码里卡住"**
- 来源：OpenClaw 的 `beforeToolCall` 阻断钩子（`agent-loop.ts:978`）+
  `net-policy` 包把安全规则做成可执行代码而非指令；Yuanshen 自己的
  `TodoManager.deployed_main` 卡点也是同一思路的成功先例。
- 现状缺口：GPIO34/35 禁止配置为输出、串口只能是 `/dev/ttyACM*`/
  `/dev/ttyUSB*`，这两条目前只在 `build_system()` 的文字里，`execute_tool`
  没有真正拦截。模型（尤其是较弱的开源/国产模型）偶尔会不遵守文字红线。
- 建议：在 `execute_tool` 分派 `upload`/`repl_exec`/`run_script` 前，加一次
  轻量静态检查——扫一眼即将烧录/执行的代码里有没有
  `Pin(34, Pin.OUT)` / `Pin(35, Pin.OUT)` 这类模式，命中就直接返回
  `Error:` 而不下发到板子；串口号也可以在 `MCPClient`/MCP 服务器侧加一次
  白名单校验而不是只靠 prompt 描述。这是"把文字红线变成程序卡点"，和你
  已经给"烧录"做的事完全同构，改动量很小。

**2. API 调用加一层重试/退避**
- 来源：Hermes 的 `retry_utils.jittered_backoff`（`conversation_loop.py:63-68`）
  和错误分类 `error_classifier.classify_api_error`；OpenClaw 也有
  auth profile rotation + fallback（`README.md:139`）。
- 现状缺口：`llm_create()` 目前直接调用一次就返回（未见重试逻辑）。国产 API
  偶发 5xx / 超时很常见，一次失败就中断整个任务，对着一句话目标跑 50 轮的
  场景体验很差。模型已改为固定 deepseek-v4-pro、不再提供 `/model` 手动切换
  兜底，这条缺口的影响面比之前更大——现在没有"手动换一个模型再试"这条
  退路了，一次网络抖动就等于任务失败。
- 建议：给 `llm_create` 包一层"网络类错误重试 2-3 次、指数退避+抖动"，
  区分"可重试"（超时/5xx/429）和"不可重试"（400 参数错、认证失败）——
  不需要 Hermes 那套完整的错误分类框架，几行代码即可。

**3. MCP 服务器列表从硬编码变成可配置**
- 来源：Hermes 从 `~/.hermes/config.yaml` 的 `mcp_servers` 读取任意数量
  的外部 MCP server（`mcp_tool.py:12-40`）；OpenClaw 每个 provider/channel
  都是独立注册而非写死。
- 现状缺口：`MCP_SERVERS` 是硬编码的单元素列表（只有 `esp32-piano`）。
  你的项目里已经有麦轮小车、钢琴、Yuanshen 系列等多个"硬件对象"，未来如果要
  给Yuanshen 接一个新的 MCP server（比如给麦轮小车做的调试工具），现在得改
  主程序代码。
- 建议：把 `MCP_SERVERS` 从硬编码列表改成读一个简单的 `mcp_servers.json`
  或复用 `.env`（如 `MCP_SERVERS=esp32-piano:esp32_piano_mcp.py,car:car_mcp.py`），
  `init_mcp()` 逻辑基本不用变，只是数据来源从常量变成配置文件。这一步做完，
  "Yuanshen 换硬件对象"就和"Yuanshen 接硬件说明文档"一样零改代码。

**4. 工具调用参数/格式的容错**
- 来源：OpenClaw 的 `tool-call-repair` 包（`payload.ts`/`stream-normalizer.ts`）
  专门修复"模型把工具调用当纯文本吐出来"的情况——这是应对不同厂商模型
  工具调用规范不统一的现实问题。
- 现状缺口：Yuanshen 同时支持 3 家国产模型的 Anthropic 兼容端点，工具调用格式
  遵循程度可能参差；目前 `agent_loop` 假设 `response.content` 里的
  `tool_use` block 总是规范的。
- 建议：不需要做通用修复器，但可以在 `_text_of`/工具调用解析处加一个兜底——
  如果某轮 `stop_reason` 不是 `tool_use` 但文本里明显包含类似工具调用的
  JSON 片段（换模型时可能出现），打一条警告日志，方便你换模型测试时快速
  定位"是模型不听话还是我的解析有问题"。这是可观测性而非强修复，成本很低。

### 🟡 规模变大后再考虑（不急，先记着）

**5. 长任务的上下文裁剪**
- 来源：Hermes 的 `context_compressor.py`——保护首尾、摘要中段。
- 现状：Yuanshen 目前每任务开新对话、任务内 `MAX_ITERATIONS=50` 硬顶，天然
  限制了单次对话长度，"n 后可选保留上下文继续多轮对话修改"才会让对话
  变长。如果你发现这种多轮修改场景经常把上下文吃满、模型开始"忘事"，
  再引入"超过 N 轮后，把中间轮次摘要成一段文字、保留首尾"的机制，
  不需要 Hermes 那套自动压缩+会话分裂的完整实现。

**6. 子任务委派（子 Agent）**
- 来源：Hermes 的 `delegate_task`/`delegate_tool.py`——父 Agent 派生一个
  隔离上下文的子 Agent，只看到调用和摘要。
- 现状：Yuanshen 是单一线性任务流（编写→烧录→测试→完成），暂时没有"需要并行
  探索多个方案"的场景。如果以后任务变复杂（比如同时调试多块板子、或者
  一个任务需要先大量阅读某个新芯片手册再写代码），委派机制能防止"读手册
  的过程污染主任务上下文"，但现在加只是增加复杂度。

**7. 技能库变大后的整理机制**
- 来源：Hermes 的 `curator.py`——后台模型定期审查自建技能，pin/archive/
  consolidate，但"never auto-deletes"。
- 现状：Yuanshen 的经验提取已经有"生成候选 → 用户 y/n 确认"的把关，这个人工
  确认环节在教学/大作业场景下比自动化更合适（正确性优先于省事）。但当
  `exp-` 前缀的技能积累到几十个、可能出现重复或过时条目时，可以参考
  curator 的思路加一条 `/skill clean` 之类的手动整理命令，列出疑似重复的
  技能供你人工合并，仍然不自动删除。

### ❌ 不建议引入（明确过度工程化）

- **多渠道消息网关**（OpenClaw 20+ 渠道 / Hermes 20+ 平台插件）：Yuanshen 是
  你自己在终端里用的开发工具，没有"通过微信远程遥控 ESP32"这类需求，
  引入网关协议纯属为了炫技而增加攻击面和维护成本。
- **多 Provider 插件生态**（33 个 provider 插件/适配器）：现在固定只用
  deepseek-v4-pro 一个 Anthropic 兼容端点，连"多预设选一个"的需求都没有
  了，插件系统更无从谈起。
- **向量检索长期记忆**（`memory-host-sdk` 的 RAG/embedding）：Yuanshen 的
  "记忆"就是 skills 目录里的分块文档，规模（几个到十几个技能文件）远远
  用不上向量检索，纯文本匹配 + 模型自己判断已经够用。
- **安全沙箱/exec approval 协议**（OpenClaw 的 net-policy、审批 RPC）：
  这套东西是为"处理不可信第三方发来的消息"设计的（`SECURITY.md` 明确说
  信任模型是"单一可信操作者"）。Yuanshen 的操作者只有你自己，风险模型完全
  不同，照搬只会增加无意义的摩擦。
- **多前端复用同一核心**（CLI + Gateway + TUI + ACP + Web）：Yuanshen 目前只有
  一个 REPL 入口，除非你真的需要"手机上远程给板子发任务"，否则这是纯
  为了架构好看而做的抽象。

## 三、一条贯穿的判断标准

OpenClaw 的 `VISION.md` 有一句话很适合作为 Yuanshen 以后"要不要加这个功能"的
筛选器："We will not merge manager-of-managers agent hierarchy frameworks"
——**核心保持窄，能力挂在边缘**。Hermes 的说法是"every core tool is paid
for on every single API call"。

Yuanshen 现在的架构已经符合这个原则（skill 按需注入、工具集小、TodoManager
程序级卡点）。上面"✅ 值得借鉴"的四条都是同一类改动——**把本来只在
文字/人脑里的约束变成代码里的硬检查**，成本低、和现有风格一致；而"❌
不建议"的那几条都是"引入一整套新的子系统来解决 Yuanshen 根本不存在的问题"。
以后遇到新的架构点子，可以先问一句：这是在给已有的窄核心打补丁，还是在
造一个新的重型子系统？前者做，后者先放一放。
