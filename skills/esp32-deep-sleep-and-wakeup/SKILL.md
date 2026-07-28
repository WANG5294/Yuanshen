---
name: esp32-deep-sleep-and-wakeup
description: 经典 ESP32 Deep Sleep、RTC GPIO、EXT0/EXT1、定时器和触摸唤醒、复位原因、RAM 状态与唤醒限制。实现低功耗、KEY 唤醒、多引脚唤醒或排查深睡重启时加载。
---

# Deep Sleep 与唤醒

芯片能力以 Espressif ESP32 睡眠模式文档为准，板载连接以 GPIO Skill 为准。不要沿用
遗漏 GPIO34/35 的旧表。

## RTC GPIO 与本板可达性

经典 ESP32 RTC GPIO 为：

`0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33, 34, 35, 36, 37, 38, 39`

本板可直接使用的 RTC GPIO 为 `0,2,4,12,13,14,15,25,26,27,32,33,34,35`；
36~39 未见排针引出。**EXT0 可以使用 GPIO34/35**，它们只输入并不妨碍电平唤醒。

板载影响：

| GPIO | 连接 | 睡眠唤醒注意 |
|:---:|---|---|
| 35 | KEY1，1kΩ 上拉、按下为低 | 很适合 EXT0 低电平唤醒 |
| 34 | KEY2，1kΩ 上拉、按下为低 | 同上 |
| 0 | BOOT 按键 | 唤醒后的复位阶段仍影响下载模式 |
| 25 | 蜂鸣器 | J11 连接时注意静态电平 |
| 26/27 | 电机 H 桥 | 睡前停机，避免悬空/误动作 |
| 32/33 | LED | 睡前设为熄灭，板载负载会增加系统电流 |
| 12/15/2 | Strapping | 外部唤醒电路还必须兼容复位启动电平 |

## 唤醒源

- Timer：不占 GPIO，最通用；`machine.deepsleep(ms)` 常直接设置定时唤醒。
- EXT0：一个 RTC GPIO、指定高或低电平；RTC peripherals 通常需要保持供电。
- EXT1：多个 RTC GPIO的组合条件。经典 ESP32 硬件支持 `ANY_HIGH` 或 `ALL_LOW`；
  MicroPython 常量/API 以固件版本为准，不能笼统写成“任意低”。
- Touch：RTC 触摸通道；阈值、固件 API 和 RTC 外设电源域限制需实机校准。
- ULP：芯片支持，但 MicroPython 固件是否提供完整工作流需另行确认。

某些唤醒源与 RTC 电源域选项、内部上下拉互相限制。例如 EXT0 与触摸/ULP 在部分
经典 ESP32 配置不能同时启用；EXT1 在关闭 RTC peripherals 时内部拉阻失效，应使用
外部电阻或 RTC GPIO hold。按当前 ESP-IDF/MicroPython 版本核对组合限制。

## KEY1 EXT0 示例

```python
import machine
import esp32

key1 = machine.Pin(35, machine.Pin.IN)  # 板载 R12 上拉
esp32.wake_on_ext0(pin=key1, level=esp32.WAKEUP_ALL_LOW)

# 先保存必要状态、停 PWM/电机、关闭文件并设置安全输出
machine.deepsleep()  # 无定时器，只等外部源
```

定时唤醒：

```python
machine.deepsleep(10_000)
```

EXT1 示例的函数签名在 MicroPython 版本间有差异，常见形式为
`esp32.wake_on_ext1(pins=(Pin(...), ...), level=esp32.WAKEUP_ANY_HIGH)`；先在当前
固件验证常量和是否支持目标逻辑。

## 启动路径与状态

Deep Sleep 唤醒表现为一次复位启动：重新执行 `boot.py` 和 `main.py`。普通 Python
堆、全局变量和外设对象不会保留；需要持久化的少量状态可用 RTC memory（若固件支持）
或 Flash，但不要为高频计数反复写 Flash。

```python
import machine
if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    print("woke from deep sleep")
else:
    print("cold/other reset")
```

睡前完成以下动作：

1. 关闭/卸载文件系统写入，保存必要状态。
2. 停止电机、蜂鸣器、PWM/RMT，设置外设安全电平。
3. 配置且验证唤醒源，防止已处于有效电平而立即重醒。
4. 测整板电流而非引用芯片裸片的“5~10µA”；CP2102N、LDO、LED、传感器与外接电路
   会让开发板实际 Deep Sleep 电流高得多。
5. 唤醒后用复位原因分支初始化，不假设 RAM 或总线状态仍存在。
