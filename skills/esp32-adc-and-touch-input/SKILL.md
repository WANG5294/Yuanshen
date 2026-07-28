---
name: esp32-adc-and-touch-input
description: ESP32 的 ADC1/ADC2、衰减、有效量程、非线性、Wi-Fi 限制、校准，以及电容触摸通道、基线校准和推荐引脚。读取模拟传感器、ADC 与 Wi-Fi 共存或制作触摸按键时加载。
---

# ADC 与电容触摸输入

GPIO 的板载占用以 `esp32-gpio-capabilities` 为准。本 Skill 负责模拟/触摸采样方法，
不重复排针和通用 GPIO 表。

## ADC 通道

| ADC | GPIO（本板状态） | Wi-Fi |
|---|---|---|
| ADC1 | 32(LED2)、33(LED3)、34(KEY2)、35(KEY1) | 可用 |
| ADC1 | 36、39（芯片有，但本板未见引出） | 可用 |
| ADC2 | 4、12、13、14、15（排针）、27(电机 IN2) | **不可用/冲突** |

需要 Wi-Fi 或 Bluetooth 同时工作时只选 ADC1。GPIO32/33 的 LED、GPIO34/35 的按键
上拉会影响模拟电压，不能把“通道存在”理解为板载负载已隔离。GPIO34/35 只输入恰好
适合 ADC，但板载 1kΩ 上拉很强，外部传感器必须能驱动该节点或进行硬件隔离。

## 衰减、量程与采样

经典 ESP32 ADC 标称 12-bit，但芯片间 Vref、衰减曲线和端点非线性差异明显。旧版
MicroPython 常见接口与近似范围：

| 设置 | 近似满量程 |
|---|---:|
| `ATTN_0DB` | 1.1V |
| `ATTN_2_5DB` | 1.5V |
| `ATTN_6DB` | 2.2V |
| `ATTN_11DB` | 约 3.3~3.6V，但输入绝不能超过 3.3V |

```python
from machine import ADC, Pin
import time

adc = ADC(Pin(34))
adc.atten(ADC.ATTN_11DB)
if hasattr(adc, "width"):
    adc.width(ADC.WIDTH_12BIT)

samples = []
for _ in range(32):
    samples.append(adc.read())
    time.sleep_us(200)
raw = sum(samples) / len(samples)
```

不要直接把 `raw * 3.3 / 4095` 当作精密电压；它只是粗略显示。11dB 时低端约
0~0.2V、高端约 3.1V 以上尤其非线性。精确测量应：

1. 用稳定已知电压覆盖实际工作范围采集校准点。
2. 对每块板建立线性/分段查表，或使用固件提供的校准后 `read_uv()`。
3. 做多次采样、去异常值/平均，并控制源阻抗；必要时加 RC 与缓冲器。
4. 先确认当前 MicroPython 版本的衰减常量和 `read_u16`/`read_uv` 语义。

## 电容触摸

| 通道 | GPIO | 本板负载/风险 |
|:---:|:---:|---|
| T0 | 4 | J8，优先 |
| T1/T2 | 0/2 | BOOT / Strapping |
| T3/T5 | 15/12 | Strapping |
| T4/T6 | 13/14 | 排针，可用 |
| T7 | 27 | MX620B IN2，避免 |
| T8/T9 | 33/32 | 板载 LED，避免 |

推荐外接触摸片优先 GPIO4、13、14；GPIO27 虽无相同启动风险但连着电机驱动，不应列为
“负载轻”。本板没有触摸焊盘，需要经排针接约 ≥10×10mm 铜箔，并尽量让走线短于
30cm、远离 PWM/电机与天线。

```python
from machine import TouchPad, Pin
import time

touch = TouchPad(Pin(4))
baseline_samples = [touch.read() for _ in range(32)]
baseline = sum(baseline_samples) / len(baseline_samples)
threshold = baseline * 0.7

while True:
    value = touch.read()
    active = value < threshold  # 经典 ESP32 通常触摸后读数下降
    time.sleep_ms(20)
```

“未触摸 800~1000、触摸 200~500”只能作经验示例，不能写死阈值。每块板、每个焊盘和
环境湿度都要校准；启动时取多点基线，运行中只在确定未触摸时缓慢跟踪漂移，并加入
迟滞与连续样本判定。Deep Sleep 的触摸唤醒限制见睡眠 Skill。
