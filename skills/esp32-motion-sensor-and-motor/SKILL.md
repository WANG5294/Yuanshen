---
name: esp32-motion-sensor-and-motor
description: 板载 MPU6050 六轴传感器与 MX620B H 桥电机接口的接线、地址、供电、初始化、方向和 PWM 控制。读取姿态、扫描 IMU、驱动直流电机或振动反馈时加载。
---

# 运动传感器与电机

本 Skill 只定义板载器件接法和操作。通用 I²C、PWM 与选脚规则分别见
`esp32-spi-i2c-uart-and-sd`、`esp32-dac-pwm-rmt-and-neopixel` 和
`esp32-gpio-capabilities`。

## MPU6050 六轴 IMU

| MPU6050 U7 | 板级连接 |
|---|---|
| VCC/GND | 3.3V / GND |
| SDA | GPIO16，R7 10kΩ 上拉至 VCC |
| SCL | GPIO17，R8 10kΩ 上拉至 VCC |
| AD0 | 电平决定地址；板级资料未确认固定电平 |

地址必须通过扫描确认：AD0=GND 时为 `0x68`，AD0=VCC 时为 `0x69`。不要在没有
网表证据时写死 AD0 接法。

```python
from machine import I2C, Pin
import time

time.sleep_ms(100)
i2c = I2C(0, scl=Pin(17), sda=Pin(16), freq=400_000)
found = i2c.scan()
addr = next((x for x in found if x in (0x68, 0x69)), None)
if addr is None:
    raise OSError("MPU6050 not found")

i2c.writeto_mem(addr, 0x6B, b"\x00")  # PWR_MGMT_1: wake
who = i2c.readfrom_mem(addr, 0x75, 1)[0]
```

- 上电后等待约 100ms，再唤醒并读 `WHO_AM_I`。
- I²C 频率不超过 400kHz；长外接线或多设备时可降到 100kHz。
- MPU6050 由板上 3.3V 供电，不要向信号线或 VCC 注入 5V。
- GPIO16/17 同时在 J5 引出；外接 I²C 设备可共用这对线和上拉，但要避免地址冲突、
  上拉过强和总线电容过大。
- 读取加速度/陀螺仪的 16 位寄存器时使用有符号大端转换；量程换算必须匹配配置寄存器。

## MX620B H 桥

| MX620B U6 | 板级连接 |
|---|---|
| 逻辑电源 | VCC 3.3V |
| 电机电源 | BAT，标称约 5V |
| IN1 / IN2 | GPIO26 / GPIO27 |
| 输出 A1 / A2 | J6_2 / J6_3 |
| J6_1 / J6_4 | GND / BAT |

电机跨接 J6 的 A1/A2；不要把电机接在 A1-GND 或 GPIO-GND。GPIO26/27 会直接控制
H 桥输入，复用 DAC、ADC 或触摸前先断开电机并确认无意动作风险。

常用控制状态：

| IN1 | IN2 | 行为 |
|:---:|:---:|---|
| 0 | 0 | 停止/滑行（具体制动模式以 MX620B 资料核对） |
| PWM | 0 | 方向 A，PWM 控速 |
| 0 | PWM | 方向 B，PWM 控速 |
| 1 | 1 | 不作为默认控制；按器件资料确认制动状态 |

```python
from machine import Pin, PWM

in1 = PWM(Pin(26), freq=5_000, duty=0)
in2 = PWM(Pin(27), freq=5_000, duty=0)

def drive(duty):
    duty = max(-1023, min(1023, int(duty)))
    if duty >= 0:
        in2.duty(0); in1.duty(duty)
    else:
        in1.duty(0); in2.duty(-duty)

def stop():
    in1.duty(0); in2.duty(0)
```

换向前先将两路置 0 并留短暂死区，避免贯通和机械冲击。电机启动电流与反电动势可能
造成复位或噪声；必要时使用独立合规电源并共地、增加去耦/抑制，且不超过板上 F1、
走线和驱动芯片能力。GPIO27 的 ADC2 在 Wi-Fi 开启时不可用，但数字 PWM 不受此限制。
