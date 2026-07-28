---
name: esp32-dac-pwm-rmt-and-neopixel
description: ESP32 DAC、LEDC PWM、频率与分辨率、舵机、蜂鸣器音阶、RMT、WS2812/NeoPixel、响度包络和防爆音。生成模拟电压、PWM、音调、舵机脉冲、精准波形或灯带协议时加载。
---

# DAC、PWM、RMT 与 NeoPixel

本 Skill 是波形输出的唯一完整定义。板载蜂鸣器电路见 `esp32-led-key-buzzer`；
GPIO 板载冲突见 `esp32-gpio-capabilities`。

## DAC

经典 ESP32 有两个 8-bit DAC：

| 通道 | GPIO | 本板负载 |
|---|:---:|---|
| DAC1 | 25 | J11 默认连接蜂鸣器 Q3；使用 DAC 前拔 J11 |
| DAC2 | 26 | MX620B IN1；输出会作用于电机驱动输入 |

```python
from machine import DAC, Pin
dac = DAC(Pin(25))
dac.write(128)  # 0..255，约中点；不是精密基准源
```

DAC 输出近似 0~VDD，不保证精密线性或大电流驱动。GPIO26 没有板上隔离跳线；除非确认
电机断开且 H 桥输入影响可接受，否则不要用它作模拟输出。

## LEDC PWM

ESP32 LEDC 有高速/低速共最多 16 个通道（具体暴露数量和 API 取决于 MicroPython
固件），频率约 1Hz~40MHz；频率越高，可用占空比分辨率越低。所有具备输出能力且可
经 GPIO matrix 路由的脚都可使用，GPIO34~39 不可输出。

```python
from machine import Pin, PWM
pwm = PWM(Pin(25), freq=440, duty=0)
pwm.duty(512)       # 旧式 10-bit API
pwm.freq(880)
pwm.duty(0)
pwm.deinit()
```

不同版本可能使用 `duty_u16()` 或 `duty_ns()`；先检查当前固件，不要混用量程。经验用途：

| 频率 | 典型用途 | 注意 |
|---:|---|---|
| 50Hz | 模拟舵机 | 以脉宽为准，通常约 1~2ms |
| 1kHz | LED 调光 | 板载 LED 低电平有效，逻辑反转 |
| 5~25kHz | 直流电机 | 结合驱动器规格、噪声和分辨率选择 |
| 262Hz~数 kHz | 压电蜂鸣器 | 频率定音高 |

```python
servo = PWM(Pin(2), freq=50)
servo.duty_ns(1_500_000)  # 固件支持时优先明确脉宽
```

旧式 10-bit、50Hz 下 `duty(77)` 约为 1.50ms，但须限制到舵机安全端点并单独供电共地。

## 音阶与响度

十二平均律 `f = 440 * 2**((midi-69)/12)`。常用 C4~C5：

| 音 | C4 | D4 | E4 | F4 | G4 | A4 | B4 | C5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hz | 262 | 294 | 330 | 349 | 392 | 440 | 494 | 523 |

压电蜂鸣器的载波占空比主要影响驱动波形，不提供可靠线性的“音量旋钮”。用低频开关门控
模拟音量会产生可听斩波/锯齿。需要平滑包络时，使用合适 DAC+功放，或用 RMT/PWM-DAC
生成高采样率波形，并以连续样本幅度形成包络。

为避免爆音：

1. 初始化为静音，切换频率前视情况先拉低幅度。
2. 波形首尾使用约 5~10ms 平滑斜坡，避免直流阶跃。
3. 连续缓冲之间保持相位和电平连续；避免 GC、分配和调试打印。
4. 停止时先输出静音/中点，再释放外设。

## RMT 与连续波形

经典 ESP32 RMT 有 8 通道、每通道块状内存，可生成精确定时脉冲、红外、WS2812 或
PWM-DAC 数据。MicroPython 的 `esp32.RMT` API 随版本变化，应检查固件能力。

RMT 音频常见“哒哒”来自块间空隙或电平不连续。若尖峰间隔约为 `CHUNK/RATE`，优先
检查 refill 延迟、内存块、循环边界、采样率推导和 GC，而不是把它当作音色。

## WS2812 / NeoPixel

```python
from machine import Pin
import neopixel
np = neopixel.NeoPixel(Pin(21), 8)
np[0] = (32, 0, 0)
np.write()
```

GPIO21 是无板载负载的常用选择。灯带使用独立足量电源时必须共地；ESP32 的 3.3V 数据
高电平对某些 5V WS2812 裕量不足，长线/高电流场景加电平转换、近端去耦和电源注入。
不要用 GPIO5/12/15 等启动敏感脚作为默认推荐。最终波形与声音质量用
`esp32-audio-closed-loop-validation` 验收。
