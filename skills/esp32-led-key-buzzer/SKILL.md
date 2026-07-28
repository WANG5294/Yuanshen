---
name: esp32-led-key-buzzer
description: 板载 LED、KEY1/KEY2、BOOT、RESET 与 MLT-5020 蜂鸣器的电路、有效电平、安全初始化、消抖、中断和基本发声。实现点灯、按键控制、提示音、J11 隔离或外接喇叭安全时加载。
---

# LED、按键与蜂鸣器

GPIO 能力与冲突以 `esp32-gpio-capabilities` 为准；音阶、响度包络、RMT 等波形生成
加载 `esp32-dac-pwm-rmt-and-neopixel`。

## LED

```text
VCC ─ LED2(绿) ─ R10 ─ GPIO32
VCC ─ LED3(红) ─ R11 ─ GPIO33
VCC ─ R3 ─ LED1(红色电源灯) ─ GND
```

LED2/LED3 为共阳、**低电平点亮**：0=亮，1=灭；LED1 通电常亮且不可编程。
初始化先输出高电平，避免上电闪光：

```python
from machine import Pin
green = Pin(32, Pin.OUT, value=1)
red = Pin(33, Pin.OUT, value=1)
green.value(0)       # 点亮
green.value(1)       # 熄灭
```

板载 R10/R11 已限流。GPIO32/33 同时有 ADC1 和触摸能力，但 LED 会形成负载。

## 按键

| 丝印 | 位号 | 连接 | 读值/用途 |
|---|---|---|---|
| KEY1 | K4 | GPIO35，R12 1kΩ 上拉，按下接地 | 0=按下 |
| KEY2 | K3 | GPIO34，R9 1kΩ 上拉，按下接地 | 0=按下 |
| BOOT | K1 | GPIO0，按下接地 | 运行时可读；复位时按住进下载模式 |
| RESET | K2 | CHIP_PU/EN，按下接地 | 硬件复位，不可编程 |

历史代码可能把 GPIO34 注释成 KEY1；以板上丝印和网表为准：
**KEY1=GPIO35，KEY2=GPIO34**。34/35 仅输入且没有内部拉阻，不要设 `Pin.OUT` 或依赖
`PULL_UP`；板上已经外部上拉。

```python
from machine import Pin
import time

key1 = Pin(35, Pin.IN)
key2 = Pin(34, Pin.IN)
boot = Pin(0, Pin.IN, Pin.PULL_UP)

if key1.value() == 0:
    time.sleep_ms(20)
    if key1.value() == 0:
        print("KEY1 pressed")
```

中断回调只置标志，消抖和业务放到主循环：

```python
pressed = False
def on_key(pin):
    global pressed
    pressed = True
key1.irq(trigger=Pin.IRQ_FALLING, handler=on_key)
```

自动化 agent 无法按实体键。把按键触发的业务封装成可导入函数，用 REPL 调用该函数
做软件触发；仍应单独检查按键读值和消抖逻辑。

## 蜂鸣器

```text
VCC ─ B1(MLT-5020 压电蜂鸣器) ─ Q3 集电极
GPIO25 ─ J11 ─ R13 ─ Q3 基极；Q3 发射极 ─ GND
```

GPIO25 高使 NPN Q3 导通。J11 默认插帽；拔掉可隔离 GPIO25，做 DAC 或其他用途。

```python
from machine import Pin, PWM
buzzer = PWM(Pin(25), freq=440, duty=0)
buzzer.duty(512)       # 10-bit API 下约 50%，发声
buzzer.duty(0)         # 静音
buzzer.deinit()
Pin(25, Pin.OUT, value=0)
```

MLT-5020 是压电负载：PWM 频率决定音高，改变载波占空比不等于可靠的线性音量控制。
结束时先置静音再释放，复杂音频用约 10ms 包络斜坡减少电平阶跃爆音。

严禁把 8Ω 喇叭直接接 GPIO25-GND；电流会远超 GPIO 能力。使用板载 Q3 的合适负载
路径、限流/隔直，或外接功放（如 PAM8403）。实机声音验收加载
`esp32-audio-closed-loop-validation`。
