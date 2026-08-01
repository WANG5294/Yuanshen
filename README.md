# Yuanshen v1.2.2

面向 ESP32 MicroPython 的闭环开发 Agent。用户确认规范化需求与接线后，Agent 自动完成编写代码、烧录为板上 `main.py`、实机测试和结果汇报。

[![Version](https://img.shields.io/badge/version-1.2.2-blue)](https://www.npmjs.com/package/yuanshen-esp32-agent)
[![License](https://img.shields.io/badge/license-MIT-green)](./package.json)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](./requirements.txt)
[![npm](https://img.shields.io/npm/v/yuanshen-esp32-agent)](https://www.npmjs.com/package/yuanshen-esp32-agent)

---

## 目录

- [简介](#简介)
- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [配置](#配置)
- [常用命令](#常用命令)
- [项目结构](#项目结构)
- [文档](#文档)
- [开发](#开发)
- [许可证](#许可证)

---

## 简介

Yuanshen 是一个跨平台命令行 Agent（Windows 物理机 / Linux（含虚拟机）/ macOS，运行环境由程序自动探测并注入 System Prompt），专用于 ESP32 单片机开发。它把"需求理解 → 代码生成 → 固件烧录 → 实机验证"串成闭环：

1. **需求与接线规范化**：独立模型读取 ESP32 硬件参考手册、当前 `wiring.md` 和用户任务，生成可测试的需求与规范化接线，经用户确认后写入 `requirement.md` 并更新 `wiring.md`。
2. **Agent 闭环执行**：主体模型在固定 System Prompt 下，按"编写代码 → 烧录代码 → 测试代码 → 完成"主线推进。
3. **实机验证**：通过 MCP 工具连接串口，上传并执行板上 `main.py`；音频任务额外使用麦克风闭环验证。
4. **审计与沉淀**：每轮保存完整快照到 `~/.yuanshen/projects/<项目>/rounds/`，任务结束后可提取经验为 Skill。

---

## 核心特性

- **现代化终端 UI**：ASCII art 启动画面、Rich 彩色面板/表格、**真正的 token 级流式输出**（模型回复像打字一样逐字出现，工具轮思考块保留回传）、需求确认页左右分栏+变更高亮、主线进度条、轮次结果卡片、最终报告模板、`/work` 健康仪表盘（含修复建议）、`/help` 分组命令菜单。
- **规范化门禁**：任务执行前必须用户确认需求和接线，原始提示词不会直接进入 Agent 循环。
- **固定 Prompt 前缀**：System Prompt 在单任务内冻结，历史只追加、不回改，TodoList 固定在最末尾，尽可能复用公共前缀。
- **四项主线 TodoList**：`编写代码`、`烧录代码`、`测试代码`、`完成`，不可改名、调序或插入子项。
- **实机证据判定**：只有上传为板上 `main.py` 且明确执行 `main.py` 才算完成烧录与测试；代码修改会自动失效旧证据。
- **长连接设备通道**：默认通过 pyserial 持久 raw REPL 会话操作开发板（单次执行 ~0.1s，任务期间独占串口、避免被其他程序抢占）；`ESP32_DEVICE_MODE=mpremote` 可回退为每次调用起 mpremote 子进程。
- **串口故障自诊断**：`check_port` 主动探测占用，静默超时自动分类为"已打开但 REPL 无应答"（WCH CH9102/CH340 驱动不强制独占的典型症状），不再误诊为板上死循环。
- **音频可降级验收**：`AUDIO_VALIDATION_MODE` 支持 `auto`（默认）/`required`/`off`，音频异常时不拖垮主任务。
- **逐轮审计快照**：每轮模型调用后保存 System Prompt、工具定义、完整消息链、模型响应和 TodoList。
- **Skill 知识库**：硬件手册拆分为 11 个主题 Skill，按需加载；历史经验经用户确认后沉淀为 `exp-*` Skill。
- **命令 Tab 补全**：所有斜杠命令支持 Tab 补全，带中文描述。
- **A2A 服务端**：可通过标准 Agent2Agent 协议（JSON-RPC over HTTP）把 Yuanshen 暴露给多 Agent 系统；远程任务仍须本机终端人工确认后才执行硬件操作。
- **内联 API Key 管理**：`/api-key` 命令可直接输入并保存 Key 到 `.env`，无需手动编辑文件。
- **项目化管理**：`/new 项目名` 创建独立项目目录，`/history` 回顾历史项目。

---

## 快速开始

### 方式一：源码运行

前置要求：系统已安装 `python3`（建议 3.10+）。

```bash
# 克隆仓库
git clone https://github.com/WANG5294/Yuanshen.git
cd Yuanshen

# 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 启动（首次运行会自动创建 ~/.yuanshen/.env 并复制配置模板）
.venv/bin/python yuanshen.py

# 配置 API Key（二选一）：
#   方式一：编辑 ~/.yuanshen/.env 填入 Key 后重启
#   方式二：启动后在程序内运行 /api-key 命令
```

### 方式二：npm 全局安装

```bash
npm install -g yuanshen-esp32-agent
yuanshen
```

首次运行会自动创建 `.venv` 并安装 Python 依赖。

### 串口权限

- **Linux**：串口访问需要当前用户在 `dialout` 组；虚拟机运行还需把 USB 串口透传进虚拟机。

  ```bash
  sudo usermod -aG dialout $USER
  # 重新登录后生效
  ```

- **Windows**：免配置，串口为 `COM*`（如 COM5）。注意串口同一时刻只能被一个程序有效使用——
  使用前请关闭 Pymakr、串口监视器、Thonny 等占用方；WCH 芯片（CH9102/CH340）驱动
  不强制独占，被占用时表现为"能打开但无应答"的静默超时。
- **macOS**：串口为 `/dev/cu.usbserial-*`。

---

## 配置

配置文件位于 `~/.yuanshen/.env`（用户数据目录）。首次运行程序会自动从 `.env.example` 创建模板；也可以直接在程序内用 `/api-key` 命令设置。项目根目录的 `.env` 仍兼容（旧版升级），优先级为 `~/.yuanshen/.env` 优先。

```text
# 默认模型：deepseek-v4-pro | deepseek-v4-flash | kimi-k3 | kimi-k2.7
MODEL=deepseek-v4-pro

# DeepSeek 模型使用
DEEPSEEK_API_KEY=你的DeepSeek_Key

# Kimi 模型使用
MOONSHOT_API_KEY=你的Moonshot_Key

# 音频验收模式
AUDIO_VALIDATION_MODE=auto   # auto | required | off

# 串口（可选）：固定连接 ESP32 的串口，避免 auto 误选蓝牙等虚拟串口
ESP32_PORT=COM5              # Windows: COM5 等；Linux: /dev/ttyUSB0 等

# 设备通道模式（可选）：persistent 长连接（默认）| mpremote 短连接回退
ESP32_DEVICE_MODE=persistent

# A2A 服务端（--a2a 启动时生效）
A2A_HOST=127.0.0.1         # 监听地址；0.0.0.0 暴露到局域网（无认证，谨慎）
A2A_PORT=9999
# A2A_BASE_URL=            # Agent Card 对外 URL（反向代理/内网穿透时设置）
```

- `MODEL` 决定启动时默认使用的模型；不同模型需要对应 Key。
- 运行中可通过 `/model` 选择或 `/api-key` 设置新 Key（自动保存到 `.env`）。
- 运行中可通过 `/audio` 切换音频验收模式，仅当前会话生效。
- 运行中可通过 `/port` 查看/切换串口（热切换，自动释放旧长连接）。

---

## 常用命令

在 Agent 提示符 `>` 处输入（支持 Tab 补全）：

| 命令 | 作用 |
|------|------|
| `/work` | 检查串口、板子 REPL、麦克风、Skill、API 等环境状态 |
| `/tool` | 查看本地工具与 MCP 工具列表 |
| `/skill` | 查看已加载 Skill |
| `/wiring` | 查看当前接线文档 |
| `/model` | 查看并切换大模型（Tab 补全模型名） |
| `/api-key` | 查看或更新当前模型的 API Key（自动保存到 .env） |
| `/audio` | 交互切换音频验收模式 |
| `/doc <md路径>` | 导入符合格式的硬件说明文档为 Skill |
| `/port` | 查看/切换连接 ESP32 的串口 |
| `/new 项目名` | 创建新 ESP32 项目（项目文件保存在 `~/.yuanshen/projects/` 下） |
| `/history` | 浏览历史项目，输入编号查看详情 |
| `/exit` | 退出程序 |

---

## A2A 服务端（Agent2Agent）

Yuanshen 可作为标准 [A2A 协议](https://a2a-protocol.org) 的服务端 Agent，接入多 Agent 系统，由其他 Agent/客户端远程下发 ESP32 开发任务。

```bash
# 源码运行
.venv/bin/python yuanshen.py --a2a                 # 默认 127.0.0.1:9999
.venv/bin/python yuanshen.py --a2a --a2a-port 9000

# npm 安装后
yuanshen --a2a
```

- **Agent Card**：`http://127.0.0.1:9999/.well-known/agent.json`（名称、技能、能力声明）。
- **任务下发**：向 `http://127.0.0.1:9999/` POST JSON-RPC `message/send`；任务为非流式，最终报告放在 `final_report` artifact 中返回，可用 `tasks/get` 回查。
- **人工确认门禁**：远程任务的规范化需求/接线仍在本机终端逐条确认（`y` 执行 / `n` 拒绝 / 输入修改意见重优化），确认前不会触碰硬件；无输入（如守护进程）一律视为拒绝。
- **单任务串行**：串口独占，同一时刻只执行一个远程任务；占用期间新任务立即返回 `failed`（busy），调用方应稍后重试。
- **独立项目目录**：每个远程任务在 `~/.yuanshen/projects/<时间戳>_a2a-<slug>/` 下归档，与本地项目一致（含 rounds/ 逐轮快照）。
- **安全提示**：v1 无认证机制，请保持默认 `127.0.0.1` 绑定；如需对外暴露请自行加反向代理鉴权。不支持流式与执行中取消。

调用示例（curl）：

```bash
curl -X POST http://127.0.0.1:9999/ \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": "1", "method": "message/send",
    "params": {"message": {"role": "user", "messageId": "m-1",
      "parts": [{"kind": "text", "text": "用 GPIO4 的 LED 做每秒闪烁"}]}}
  }'
```

回归/冒烟测试（服务端在运行时）：`python project/test_a2a_client.py`。

### 从 Kimi Code 调用（MCP 桥）

`a2a_client_mcp.py` 把 A2A 调用封装成 MCP 工具，Kimi Code 等 MCP 客户端挂载后即可把 ESP32 任务委派给 Yuanshen（项目级配置见 `.kimi-code/mcp.json`，新会话生效，`/mcp` 查看连接状态）：

- `yuanshen_agent_card` — 查看 Yuanshen 能力名片/在线状态
- `yuanshen_esp32_task(requirement)` — 下发开发任务，阻塞返回实机测试报告

注意 `toolTimeoutMs` 必须放大（配置中为 900s），否则长任务会被客户端提前掐断。也可 CLI 单测：`python a2a_client_mcp.py yuanshen_esp32_task "需求..."`。

---

## 项目结构

```text
Yuanshen/
├── bin/
│   └── yuanshen.js              # npm 启动器：自动准备 venv 与依赖
├── docs/
│   ├── architecture-v4.md       # 架构说明
│   ├── prompt-architecture-v4.md# Prompt 与缓存结构
│   ├── user-guide.md            # 用户指南
│   ├── code-audit-v1.0.md       # 风险审查与修复记录
│   ├── reference/               # ESP32 硬件参考手册
│   └── legacy/                  # 历史文档
├── project/                     # 预留目录（实际项目在 ~/.yuanshen/projects/）
│   └── .gitkeep
├── skills/                      # 硬件知识与经验 Skill
│   ├── esp32-gpio-capabilities/
│   ├── esp32-led-key-buzzer/
│   └── ...
├── yuanshen.py                  # 入口：命令分发与主 REPL
├── main.py                      # REPL 连通性检查脚本（ESP32 板上程序）
├── yuanshen/                    # 核心包
│   ├── __init__.py              # 兼容导出（供 a2a_server 等导入）
│   ├── config.py                # 配置、全局状态、路径常量
│   ├── models.py                # 大模型客户端与 API Key 管理
│   ├── mcp_client.py            # MCP 最小客户端
│   ├── skills.py                # Skill 知识库
│   ├── todos.py                 # 主线任务状态机
│   ├── prompts.py               # System/User Prompt 构建与归档渲染
│   ├── tools.py                 # 本地工具与 MCP 工具路由
│   ├── ui.py                    # Rich 终端 UI 渲染与输入
│   ├── agent.py                 # 需求规范化、Agent 循环、归档、经验提取
│   └── utils.py                 # 小型共享工具函数
├── a2a_server.py                # A2A 服务端（Agent Card + 远程任务桥接）
├── a2a_client_mcp.py            # A2A 客户端 MCP 桥（供 Kimi Code 等委派任务）
├── esp32_piano_mcp.py           # MCP 服务器（长连接设备通道 + 音频闭环）
├── wiring.md                    # 全局默认接线模板
├── requirements.txt             # Python 依赖
├── package.json                 # npm 包元数据
├── .env.example                 # 环境变量模板
├── .env                         # API Key 等配置（已 gitignore）
├── kimi.md                      # UI 改造方案文档
├── prompt/                      # 各轮改造提示词
│   └── prompt.md
└── README.md                    # 本文档
```

每个项目的内部结构（位于 `~/.yuanshen/projects/<时间戳_项目名>/`）：

```text
<时间戳_项目名>/
├── requirement.md       # 用户确认后的规范化需求
├── wiring.md            # 项目专属接线文档
├── main.py              # 最终烧录到板上的程序
├── userprompt.md        # 完整消息链
└── rounds/
    ├── round-0001.md    # 每轮完整快照
    └── ...
```

---

## 文档

- [v1.0 架构](docs/architecture-v4.md)
- [Prompt 与缓存结构](docs/prompt-architecture-v4.md)
- [用户指南](docs/user-guide.md)
- [v1.0 全代码风险审查](docs/code-audit-v1.0.md)
- [ESP32 硬件参考手册](docs/reference/修正ESP32_D0WD_硬件开发手册.md)

---

## 开发

本地语法检查：

```bash
python3 -m py_compile yuanshen.py yuanshen/*.py a2a_server.py esp32_piano_mcp.py
```

清理缓存：

```bash
find . -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} +
```

---

## 许可证

MIT —— 详见 [package.json](package.json)。
