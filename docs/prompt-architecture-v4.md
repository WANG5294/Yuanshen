# Yuanshen v1.0 Prompt 与缓存结构

## 1. 三部分顺序

每轮请求的逻辑结构固定为：

```text
第一部分：System Prompt
第二部分：User / Assistant / Tool 追加式历史
第三部分：当前 TodoList
```

### 第一部分：System Prompt

System Prompt 是任务内固定前缀，包含模型要求、身份、用户确认后的规范化任务要求与
规范化接线、主线规则、Skill、本地 Tool、MCP 标签、安全边界、验证标准和审计要求。

ESP32 板级硬件参考手册只提供给循环外的前置规范化模型，用于生成待用户确认的
`requirement.md` 和 `wiring.md`；手册全文不进入主体 Agent 的 System Prompt，确认后的
两份规范化结果则作为固定快照进入。

任务开始后：

- 不重新构建；
- 不插入动态计时；
- 不插入当前 Todo；
- 不改变工具顺序；
- 不改变 Skill 描述顺序；
- 不重新读取 `wiring.md`。

### 第二部分：追加式历史

这一部分按协议保留 `user`、`assistant`、`tool_use` 和 `tool_result`。每轮只在末尾添加新消息，不修改旧消息。

```text
第 1 轮：System + History₁ + Todo₁
第 2 轮：System + History₁ + Response₁ + ToolResult₁ + Todo₂
第 3 轮：System + History₁ + Response₁ + ToolResult₁
                    + Response₂ + ToolResult₂ + Todo₃
```

上一轮已经发送的完整前缀在下一轮保持不变，因此具备缓存复用条件。是否实际命中仍由模型供应商、最低 token 门槛、缓存有效期和请求路由决定。

### 第三部分：TodoList

当前 TodoList 永远是最新 User 内容的最后一段：

```text
【TodoList｜当前唯一权威状态】
[x] 编写代码
[>] 烧录代码
[ ] 测试代码
[ ] 完成
```

四项名称、顺序和数量固定，模型只能更新状态；后面不得追加计时、要求、解释或工具结果。

“当前 Todo 不命中缓存”的准确含义是：本轮新生成或改变的 Todo 后缀没有出现在上一轮请求中，因此不能依赖上一轮缓存；当它成为下一轮不可修改的历史内容后，它可以属于下一轮的公共前缀。

## 2. 缘何不把动态 Todo 放进 System

Prompt Cache 依赖精确前缀。若动态 Todo 位于 System 中，Todo 变化会让其后的工具定义和长历史全部失去公共前缀资格。

```text
不推荐：
[固定 System][动态 Todo][工具][长历史]
               ↑ 变化后，右侧都无法继续匹配

推荐：
[固定 System][固定工具][追加历史][动态 Todo]
                                      ↑ 只影响末尾
```

## 3. Skill 正文的位置

初始 System Prompt 只保存 Skill 名称和描述。命中 Skill 后：

```text
assistant → Skill 工具调用
tool_result → 完整 SKILL.md 正文
```

该工具结果进入追加式历史，后续轮次仍能看到，除非未来实现显式压缩或截断上下文。

## 4. 每轮完整快照

每轮结束立即写：

```text
file/<任务>/rounds/round-NNNN.md
```

快照使用实际发送和接收的内容，不使用摘要替代。即使任务异常结束，已完成轮次的快照仍然存在。

## 5. 缓存验证

架构只保证提供稳定精确前缀，不保证供应商一定命中缓存。若 API 返回缓存用量字段，应把实际的 cached input token 数据写入后续监控；没有供应商统计时，不得声称某轮已经命中。
