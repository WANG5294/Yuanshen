# Yuanshen v1.1.5

面向 ESP32 MicroPython 的闭环开发 Agent。用户确认规范化需求与接线后，Agent 自动完成编写代码、烧录为板上 `main.py`、实机测试和结果汇报。

[![App Version](https://img.shields.io/badge/app-1.1.5-blue)](https://github.com/WANG5294/Yuanshen)
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

Yuanshen 是一个可运行在 Windows、Ubuntu/Linux 或 macOS 上的命令行 Agent，专用于 ESP32 单片机开发。它把“需求理解 → 代码生成 → 文件部署 → 实机验证”串成闭环：

1. **需求与接线规范化**：独立模型读取 ESP32 硬件参考手册、当前 `wiring.md` 和用户任务，生成可测试的需求与规范化接线，经用户确认后写入 `requirement.md` 并更新 `wiring.md`。
2. **Agent 闭环执行**：主体模型在固定 System Prompt 下，按"编写代码 → 烧录代码 → 测试代码 → 完成"主线推进。
3. **可靠部署与实机验证**：通过 MCP 工具连接串口；文件先上传到临时路径，在 ESP32 端核对大小与 SHA-256 后再安全替换目标文件。上传 `main.py` 时保留失败回滚路径；音频任务额外使用麦克风闭环验证。
4. **审计与沉淀**：每轮保存完整快照到 `project/<项目>/rounds/`，任务结束后可提取经验为 Skill。

---

## 核心特性

- **现代化终端 UI**：ASCII art 启动画面、Rich 彩色面板/表格、打字机效果输出、橙色分隔线、对话颜色区分。
- **规范化门禁**：任务执行前必须用户确认需求和接线，原始提示词不会直接进入 Agent 循环。
- **固定 Prompt 前缀**：System Prompt 在单任务内冻结，历史只追加、不回改，TodoList 固定在最末尾，尽可能复用公共前缀。
- **四项主线 TodoList**：`编写代码`、`烧录代码`、`测试代码`、`完成`，不可改名、调序或插入子项。
- **实机证据判定**：只有上传为板上 `main.py` 且明确执行 `main.py` 才算完成烧录与测试；代码修改会自动失效旧证据。
- **跨平台串口连接**：兼容 Windows `COM*` 与 Linux `/dev/ttyACM*`、`/dev/ttyUSB*`；唯一 USB 串口可自动选择，多设备时要求明确指定。
- **可校验安全上传**：临时文件写入后在 ESP32 端计算 SHA-256，校验通过才替换目标文件；设备异常和 Raw REPL 协议异常不会误报为 `OK`。
- **连接自动释放**：程序退出时关闭 MCP 子进程及其持有的串口，减少 IDE、串口监视器或其他 Yuanshen 实例无法连接的问题。
- **音频可降级验收**：`AUDIO_VALIDATION_MODE` 支持 `auto`（默认）/`required`/`off`，音频异常时不拖垮主任务。
- **逐轮审计快照**：每轮模型调用后保存 System Prompt、工具定义、完整消息链、模型响应和 TodoList。
- **Skill 知识库**：硬件手册拆分为 11 个主题 Skill，按需加载；历史经验经用户确认后沉淀为 `exp-*` Skill。
- **命令 Tab 补全**：所有斜杠命令支持 Tab 补全，带中文描述。
- **内联 API Key 管理**：`/api-key` 命令可直接输入并保存 Key 到 `.env`，无需手动编辑文件。
- **项目化管理**：`/new 项目名` 创建独立项目目录，`/history` 回顾历史项目。

---

## 快速开始

### 方式一：源码运行

前置要求：系统已安装 Python 3.10 或更新版本。

#### Linux / macOS

```bash
# 克隆仓库
git clone https://github.com/WANG5294/Yuanshen.git
cd Yuanshen

# 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 启动
.venv/bin/python yuanshen.py
```

#### Windows PowerShell

```powershell
git clone https://github.com/WANG5294/Yuanshen.git
Set-Location Yuanshen
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env 并填入对应模型的 API Key
.\.venv\Scripts\python.exe .\yuanshen.py
```

### 方式二：npm 全局安装

```bash
npm install -g yuanshen-esp32-agent
yuanshen
```

首次运行会自动创建 `.venv` 并安装 Python 依赖。

### 连接 ESP32

默认 `ESP32_PORT=auto`。当系统中恰好存在一个 USB 串口时，长连接会自动选择它；存在多个候选设备时，请使用 `/port` 或 `.env` 明确指定，例如：

```text
ESP32_PORT=/dev/ttyACM0   # Linux
ESP32_PORT=COM5           # Windows
```

Linux 串口访问通常需要当前用户在 `dialout` 组：

```bash
sudo usermod -aG dialout $USER
# 重新登录后生效
```

连接前请关闭 Thonny、串口监视器、Pymakr、其他 `mpremote` 或 Yuanshen 实例。Linux 设备重新插拔后编号可能变化，应重新运行 `/work` 或 `/port` 枚举；Windows 请在设备管理器确认实际 `COM` 号。

---

## 配置

在项目根目录创建 `.env`（可复制 `.env.example`，或使用 `/api-key` 命令在程序内设置）：

```text
# 默认模型：deepseek-v4-pro | deepseek-v4-flash | kimi-k3 | kimi-k2.7
MODEL=deepseek-v4-pro

# DeepSeek 模型使用
DEEPSEEK_API_KEY=你的DeepSeek_Key

# Kimi 模型使用
MOONSHOT_API_KEY=你的Moonshot_Key

# 音频验收模式
AUDIO_VALIDATION_MODE=auto   # auto | required | off

# ESP32 串口；单个 USB 串口可用 auto，多设备时明确指定
ESP32_PORT=auto
```

- `MODEL` 决定启动时默认使用的模型；不同模型需要对应 Key。
- 运行中可通过 `/model` 选择或 `/api-key` 设置新 Key（自动保存到 `.env`）。
- 运行中可通过 `/audio` 切换音频验收模式，仅当前会话生效。
- 运行中可通过 `/port` 查看、枚举或切换 ESP32 串口。

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
| `/port` | 查看、枚举或切换 ESP32 串口 |
| `/doc <md路径>` | 导入符合格式的硬件说明文档为 Skill |
| `/new 项目名` | 创建新 ESP32 项目（项目文件保存在 project/ 下） |
| `/history` | 浏览历史项目，输入编号查看详情 |
| `/exit` | 退出程序 |

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
├── project/                     # 项目目录（/new 创建的项目）
│   ├── .gitkeep
│   └── <时间戳_项目名>/
│       ├── requirement.md       # 用户确认后的规范化需求
│       ├── wiring.md            # 项目专属接线文档
│       ├── main.py              # 最终烧录到板上的程序
│       ├── userprompt.md        # 完整消息链
│       └── rounds/
│           ├── round-0001.md    # 每轮完整快照
│           └── ...
├── skills/                      # 硬件知识与经验 Skill
│   ├── esp32-gpio-capabilities/
│   ├── esp32-led-key-buzzer/
│   └── ...
├── yuanshen.py                  # Agent 主程序
├── esp32_piano_mcp.py           # MCP 服务器（设备通道 + 音频闭环）
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

---

## 文档

- [架构说明](docs/architecture-v4.md)
- [Prompt 与缓存结构](docs/prompt-architecture-v4.md)
- [用户指南](docs/user-guide.md)
- [全代码风险审查](docs/code-audit-v1.0.md)
- [ESP32 硬件参考手册](docs/reference/修正ESP32_D0WD_硬件开发手册.md)

---

## 开发

本地语法检查：

```bash
python3 -m py_compile yuanshen.py
python3 -m py_compile esp32_piano_mcp.py
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m py_compile .\yuanshen.py .\esp32_piano_mcp.py
```

清理缓存：

```bash
find . -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} +
```

---

## 许可证

MIT —— 详见 [package.json](package.json)。
