# Yuanshen v1.1.5 用户指南

## 启动

```bash
.venv/bin/python yuanshen.py
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe .\yuanshen.py
```

也可以安装 npm 包后运行：

```bash
yuanshen
```

## 使用流程

1. 输入 ESP32 任务目标。
2. 独立的前置模型读取 ESP32 板级硬件参考手册、当前 `wiring.md` 和用户要求，
   补全可确认的板载 GPIO、驱动和有效电平。
3. 输入 `y` 确认，输入修改意见重新生成，输入 `n` 取消。
4. 确认后保存 `requirement.md`、更新 `wiring.md`；两份确认结果被冻结到主体 Agent
   的 System Prompt，硬件手册全文不进入循环。
5. 用户确认后从 0 秒开始计时；每轮工具启动时实时显示轮次、累计秒数、工具和约
   50 字的目的，结束后在同一行更新成功/失败。
6. TodoList 始终位于输入末尾，只包含编写代码、烧录代码、测试代码、完成四项。
7. 在 `~/.yuanshen/projects/<任务>/rounds/` 查看每轮完整快照。
8. 在 `~/.yuanshen/projects/<任务>/userprompt.md` 查看任务结束后的完整消息链。

## 常用命令

| 命令 | 作用 |
|---|---|
| `/work` | 检查 API、串口、麦克风、Skill 和 MCP |
| `/audio` | 交互选择开启、关闭或严格音频验收 |
| `/audio on\|off\|required` | 直接切换当前会话的音频验收模式 |
| `/port` | 查看、枚举或切换 ESP32 串口 |
| `/wiring` | 查看当前接线 |
| `/tool` | 查看本地与 MCP 工具 |
| `/skill` | 查看已加载 Skill |
| `/doc <路径>` | 导入符合格式的 Skill 文档 |
| `exit` | 退出 |

## ESP32 连接与上传

- 默认使用 `ESP32_PORT=auto`。只有一个 USB 串口时会自动选择；多个候选设备时用
  `/port` 指定 Windows `COM*` 或 Linux `/dev/ttyACM*`、`/dev/ttyUSB*`。
- Linux 用户如遇权限错误，应加入 `dialout` 组并重新登录。
- 连接前关闭串口监视器、IDE 和其他 Yuanshen/mpremote 实例。
- 上传先写临时文件，在 ESP32 端校验大小与 SHA-256，校验通过后才替换目标；
  失败时保留原文件并报告错误。
- MicroPython traceback、Raw REPL 帧损坏和上传校验失败均属于工具失败，不能作为
  烧录或测试成功证据。
- 程序正常退出时会关闭 MCP 子进程并释放持久串口。

## 音频验收模式

在 `.env` 设置 `AUDIO_VALIDATION_MODE`：

- `auto`（默认）：尝试一次闭环；录音异常或未达标时停止音频重试，将其记为警告，
  其他板上执行证据合格后仍可完成任务。
- `required`：音频必须达到数字验收标准，否则任务不能完成。
- `off`：完全不调用麦克风工具，仅按板上执行证据验收。

`auto` 和 `off` 不会把未验证音频写成通过；最终报告会明确标注降级状态。

## 数据目录

- `~/.yuanshen/projects/`：任务、逐轮快照和最终产物。
- `~/.yuanshen/projects/<任务>/requirement.md`：循环外规范化并经用户确认的任务要求。

旧版本数据不会被程序自动迁移、覆盖或删除。

逐轮快照可能包含完整用户输入、模型输出、工具参数和工具结果。不要在任务输入或工具输出中放入不希望落盘的密钥。
