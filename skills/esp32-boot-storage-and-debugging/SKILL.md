---
name: esp32-boot-storage-and-debugging
description: ESP32 板的 CP2102N、自动下载、4MB SPI Flash、MicroPython 启动顺序、UART0 REPL、内存 GC、死循环恢复、soft_reset 与 mpremote 串口故障处理。烧录、连接失败、REPL 被占、程序卡死或启动异常时加载。
---

# 启动、存储与调试恢复

GPIO0/Strapping 的电气定义见 `esp32-gpio-capabilities`；这里负责启动链和调试流程。

## USB-UART 与自动下载

```text
USB D+/D- ─ CP2102N
CP2102N TXD ─ R14 ─ GPIO1(U0TXD)
CP2102N RXD ───────── GPIO3(U0RXD)
DTR ─ Q2 ─ GPIO0
RTS ─ Q1 ─ CHIP_PU/EN
```

烧录工具通过 DTR/RTS 自动控制下载模式与复位，一般不需手按 BOOT+RESET。串口通常为
115200-8-N-1；实际设备名先枚举确认，不要写死 `/dev/ttyACM0` 或 `/dev/ttyUSB0`。
UART0 同时承载 ROM 日志和 MicroPython REPL，因此程序使用 GPIO1/3 会破坏调试通道。

## Flash 与启动顺序

- U5 ZB25VQ32 为 32Mbit/4MB Flash，使用芯片专用 GPIO6~11；这些脚绝不可复用。
- “固件约 1.5MB、文件系统约 2.5MB”只能作常见布局示例；实际分区以当前固件镜像
  和 `os.statvfs('/')` 为准。
- MicroPython 正常顺序：上电/复位 → `boot.py` → `main.py` → REPL。
- `main.py` 若永久循环，REPL 仍可能因循环不让步、异常洪泛或串口占用而难以连接。
  循环中让步并提供退出/维护窗口，开发阶段避免开机立即锁死。

```python
import time
time.sleep_ms(500)  # 仅在确有外设稳定或维护窗口需求时使用
```

## 安全调试流程

1. 枚举串口并确认没有串口监视器、IDE 或另一个 mpremote 占用。
2. 先用短命令验证 REPL，再上传模块；最后才部署为 `main.py`。
3. 长运行脚本使用工具超时；业务逻辑封装为函数，便于 REPL 软件触发。
4. 部署前保留可恢复路径；若 `main.py` 卡死，利用复位后的中断窗口进入原始 REPL，
   或按住 BOOT/使用安全模式方案后重命名问题文件。
5. 诊断启动异常时先拆掉可能影响 Strapping 的外设，再看 ROM 启动日志。

## mpremote / REPL 连接失败

典型错误含 `mpremote connect /dev/tty...`、端口忙、脚本不返回或前一会话异常断开。
按以下顺序恢复，不把“反复复位”当作唯一办法：

1. 关闭其他串口客户端，重新枚举并确认端口与权限。
2. 对当前工具调用使用 `soft_reset`，然后重试一次 `repl_exec`。
3. 若仍失败，再做一次 `soft_reset → repl_exec`；已知实机任务曾由此从挂起会话恢复。
4. 连续失败时停止循环复位，检查 USB 透传/线缆、设备是否重枚举、`main.py` 是否立即
   抢占 REPL，必要时重新插拔 USB 或进入维护启动。

MicroPython 原始 REPL 的软复位（通常 Ctrl-D）会重启解释器并重新执行启动文件；它不等
同于擦除 Flash，也不修复物理 USB 故障。工具名 `soft_reset` 的具体实现以 MCP 工具说明
为准。

释放 UART0 可使用 `uos.dupterm(None, 1)`，但会主动失去当前串口 REPL；只有已有
WebREPL/替代控制通道且任务确需 GPIO1/3 时才做。

## 内存与异常

```python
import gc
gc.collect()
print(gc.mem_free())
```

在分配大音频/传感器缓冲前后主动回收并检查余量；优先复用缓冲。ISR 中禁止分配对象。
捕获异常时输出有限日志并让主循环退让，避免高速 traceback 让 REPL 看似失联。
`machine.reset_cause()` 可区分部分复位来源；Deep Sleep 的专门处理见
`esp32-deep-sleep-and-wakeup`。
