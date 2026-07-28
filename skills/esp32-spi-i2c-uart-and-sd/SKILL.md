---
name: esp32-spi-i2c-uart-and-sd
description: 本板 SPI、I²C、UART 与 SD 卡 SPI 模式的总线权威说明，含推荐映射、板载占用、重映射、共享冲突和 MicroPython 初始化。连接显示屏、传感器、串口模块或 MicroSD 时加载。
---

# SPI、I²C、UART 与 SD

总线定义只在本 Skill 维护。连接器针号见 `esp32-connectors-and-pin-headers`，选脚限制
见 `esp32-gpio-capabilities`，MPU6050 寄存器操作见运动传感器 Skill。

## SPI

ESP32 的 SPI0/SPI1 服务内部 Flash，不可给用户外设。用户使用 HSPI/SPI2 或
VSPI/SPI3；GPIO matrix 允许重映射，但高速时优先原生引脚。

推荐 VSPI 组合全部位于 J4：

| 信号 | GPIO | J4 |
|---|:---:|:---:|
| SCLK | 18 | 7/8 |
| MISO | 19 | 9/10 |
| MOSI | 23 | 15/16 |
| CS | 5 | 1/2 |

GPIO5 是 Strapping，外设 CS 应有合适空闲电平，且不得在复位时强拉错误状态。传统 HSPI
默认 12/13/14/15 含多个 Strapping；若必须使用，可改为 SCLK=14、MISO=19、
MOSI=23、CS=5，并逐项核对启动电平。优先使用更安全的 VSPI 组合。

```python
from machine import SPI, Pin
spi = SPI(2, baudrate=10_000_000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(23), miso=Pin(19))
cs = Pin(5, Pin.OUT, value=1)
```

同一总线可共享 SCLK/MOSI/MISO，但每个设备必须独立 CS。访问不同 mode、字宽或速率的
设备前重新初始化/配置，并确保非目标 CS 为高。长线和面包板先降低速率。

## I²C

ESP32 的 I²C 控制器可通过 GPIO matrix 映射，MicroPython 的端口号与“默认脚”可能随
固件版本变化；显式给出 `sda`、`scl`。

| 用途 | SDA | SCL | 说明 |
|---|:---:|:---:|---|
| 板载 MPU6050 | 16 | 17 | 板载 R7/R8 各 10kΩ 上拉，≤400kHz |
| 推荐外部总线 | 21 | 22 | 均在 J4；与 MPU 分开便于隔离 |

```python
from machine import I2C, SoftI2C, Pin
i2c_mpu = I2C(0, sda=Pin(16), scl=Pin(17), freq=400_000)
i2c_ext = SoftI2C(sda=Pin(21), scl=Pin(22), freq=100_000)
print(i2c_mpu.scan())
```

多个设备可以同总线但地址必须不同。检查所有模块自带上拉的并联等效值；3.3V 总线不得
被 5V 模块上拉。扫描只证明有 ACK，不证明器件型号或量程配置正确。

## UART

| UART | 默认/板级状态 | 建议 |
|---|---|---|
| UART0 | GPIO1/3 接 CP2102N，REPL 使用 | 保留作调试 |
| UART1 | 默认 GPIO9/10 等落在 Flash 6~11 | 不用默认脚；仅在固件允许时重映射 |
| UART2 | 默认 17/16 与 MPU6050 I²C 重叠 | 重映射到可用排针 |

示例将 UART2 映射为 TX=GPIO5、RX=GPIO18：

```python
from machine import UART
uart = UART(2, baudrate=115200, tx=5, rx=18,
            bits=8, parity=None, stop=1)
```

这只是推荐组合，不是固定硬件连接；GPIO5 的启动约束仍需处理。UART 两端必须共地，
TX 接对端 RX、RX 接对端 TX，且只接 3.3V TTL，不能直接接 RS-232 电平。

## MicroSD

本板的 GPIO6~11 已连接板载 SPI Flash，因此不要尝试占用它们的 4-bit SDIO/MMC 接法。
外接 MicroSD 使用 SPI 模式和上面的 VSPI 组合：

```python
from machine import SPI, Pin, SDCard
import os

spi = SPI(2, baudrate=10_000_000,
          sck=Pin(18), mosi=Pin(23), miso=Pin(19))
sd = SDCard(spi, cs=Pin(5))
os.mount(sd, "/sd")
# 完成写入后先关闭文件，再 os.umount("/sd")
```

不同 MicroPython 版本可能采用 `machine.SDCard(slot=..., sck=..., ...)` 或不接受外部
`SPI` 对象；先检查当前固件 API，再调整构造方式。SD 模块必须兼容 3.3V 逻辑，写入时
不要突然断电，部署前验证挂载、读写、卸载和错误恢复。
