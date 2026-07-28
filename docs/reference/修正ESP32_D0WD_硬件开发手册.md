# ESP32-D0WD 开发板硬件开发手册

> **文档目的**：本文档从底层硬件网表（Netlist）出发，完整描述 ESP32-D0WD 开发板的全部硬件资源、引脚映射、外设驱动电路及编程约束。开发者无需访问 Gerber 文件即可获得编程所需的全部硬件信息。
>
> **数据来源**：基于 FlyingProbeTesting.json（飞针测试网表）、Drill 钻孔文件、Gerber 板框层，经 MECE 交叉验证。
>
> **适用固件**：MicroPython（本文档引脚编号使用 ESP32 芯片 GPIO 编号）

---

## 一、开发板总体概览

| 属性 | 值 |
|------|-----|
| 主控芯片 | ESP32-D0WD-V3（双核 Xtensa LX6, 240MHz） |
| 板级尺寸 | **90.0mm × 40.0mm**（四角 R≈2.5mm 圆角） |
| Flash 存储 | ZB25VQ32（32Mbit / 4MB SPI Flash） |
| 晶振 | 40MHz（连接 ESP32 XTAL 引脚 U4_44 / U4_45） |
| USB 转串口 | CP2102N（Micro-USB 接口） |
| 工作层数 | **2 层板**（仅 TopLayer + BottomLayer 铜箔） |
| 元器件总数 | 82 个（不含 PCB 焊盘） |
| 生成工具 | EasyEDA Pro v3.2.149 |

---

## 二、电源架构

```
Micro-USB (5V)
    │
    ├── F1（保险丝）── D1（防反接二极管）── NET: BAT（电池/外部电源总线）
    │
    ├── U1（AMS1085CM-5 LDO）──────────────── NET: +5V（5V 稳压输出）
    │      输入: BAT    输出: +5V    最大 3A
    │
    └── U3（AMS1085CM-3.3 LDO）────────────── NET: VCC（3.3V 稳压输出）
           输入: BAT    输出: VCC    最大 3A
```

### 2.1 三路电源网络

| 网络名 | 电压 | 来源 | 用途 |
|--------|------|------|------|
| **USB** | 5V（直通） | Micro-USB VBUS → F1 | 仅 USB 输入端 |
| **BAT** | 5V（经保险丝+二极管） | USB → F1 → D1 | 电机驱动电源、LDO 输入 |
| **+5V** | 5V（稳压） | U1 输出 | 5V 排针输出 |
| **VCC** | 3.3V（稳压） | U3 输出 | ESP32 芯片、全部外设、3.3V 排针 |
| **VDD_SDIO** | 3.3V | VCC（经滤波） | Flash SPI 接口电源 |
| **GND** | 0V | 公共地 | 全部电路地 |

### 2.2 关键电源电容（Bypass/Decoupling）

- U1（5V LDO）输入：C1（BAT→GND）；输出：C2（+5V→GND）
- U3（3.3V LDO）输入：C4（BAT→GND）、C5（BAT→GND）；输出：C6（VCC→GND）、C7（VCC→GND）
- ESP32 VCC 引脚就近：C3（VCC→GND）、C8（VCC→GND）、C9（VCC→GND）、C28（VCC→GND）、C30（VCC→GND）
- ESP32 VDD_SDIO：C26（VDD_SDIO→GND）
- MPU6050 就近：C23（VCC→GND）、C27（VCC→GND）
- CP2102N 就近：C25（VCC→GND）
- 全板共 30 颗 MLCC 电容

---

## 三、完整 GPIO 引脚映射

### 3.1 ESP32-D0WD 全部引脚及其板上用途

> **来源**：从 FlyingProbeTesting.json 网表中的 U4 引脚（ESP32-D0WD 48 脚 QFN 封装）逐一追踪 NET_NAME 得到。

| ESP32 GPIO | 网表名 | 板上用途 | 是否可用 | 关键约束 |
|:----------:|--------|----------|:-------:|----------|
| **GPIO0** | GPIO0 | BOOT 按键 (K1)、排针 J5_15/16 | ⚠️ 受限 | 低电平进入下载模式；板上经 K1 接 GND，外接 10kΩ 上拉 |
| **GPIO1** | U0TXD | UART0 TXD → CP2102N（经 R14 串联） | ⚠️ 受限 | 与 USB 串口共用，MicroPython REPL 默认占用 |
| **GPIO2** | GPIO2 | 排针 J7（3P: GPIO2 / VCC / GND） | ✅ 可用 | 启动时需浮空或低电平 |
| **GPIO3** | U0RXD | UART0 RXD ← CP2102N | ⚠️ 受限 | 与 USB 串口共用，REPL 占用 |
| **GPIO4** | GPIO4 | 排针 J8（3P: GPIO4 / VCC / GND） | ✅ 可用 | — |
| **GPIO5** | GPIO5 | 排针 J4_1/2 | ✅ 可用 | 启动 Strapping 引脚 |
| **GPIO12** | GPIO12 | 排针 J4_3/4 | ⚠️ 受限 | **MTDI**：启动时必须低电平（选择 3.3V Flash 电压），否则无法启动 |
| **GPIO13** | GPIO13 | 排针 J9（3P: GPIO13 / +5V / GND） | ✅ 可用 | — |
| **GPIO14** | GPIO14 | 排针 J4_5/6 | ✅ 可用 | — |
| **GPIO15** | GPIO15 | 排针 J10（3P: GPIO15 / +5V / GND） | ⚠️ 受限 | **MTDO**：启动时必须高电平（片内上拉），否则启动日志输出异常 |
| **GPIO16** | GPIO16 | **MPU6050 SDA**（I²C 数据线）、排针 J5_3/4 | ✅ 可用 | 外接 R7 = 10kΩ 上拉至 VCC |
| **GPIO17** | GPIO17 | **MPU6050 SCL**（I²C 时钟线）、排针 J5_1/2 | ✅ 可用 | 外接 R8 = 10kΩ 上拉至 VCC |
| **GPIO18** | GPIO18 | 排针 J4_7/8 | ✅ 可用 | — |
| **GPIO19** | GPIO19 | 排针 J4_9/10 | ✅ 可用 | — |
| **GPIO21** | GPIO21 | 排针 J4_11/12 | ✅ 可用 | — |
| **GPIO22** | GPIO22 | 排针 J4_13/14 | ✅ 可用 | — |
| **GPIO23** | GPIO23 | 排针 J4_15/16 | ✅ 可用 | — |
| **GPIO25** | GPIO25 | **蜂鸣器**（经 NPN 驱动）、排针 J5_5/6、跳线 J11 | ✅ 可用 | DAC_1 输出；板上接跳线 J11 可断开通向蜂鸣器 |
| **GPIO26** | GPIO26 | **MX620B 电机驱动 IN1** | ✅ 可用 | DAC_2 输出 |
| **GPIO27** | GPIO27 | **MX620B 电机驱动 IN2** | ✅ 可用 | — |
| **GPIO32** | GPIO32 | **LED2（绿色）**、排针 J5_13/14 | ✅ 可用 | **低电平点亮**；经 R10（限流）→ LED2 阳极接 VCC |
| **GPIO33** | GPIO33 | **LED3（红色）**、排针 J5_11/12 | ✅ 可用 | **低电平点亮**；经 R11（限流）→ LED3 阳极接 VCC |
| **GPIO34** | GPIO34 | **KEY2 按键**、排针 J5_9/10 | ✅ 可用 | **仅输入**（无输出能力）；外接 R9 = 1kΩ 上拉至 VCC；K3 按下→GND |
| **GPIO35** | GPIO35 | **KEY1 按键**、排针 J5_7/8 | ✅ 可用 | **仅输入**（无输出能力）；外接 R12 = 1kΩ 上拉至 VCC；K4 按下→GND |
| — | U4_28~U4_33 | **SPI Flash**（ZB25VQ32） | ❌ 占用 | ESP32 内部 SD_DATA0~3 / SD_CLK / SD_CMD（对应 GPIO6~11），不可做 GPIO |
| — | U4_44~U4_45 | **40MHz 晶振 X1** | ❌ 占用 | ESP32 XTAL 振荡器引脚，不可做 GPIO |

### 3.2 ESP32 GPIO 特殊功能对照（MicroPython 开发必读）

| 类别 | GPIO | 说明 |
|------|------|------|
| **仅输入** | GPIO34, GPIO35 | 无输出驱动能力，无内部上拉/下拉电阻 |
| **Strapping 引脚** | GPIO0, GPIO2, GPIO4, **GPIO5**, **GPIO12**, **GPIO15** | 启动时电平影响启动模式/Flash 电压/日志输出 |
| **ADC1** | GPIO32(ADC1_CH4), GPIO33(ADC1_CH5), GPIO34(ADC1_CH6), GPIO35(ADC1_CH7) | 12-bit SAR ADC，量程 0~3.3V |
| **ADC2** | GPIO4(ADC2_CH0), GPIO12(ADC2_CH5), GPIO13(ADC2_CH4), GPIO14(ADC2_CH6), GPIO15(ADC2_CH3), GPIO27(ADC2_CH7) | ADC2 与 Wi-Fi 共用，Wi-Fi 启用时不可用 |
| **DAC** | GPIO25(DAC1), GPIO26(DAC2) | 8-bit DAC，输出 0~3.3V |
| **电容触摸** | GPIO2, GPIO4, GPIO12~15, GPIO27, GPIO32~33 | 10 路触摸传感器 |
| **I²C（推荐）** | GPIO16(SDA), GPIO17(SCL) | 板上已连接 MPU6050 并外接上拉电阻 |
| **UART0（默认）** | GPIO1(TX), GPIO3(RX) | MicroPython REPL 默认占用，连接 CP2102N |
| **PWM** | 全部输出可用 GPIO | LEDC PWM 控制器，最高 40MHz |

### 3.3 GPIO 冲突矩阵（互斥使用注意事项）

| 若使用…… | 则不可同时…… | 原因 |
|-----------|--------------|------|
| Wi-Fi | 使用 ADC2（GPIO4/12~15/27） | ADC2 与 Wi-Fi 射频共用 |
| MicroPython REPL | 将 GPIO1/3 做普通 GPIO | REPL 占用 UART0 |
| 蜂鸣器 PWM | 断开 J11 跳线 | 否则蜂鸣器随 GPIO25 一起动作 |
| GPIO12 接外部上拉 | 正常启动 | MTDI 启动时必须低电平 |
| GPIO15 接外部下拉 | 正常启动 | MTDO 启动时必须高电平 |

---

## 四、外设驱动电路详解

### 4.1 蜂鸣器（MLT-5020 压电蜂鸣器）

```
VCC ──────┬── B1（蜂鸣器）正极
           │
     B1 负极 ── Q3 集电极（NPN 晶体管 MMSS8050）
                      │
    GPIO25 ── J11（跳线）── R13（基极限流电阻）── Q3 基极
                      │
                     GND ── Q3 发射极
```

- **驱动方式**：NPN 共射极开关
- **逻辑**：GPIO25 = **高电平** → Q3 导通 → 蜂鸣器发声
- **频率控制**：PWM（`machine.PWM`），改变占空比不能改变音量（压电蜂鸣器特性）
- **跳线 J11**：断开即可使 GPIO25 与蜂鸣器驱动电路隔离，用于 GPIO25 他用
- **J11 位置**：2P 排针，在板上蜂鸣器下方，默认插跳线帽连通

### 4.2 LED 指示灯

#### LED1（红色，电源指示灯）
```
VCC ── R3（限流电阻）── LED1（阳极 → 阴极）── GND
```
- **不可编程控制**，通电常亮
- 作用：确认 3.3V 供电正常

#### LED2（绿色，用户可编程）
```
VCC ── LED2（阳极 → 阴极）── R10（限流电阻）── GPIO32
```
- **低电平点亮**：`GPIO32.value(0)` → 亮，`GPIO32.value(1)` → 灭
- GPIO32 = 高阻态时 LED 灭（R10 无回路）

#### LED3（红色，用户可编程）
```
VCC ── LED3（阳极 → 阴极）── R11（限流电阻）── GPIO33
```
- **低电平点亮**，与 LED2 逻辑一致

### 4.3 按键

板上共 4 个 4 脚贴片轻触开关（Tactile Switch）：

#### BOOT 按键（K1）
- 连接：GPIO0 ← 短接至 → GND（按下时）
- 用途：启动时按住进入下载模式；运行时可用作普通输入
- **注意**：ESP32 内部有弱上拉，但建议在 MicroPython 中启用内部上拉：`Pin(0, Pin.IN, Pin.PULL_UP)`

#### RESET 按键（K2）
- 连接：**CHIP_PU（EN 脚）** ← 短接至 → GND
- **不可编程**，按下即复位 ESP32 芯片
- 对应 Q1 自动下载电路：RTS 信号经 Q1 也可拉低 CHIP_PU

#### KEY1 按键（K4）
```
VCC ── R12（1kΩ 上拉）── GPIO35 ── K4 ── GND
```
- **未按下**：GPIO35 = 高电平（3.3V）
- **按下**：GPIO35 = 低电平（0V）
- 读取方式：`Pin(35, Pin.IN).value()` → `0` = 按下, `1` = 释放
- GPIO35 为仅输入引脚，需依赖外部上拉 R12（板上已提供）

#### KEY2 按键（K3）
```
VCC ── R9（1kΩ 上拉）── GPIO34 ── K3 ── GND
```
- 电特性同 KEY1

### 4.4 MPU6050 六轴惯性测量单元

```
         MPU6050（U7）
    ┌──────────────────────┐
    │  VCC  ──── 3.3V      │
    │  GND  ──── GND       │
    │  SDA  ──── GPIO16 ───┤── R7(10kΩ) ── VCC（上拉）
    │  SCL  ──── GPIO17 ───┤── R8(10kΩ) ── VCC（上拉）
    │  AD0  ──── ?         │  （AD0 电平决定 I²C 地址）
    └──────────────────────┘
```

- **I²C 地址**：0x68（AD0 = GND）或 0x69（AD0 = VCC）
- **上拉电阻**：板上已有 R7（GPIO16→VCC）、R8（GPIO17→VCC），均为 10kΩ
- **MicroPython 初始化**：
  ```python
  from machine import I2C, Pin
  i2c = I2C(scl=Pin(17), sda=Pin(16), freq=400000)
  i2c.scan()  # 应返回 [104] 或 [105]
  ```
- **注意事项**：
  - 推荐 I²C 频率 ≤ 400kHz
  - 上电后需等待 ~100ms 稳定再初始化
  - MPU6050 工作在 3.3V，不可接 5V

### 4.5 MX620B 电机驱动

```
         MX620B（U6）H 桥电机驱动
    ┌──────────────────────────┐
    │  VCC  ──── 3.3V（逻辑）   │
    │  BAT  ──── 电机电源(5V)   │
    │  GND  ──── GND           │
    │  IN1  ──── GPIO26        │
    │  IN2  ──── GPIO27        │
    │  A1   ──── J6_2（电机A+） │
    │  A2   ──── J6_3（电机A-） │
    └──────────────────────────┘
```

- **J6 接口**（4P 排针，2.54mm 间距）：
  | 引脚 | 信号 | 说明 |
  |:---:|------|------|
  | 1 | GND | 电源地 |
  | 2 | A1 | 电机 A 相 1 |
  | 3 | A2 | 电机 A 相 2 |
  | 4 | BAT | 电机电源（5V） |
- **控制逻辑**：GPIO26/27 输入 PWM 控制电机转速和方向
- **通用开发**：可用于电机、振动反馈等扩展

### 4.6 USB 转串口（CP2102N）与自动下载电路

```
Micro-USB ── CP2102N(U2) ──┐
          D+ ── USB+        │ TXD(U2_25) ── R14 ── ESP32 GPIO1(U0TXD)
          D- ── USB-        │ RXD(U2_26) ──────── ESP32 GPIO3(U0RXD)
          VBUS ── F1 ── D1  │
                            │ DTR(U2_28) ── Q2 ── GPIO0（自动进入下载模式）
                            │ RTS(U2_24) ── Q1 ── CHIP_PU（自动复位）
                            └────────────────────────────────────────
```

- **自动下载**：esptool.py 等烧录工具通过 DTR/RTS 时序控制，无需手动按 BOOT+RESET
- **串口参数**：默认 115200-8-N-1
- **MicroPython REPL**：通过此 USB 串口与 ESP32 REPL 交互

### 4.7 ZB25VQ32 SPI Flash

- 容量：32Mbit = **4MB**
- 连接：ESP32 专用 SPI Flash 接口（SD_DATA0~3, SD_CLK, SD_CMD）
- 用户不可直接访问这些 GPIO（对应 GPIO6~11）
- 4MB 分区建议（MicroPython）：固件 ~1.5MB + 用户文件系统 ~2.5MB

---

## 五、排针 / 连接器引脚定义

### 5.1 J1 — VCC 排针（3.3V 电源）
8P 单排针，全部连通至 VCC（3.3V），间距 2.54mm。

| 引脚 | 信号 |
|:---:|------|
| 1-8 | VCC（3.3V） |

### 5.2 J2 — +5V 排针（5V 电源）
8P 单排针，全部连通至 +5V。

| 引脚 | 信号 |
|:---:|------|
| 1-8 | +5V |

### 5.3 J3 — GND 排针（地）
8P 单排针，全部连通至 GND。

| 引脚 | 信号 |
|:---:|------|
| 1-8 | GND |

### 5.4 J4 — GPIO 排针 A（16P 双排）
左侧 8×2 排针，间距 2.54mm。

| 引脚 | GPIO | 引脚 | GPIO |
|:---:|:----:|:---:|:----:|
| 1 | GPIO5 | 2 | GPIO5 |
| 3 | GPIO12 | 4 | GPIO12 |
| 5 | GPIO14 | 6 | GPIO14 |
| 7 | GPIO18 | 8 | GPIO18 |
| 9 | GPIO19 | 10 | GPIO19 |
| 11 | GPIO21 | 12 | GPIO21 |
| 13 | GPIO22 | 14 | GPIO22 |
| 15 | GPIO23 | 16 | GPIO23 |

### 5.5 J5 — GPIO 排针 B（16P 双排）
右侧 8×2 排针，间距 2.54mm。

| 引脚 | GPIO | 引脚 | GPIO |
|:---:|:----:|:---:|:----:|
| 1 | GPIO17 | 2 | GPIO17 |
| 3 | GPIO16 | 4 | GPIO16 |
| 5 | GPIO25 | 6 | GPIO25 |
| 7 | GPIO35 | 8 | GPIO35 |
| 9 | GPIO34 | 10 | GPIO34 |
| 11 | GPIO33 | 12 | GPIO33 |
| 13 | GPIO32 | 14 | GPIO32 |
| 15 | GPIO0 | 16 | GPIO0 |

### 5.6 J7 / J8 / J9 / J10 — 3P 功能排针
标准 3P 单排针，间距 2.54mm，排列：信号 / 电源 / GND。

| 连接器 | 引脚 1 | 引脚 2 | 引脚 3 |
|:---:|:---:|:---:|:---:|
| J7 | **GPIO2** | VCC (3.3V) | GND |
| J8 | **GPIO4** | VCC (3.3V) | GND |
| J9 | **GPIO13** | +5V | GND |
| J10 | **GPIO15** | +5V | GND |

### 5.7 J6 — 电机接口
4P 单排针，间距 2.54mm（或 3.81mm 大电流端子）。

| 引脚 | 信号 | 说明 |
|:---:|------|------|
| 1 | GND | 电源地 |
| 2 | A1 | MX620B 电机 A 相 1 |
| 3 | A2 | MX620B 电机 A 相 2 |
| 4 | BAT | 电机电源（5V） |

### 5.8 J11 — 蜂鸣器跳线
2P 单排针。

| 引脚 | 信号 |
|:---:|------|
| 1 | R13_2（蜂鸣器驱动电路输入） |
| 2 | GPIO25 |

- **连接时**：GPIO25 控制蜂鸣器
- **断开时**：GPIO25 与蜂鸣器隔离，可他用

---

## 六、板载开发资源汇总

### 6.1 必需资源

| 功能 | 硬件资源 | 软件接口 | 关键参数 |
|------|----------|----------|----------|
| 声音输出 | 蜂鸣器 MLT-5020 @ GPIO25 | `machine.PWM` | PWM 控制发声频率 |
| 按键输入 | KEY1 (K4) @ GPIO35 | `machine.Pin.IN` | 按下=0, 释放=1；外接 1kΩ 上拉 |
| 按键输入 | KEY2 (K3) @ GPIO34 | `machine.Pin.IN` | 按下=0, 释放=1；外接 1kΩ 上拉 |
| 视觉反馈 | LED2 (绿) @ GPIO32 | `machine.Pin.OUT` | `0`=亮, `1`=灭 |
| 视觉反馈 | LED3 (红) @ GPIO33 | `machine.Pin.OUT` | `0`=亮, `1`=灭 |

### 6.2 可选扩展资源

| 扩展功能 | 硬件资源 | 软件接口 | 备注 |
|----------|----------|----------|------|
| 体感控制 | MPU6050 @ I²C(GPIO16/17) | `machine.I2C` | I²C 地址 0x68/0x69 |
| 更多按键 | 排针 GPIO12~15, 18~23 | `machine.Pin.IN` | 需外接按键 + 上拉电阻 |
| 振动反馈 | MX620B @ GPIO26/27 | `machine.PWM` | 连接 J6 直流电机 |
| 更多 LED | 排针任意 GPIO | `machine.Pin.OUT` | 需外接 LED + 限流电阻 |

### 6.3 不使用的资源

| 资源 | 不使用的理由 |
|------|-------------|
| BOOT (GPIO0) | 用于程序下载，不宜用作常规输入 |
| RESET (CHIP_PU) | 不可编程的硬件复位 |
| MX620B (GPIO26/27) | 电机驱动，可选扩展 |
| J4/J5 其他 GPIO | 当前无外接设备，可用于通用扩展 |
| UART0 (GPIO1/3) | 被 MicroPython REPL 占用 |

---

## 七、编程须知与约束

### 7.1 MicroPython 启动顺序
1. 上电 → `boot.py`（如存在）→ `main.py`（如存在）→ REPL
2. 建议在 `main.py` 中添加 `time.sleep(0.5)` 等待外设初始化
3. 按键/外设初始化应在 `main.py` 开头完成

### 7.2 蜂鸣器 PWM 音阶频率表（A4=440Hz 十二平均律）

| 音名 | 频率 (Hz) | PWM 设置 |
|------|----------|----------|
| C4 (do) | 262 | `pwm.freq(262)` |
| D4 (re) | 294 | `pwm.freq(294)` |
| E4 (mi) | 330 | `pwm.freq(330)` |
| F4 (fa) | 349 | `pwm.freq(349)` |
| G4 (sol) | 392 | `pwm.freq(392)` |
| A4 (la) | 440 | `pwm.freq(440)` |
| B4 (si) | 494 | `pwm.freq(494)` |

> **注意**：占空比设置 `pwm.duty(512)`（50% @ 10-bit）可获得最大音量。压电蜂鸣器对占空比变化不敏感。

### 7.3 GPIO 初始化代码模板

```python
from machine import Pin, PWM

# LED
led_green = Pin(32, Pin.OUT, value=1)   # 初始灭
led_red   = Pin(33, Pin.OUT, value=1)   # 初始灭

# 按键（GPIO34/35 仅输入，不设 PULL_UP）
key1 = Pin(35, Pin.IN)  # 板上已有 R12 上拉
key2 = Pin(34, Pin.IN)  # 板上已有 R9 上拉

# 蜂鸣器
buzzer = PWM(Pin(25), freq=440, duty=0)  # 初始静音

# 发声
buzzer.duty(512)   # 50% 占空比，蜂鸣器开始发声
buzzer.duty(0)     # 静音
buzzer.deinit()    # 释放 PWM 资源
```

### 7.4 注意事项

1. **GPIO34/35 仅输入** — 不可配置为 `Pin.OUT`，否则运行时报错
2. **GPIO12 启动约束** — 若通过排针外接上拉电阻到 VCC，ESP32 将无法正常启动（Flash 电压检测失败）
3. **GPIO15 启动约束** — 若通过排针外接下拉到 GND，启动日志将静默（不影响正常运行）
4. **GPIO0 启动约束** — 若程序中将 GPIO0 设为输出低电平并复位，将进入下载模式而非运行用户程序
5. **ADC2 与 Wi-Fi 冲突** — 若使用 Wi-Fi（如 WebREPL），勿使用 ADC2 通道的 GPIO
6. **蜂鸣器跳线 J11** — 使用 GPIO25 做非蜂鸣器用途前，务必拔掉 J11 跳线帽
7. **共地原则** — 通过排针外接设备时，必须共用 GND

---

## 八、ESP32 芯片级功能与本板兼容性分析

> 本章将 ESP32 芯片级外设功能与本板 GPIO 实际分配对照，给出可直接落地的 MicroPython 代码模板。**这是通用 ESP32 开发的核心参考章节。**

### 8.1 Deep Sleep 与 RTC 唤醒

ESP32 深度睡眠（Deep Sleep）功耗约 **5µA~10µA**（RTC 定时器 + 外部唤醒），本板可用的唤醒源如下：

| 唤醒源 | 本板可用 GPIO | 板上冲突 | 说明 |
|--------|:---------:|----------|------|
| **EXT0** | GPIO0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33 | GPIO0 被 BOOT 键占用 | 单一引脚电平唤醒，仅 RTC GPIO 支持 |
| **EXT1** | 同上 | — | 多引脚组合逻辑唤醒（任意高/任意低） |
| **Timer** | 不需要 GPIO | — | ULPT 协处理器定时唤醒，最通用 |
| **Touch** | GPIO2, 4, 12, 13, 14, 15, 27, 32, 33 | GPIO2=J7, GPIO4=J8, GPIO12/15=Strapping | 需外接触摸焊盘，本板无板载焊盘 |

> **RTC GPIO 编号**（与数字 GPIO 相同）：0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33, 34, 35, 36, 37, 38, 39
>
> 本板可用的 RTC GPIO：0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33, 34, 35。GPIO36~39 在 ESP32 上也是 RTC/ADC 输入引脚，但本板网表未见引出。

```python
import machine, esp32

# 使用 KEY1 (GPIO35) 做 EXT0 唤醒 — GPIO35 是 RTC GPIO ！
# 按下 KEY1 → GPIO35 = 低电平 → 唤醒
esp32.wake_on_ext0(pin=machine.Pin(35), level=esp32.WAKEUP_ALL_LOW)

# 定时器唤醒 10 秒
machine.deepsleep(10000)

# 唤醒后检查复位原因
if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    print("从深度睡眠唤醒")
```

> **特别注意**：唤醒后 RAM 全部丢失，程序从头执行（类似复位），需在 `boot.py` / `main.py` 开头检查复位原因来区分冷启动 vs 唤醒。

### 8.2 电容触摸 (TouchPad)

ESP32 内置 **10 路电容触摸传感器**，无需外接任何元件即可检测人体接近/触摸。适合制作触摸按键、接近感应、液位检测等。

| 触摸通道 | GPIO | 本板状态 | 备注 |
|:---:|:---:|:---:|------|
| T0 | **GPIO4** | ✅ 可用（排针 J8） | — |
| T1 | **GPIO0** | ⚠️ BOOT 键占用 | Strapping 引脚 |
| T2 | **GPIO2** | ✅ 可用（排针 J7） | — |
| T3 | **GPIO15** | ⚠️ Strapping 引脚 | MTDO，启动时必须高 |
| T4 | **GPIO13** | ✅ 可用（排针 J9） | — |
| T5 | **GPIO12** | ⚠️ Strapping 引脚 | MTDI，启动时必须低 |
| T6 | **GPIO14** | ✅ 可用（排针 J4） | — |
| T7 | **GPIO27** | ✅ 可用 | 与电机驱动共用 |
| T8 | **GPIO33** | ✅ 可用 | LED3 共用 |
| T9 | **GPIO32** | ✅ 可用 | LED2 共用 |

> **最佳触摸开发 GPIO**（无 Strapping 冲突且板上负载轻）：GPIO4(T0), GPIO13(T4), GPIO14(T6), GPIO27(T7)

```python
from machine import TouchPad, Pin

touch = TouchPad(Pin(4))        # 使用 GPIO4 / T0
threshold = touch.read() - 50   # 获取基线值，低于基线 50 视为触摸

while True:
    val = touch.read()
    if val < threshold:
        print("触摸检测！", val)
```

- 触摸读数范围约 0~1024（未触摸值约 800~1000，触摸后降到 200~500）
- 不同 PCB 布局导致基线不同，需**单独校准**
- 触摸焊盘建议尺寸 ≥ 10×10mm 铜箔，走线长度 < 30cm
- **本板无板载触摸焊盘**，需通过排针外接

### 8.3 SPI 总线映射

ESP32 有 4 个 SPI 控制器：SPI0（Flash 专用，不可用）、SPI1（Flash 专用）、**HSPI**（SPI2）、**VSPI**（SPI3）。

#### HSPI（SPI2）— 推荐用于高速 SPI 外设

| 信号 | 默认 GPIO | 本板状态 | 替代 GPIO |
|------|:---:|:---:|:---:|
| MISO | **GPIO12** | ⚠️ Strapping (MTDI) | GPIO19 |
| MOSI | **GPIO13** | ✅ 排针 J9 | GPIO23 |
| SCLK | **GPIO14** | ✅ 排针 J4 | GPIO18 |
| CS0 | **GPIO15** | ⚠️ Strapping (MTDO) | GPIO5 |

> **推荐本板 HSPI 引脚组合**（避开 Strapping）：
> `SCLK=14, MISO=19, MOSI=23, CS=5`

#### VSPI（SPI3）— 推荐用于 SPI Flash / SD 卡 / TFT 屏

| 信号 | 默认 GPIO | 本板状态 | 替代 GPIO |
|------|:---:|:---:|:---:|
| MISO | **GPIO19** | ✅ 排针 J4 | — |
| MOSI | **GPIO23** | ✅ 排针 J4 | — |
| SCLK | **GPIO18** | ✅ 排针 J4 | — |
| CS0 | **GPIO5** | ✅ 排针 J4 | — |

> **本板 VSPI 完美**：全部 GPIO（18, 19, 23, 5）均在 J4 排针上引出，无 Strapping 冲突，是连接 TFT 屏 / SD 卡模块的首选。

```python
from machine import SPI, Pin

# VSPI 初始化（本板推荐配置）
spi = SPI(2, baudrate=10000000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(23), miso=Pin(19))
cs = Pin(5, Pin.OUT, value=1)
```

### 8.4 I²C 总线映射

ESP32 有两路硬件 I²C 控制器：

| I²C 控制器 | 默认 SDA | 默认 SCL | 本板状态 | 可替代引脚 |
|:---:|:---:|:---:|:---|:---|
| **I²C0** | GPIO21 | GPIO22 | ✅ 均在排针 J4 | 任意 GPIO（软件 I²C） |
| **I²C1** | GPIO18 | GPIO19 | ✅ 均在排针 J4 | 任意 GPIO |

本板**板上已连接**的 I²C 外设：

| 外设 | SDA | SCL | 上拉电阻 | I²C 地址 |
|------|:---:|:---:|:---:|:---:|
| **MPU6050** (U7) | GPIO16 | GPIO17 | 板上 R7/R8 (10kΩ) | 0x68 / 0x69 |

> **如果同时使用 MPU6050 + 外部 I²C 设备**：将 MPU6050 挂在 I²C0（GPIO16/17），外部设备挂在 I²C1（GPIO21/22 或其他任意 GPIO）。

```python
from machine import I2C, Pin, SoftI2C

# 硬件 I²C — MPU6050
i2c_mpu = I2C(0, scl=Pin(17), sda=Pin(16), freq=400000)

# 软件 I²C — 外部传感器（可任意 GPIO，最多支持到 100kHz）
i2c_ext = SoftI2C(scl=Pin(22), sda=Pin(21), freq=100000)
```

### 8.5 UART 外设映射

ESP32 有 3 个硬件 UART：

| UART | 默认 TX | 默认 RX | 默认 RTS | 默认 CTS | 本板状态 |
|:---:|:---:|:---:|:---:|:---:|:---|
| **UART0** | GPIO1 | GPIO3 | GPIO22 | GPIO19 | ⚠️ 被 CP2102N + REPL 占用 |
| **UART1** | GPIO10 | GPIO9 | GPIO11 | GPIO6 | ❌ GPIO6~11 被 Flash 占用 |
| **UART2** | GPIO17 | GPIO16 | GPIO7 | GPIO8 | ⚠️ TX/RX 被 MPU6050 I²C 占用 |

> **UART1 不可用**（默认引脚全部在 Flash 上）。**UART2** 默认引脚被 MPU6050 占用，但可重映射到排针引脚。

**UART2 重映射方案**（本板可用）：
| 信号 | 推荐 GPIO | 排针 |
|------|:---:|:---:|
| TX | **GPIO5** | J4_1/2 |
| RX | **GPIO18** | J4_7/8 |

```python
from machine import UART

# UART2 重映射到排针可用引脚
uart2 = UART(2, baudrate=115200, tx=5, rx=18)
```

**使用 GPIO1/3 做 UART 外设的注意事项**：如果不用 REPL（如使用 WebREPL 或运行纯自动化程序），可以释放 GPIO1/3：
```python
import uos
uos.dupterm(None, 1)  # 断开 REPL 与 UART0 的绑定
# 此后 GPIO1(TX)/GPIO3(RX) 可自由使用
```

### 8.6 ADC 模拟输入 — 衰减与量程

ESP32 集成了 2 个 12-bit SAR ADC，共 18 个通道（本板可用 10 个）。ADC 的**衰减器（Attenuator）**决定输入电压量程。

#### 衰减配置与电压量程

| 衰减常数 | 满量程电压 | ADC 分辨率 | 本板实测建议 |
|:---|:---:|:---:|:---|
| `ADC.ATTN_0DB` | 0 ~ 1.1V | ~0.27mV/step | 默认，线性度最好 |
| `ADC.ATTN_2_5DB` | 0 ~ 1.5V | ~0.37mV/step | — |
| `ADC.ATTN_6DB` | 0 ~ 2.2V | ~0.54mV/step | — |
| `ADC.ATTN_11DB` | 0 ~ 3.6V（限 3.3V） | ~0.88mV/step | 通用外部传感器 |

> **注意**：ESP32 ADC 在 0~0.2V 和 3.1~3.3V 区间存在**非线性**，实际可用线性区域约 0.2V~3.1V（ATTN_11DB 时）。建议软件校准。

#### 本板全部 ADC 通道清单

| ADC 通道 | GPIO | 本板板上连接 | Wi-Fi 下可用？ |
|:---|:---:|:---|:---:|
| ADC1_CH0 | GPIO36 | 本板未见引出 | ✅（若可接触焊盘） |
| ADC1_CH3 | GPIO39 | 本板未见引出 | ✅（若可接触焊盘） |
| ADC1_CH4 | **GPIO32** | LED2 | ✅ |
| ADC1_CH5 | **GPIO33** | LED3 | ✅ |
| ADC1_CH6 | **GPIO34** | KEY2 (K3) | ✅ |
| ADC1_CH7 | **GPIO35** | KEY1 (K4) | ✅ |
| ADC2_CH0 | **GPIO4** | 排针 J8 | ❌（冲突） |
| ADC2_CH3 | **GPIO15** | 排针 J10 | ❌（冲突） |
| ADC2_CH4 | **GPIO13** | 排针 J9 | ❌（冲突） |
| ADC2_CH5 | **GPIO12** | 排针 J4 | ❌（冲突） |
| ADC2_CH6 | **GPIO14** | 排针 J4 | ❌（冲突） |
| ADC2_CH7 | **GPIO27** | MX620B 电机 | ❌（冲突） |

> **结论**：若需要 Wi-Fi + ADC 同时工作，**只能用 ADC1 通道**：GPIO32/33/34/35。其中 GPIO34/35 仅输入（对 ADC 无影响，ADC 本身是输入）。

```python
from machine import ADC, Pin

adc = ADC(Pin(34))              # KEY2 排针复用做 ADC 输入
adc.atten(ADC.ATTN_11DB)        # 0~3.3V 量程
adc.width(ADC.WIDTH_12BIT)      # 12-bit: 0~4095

voltage = adc.read() * 3.3 / 4095   # 转换为电压值
```

### 8.7 DAC 数模转换

ESP32 内置 2 路 **8-bit DAC**：

| DAC 通道 | GPIO | 本板板上连接 | 输出范围 |
|:---:|:---:|:---|:---:|
| **DAC1** | GPIO25 | 蜂鸣器（经跳线 J11） | 0~3.3V |
| **DAC2** | GPIO26 | MX620B IN1 | 0~3.3V |

> **使用 DAC 前**：GPIO25 需先**拔掉 J11 跳线帽**以隔离蜂鸣器；GPIO26 无额外隔离需求（电机驱动为高阻输入）。

```python
from machine import DAC, Pin

dac = DAC(Pin(25))   # GPIO25
dac.write(128)       # 50% 输出 ≈ 1.65V (0~255)
dac.write(0)         # 0V
```

### 8.8 LEDC PWM 详解

ESP32 的 PWM 通过 **LEDC（LED PWM Controller）** 实现，不占用 CPU，可产生高精度 PWM 波形。

| 参数 | 规格 |
|------|------|
| 高速通道数 | 8 路（LEDC_HS_CH0 ~ CH7） |
| 低速通道数 | 8 路（LEDC_LS_CH0 ~ CH7） |
| 频率范围 | **1Hz ~ 40MHz**（依赖分辨率） |
| 占空比分辨率 | 1-bit ~ 20-bit（频率越高分辨率越低） |
| 支持 GPIO | **全部输出可用 GPIO** |

**频率与分辨率对应关系**（以 80MHz 时钟为例）：

| 目标频率 | 最大分辨率 | 典型用途 |
|:---|:---:|------|
| 50Hz | 20-bit | 舵机控制 |
| 1kHz | 16-bit | LED 呼吸灯 |
| 5kHz | 14-bit | 直流电机 |
| 25kHz | 12-bit | 无刷电机/ESC |
| 262Hz~4kHz | 16-bit | **蜂鸣器音阶** |
| 800kHz | 7-bit | WS2812 需要 RMT 而非 LEDC |

> **本板 PWM 关键约束**：GPIO25（蜂鸣器）和 GPIO26/27（电机驱动）已有板上电路绑定，其他 GPIO 通过排针引出，使用灵活。

```python
from machine import PWM, Pin

# 蜂鸣器（GPIO25，已接 Q3 驱动电路）
buzzer = PWM(Pin(25), freq=440, duty=0)

# 舵机（通过 J7/J8/J9/J10 等排针外接）
servo = PWM(Pin(2), freq=50)      # 50Hz 标准舵机
servo.duty(77)                     # ~1.5ms 中位 (10-bit: 0~1023)

# LED 呼吸灯（GPIO32 — 板上绿色 LED，低电平有效）
led = PWM(Pin(32), freq=1000, duty=1023)  # 全占空比 = 常灭（低电平有效，反转逻辑）
```

### 8.9 RMT 红外/WS2812

ESP32 的 RMT（Remote Control）外设专为红外遥控设计，但也可驱动 **WS2812/NeoPixel** 可寻址 LED 灯带。

| 参数 | 规格 |
|------|------|
| 通道数 | 8 路 |
| 支持 GPIO | **全部输出可用 GPIO** |
| 内存 | 每通道 64×32-bit RAM |

> **本板推荐 WS2812 引脚**：J4/J5 排针任意可用 GPIO（如 GPIO5, GPIO13, GPIO14, GPIO18, GPIO21 等），注意避开 Strapping 引脚。

```python
import machine, neopixel

np = neopixel.NeoPixel(machine.Pin(21), 8)  # GPIO21, 8 颗灯珠
np[0] = (255, 0, 0)      # 第一颗红色
np.write()
```

### 8.10 GPIO 驱动能力与电气约束

| 参数 | 规格 | 说明 |
|------|:---:|------|
| **单引脚最大吸入电流** | 28mA | `Pin.OUT, value=0` 时，流入 GPIO 的电流 |
| **单引脚最大输出电流** | 40mA | `Pin.OUT, value=1` 时，流出 GPIO 的电流 |
| **推荐连续电流** | **12mA/引脚** | 长期运行安全值 |
| **全部 GPIO 总电流** | 需查 ESP32 数据手册 | 建议按单引脚推荐电流和电源域限制保守设计 |
| **VDD3P3_RTC 域** | 40mA | GPIO0,2,4,12~15,25~27,32~39 所在电源域 |
| **VDD3P3_CPU 域** | 40mA | GPIO1,3,5,16~23 所在电源域 |
| **高电平电压 (VOH)** | ≥ 2.64V | @ 12mA 负载, VCC=3.3V |
| **低电平电压 (VOL)** | ≤ 0.66V | @ 12mA 负载 |

> **重要**：本板 LED2/3 为共阳接法（VCC→LED→R→GPIO），点亮时 GPIO 吸入电流约 `(3.3V - Vf_LED) / R10`。板上限流电阻 R10/R11 已将 LED 电流限制在安全范围内，**无需额外限流**。

### 8.11 Wi-Fi / Bluetooth 与 GPIO 的互斥关系

这是 ESP32 开发中最易出错的环节：

| 无线功能 | 禁用资源 | 本板影响 |
|----------|----------|----------|
| **Wi-Fi 启用** | ADC2 全部通道不可用 | GPIO4,12,13,14,15,27 的 ADC 读数为随机值 |
| **Wi-Fi 启用** | DAC2 (GPIO26) 受干扰 | 仅轻度噪声，可用但精度下降 |
| **Bluetooth 启用** | 与 Wi-Fi 相同资源域 | 影响范围同 Wi-Fi |

> **板上外设 Wi-Fi 兼容性判断**：
> - LED2 (GPIO32)：✅ ADC1，Wi-Fi 下 ADC 正常
> - LED3 (GPIO33)：✅ ADC1，Wi-Fi 下 ADC 正常
> - KEY1 (GPIO35)：✅ ADC1，Wi-Fi 下 ADC 正常
> - KEY2 (GPIO34)：✅ ADC1，Wi-Fi 下 ADC 正常
> - 蜂鸣器 (GPIO25)：✅ 无影响
> - MPU6050 (GPIO16/17)：✅ I²C 不受影响
> - 电机驱动 (GPIO26/27)：✅ 数字输出无影响（模拟/ADC 受影响）

### 8.12 可用中断引脚

ESP32 **全部 GPIO** 均支持外部中断（`Pin.IRQ`），无引脚限制。以下为本板的实际使用建议：

```python
from machine import Pin

# KEY1 下降沿中断（按键按下时 GPIO35: 高→低）
key1 = Pin(35, Pin.IN)
key1.irq(trigger=Pin.IRQ_FALLING, handler=lambda p: print("KEY1 按下"))

# KEY2 上升沿中断（按键释放时）
key2 = Pin(34, Pin.IN)
key2.irq(trigger=Pin.IRQ_RISING, handler=lambda p: print("KEY2 释放"))
```

> **中断回调约束**：ISR 中不可分配内存（如创建对象、字典操作），只能设置全局标志位，在主循环中处理。

### 8.13 SD/SDIO/MMC 接口

| 模式 | 占用 GPIO | 本板可用性 |
|------|:---|:---|
| **SDIO** (4-bit) | GPIO6~11 + GPIO4/12/13 | ❌ GPIO6~11 被 Flash 占用 |
| **SPI SD** (1-bit) | CS/SCLK/MOSI/MISO | ✅ 用 VSPI (GPIO5/18/23/19) 接 Micro SD 卡模块 |

> 本板只能用 **SPI 模式** 接 SD 卡，推荐使用 VSPI 默认引脚组（见 §8.3）。

```python
from machine import SPI, Pin, SDCard
import uos

spi = SPI(2, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
sd = SDCard(spi, cs=Pin(5))
uos.mount(sd, '/sd')
```

---

## 九、附录

### 9.1 板级元器件完整列表

| 位号 | 器件型号 | 功能 |
|------|----------|------|
| U4 | ESP32-D0WD-V3 | 主控 MCU |
| U5 | ZB25VQ32 | 4MB SPI Flash |
| U2 | CP2102N | USB-UART 桥接 |
| U1 | AMS1085CM-5 | 5V LDO 稳压器 |
| U3 | AMS1085CM-3.3 | 3.3V LDO 稳压器 |
| U6 | MX620B | H 桥电机驱动 |
| U7 | MPU6050 | 六轴 IMU |
| USB1 | Micro-USB | USB 接口 |
| X1 | 40MHz | 晶振 |
| B1 | MLT-5020 | 压电蜂鸣器 |
| Q1, Q2 | MMSS8050 | NPN 晶体管（自动下载） |
| Q3 | MMSS8050 | NPN 晶体管（蜂鸣器驱动） |
| LED1 | 红色 LED | 电源指示 |
| LED2 | 绿色 LED | 用户可编程 |
| LED3 | 红色 LED | 用户可编程 |
| D1 | 1N4007/M7 | 防反接二极管 |
| F1 | 500mA | 自恢复保险丝 |
| K1 | 轻触开关 | BOOT 按键 |
| K2 | 轻触开关 | RESET 按键 |
| K3 | 轻触开关 | KEY2 |
| K4 | 轻触开关 | KEY1 |
| EANT1 | PCB 天线 | Wi-Fi/Bluetooth |
| R1~R14 | 贴片电阻 | 限流/上拉/分压 |
| C1~C30 | MLCC 贴片电容 | 滤波/去耦 |
| L1 | 贴片电感 | RF 匹配网络 |

### 9.2 板层结构

| 层 | Gerber 文件 | 内容 |
|:--:|------------|------|
| 1 | Gerber_TopLayer.GTL | 顶层铜箔（信号走线 + 元件焊盘） |
| 2 | Gerber_BottomLayer.GBL | 底层铜箔（信号走线 + GND 覆铜） |
| 3 | Gerber_TopSilkscreenLayer.GTO | 顶层丝印（元件轮廓 + 位号） |
| 4 | Gerber_BottomSilkscreenLayer.GBO | 底层丝印 |
| 5 | Gerber_TopSolderMaskLayer.GTS | 顶层阻焊层开窗 |
| 6 | Gerber_BottomSolderMaskLayer.GBS | 底层阻焊层开窗 |
| 7 | Gerber_TopPasteMaskLayer.GTP | 顶层钢网层 |
| 8 | Gerber_BoardOutlineLayer.GKO | 板框（90×40mm, R≈2.5 圆角） |

### 9.3 参考文档链接

- ESP32 技术参考手册：https://www.espressif.com/sites/default/files/documentation/esp32_technical_reference_manual_cn.pdf
- MicroPython ESP32 快速参考：https://docs.micropython.org/en/latest/esp32/quickref.html
- MPU6050 数据手册：https://invensense.tdk.com/products/motion-tracking/6-axis/mpu-6050/
- Model Context Protocol (MCP) 规范：https://modelcontextprotocol.io

---

> **文档版本**：v2.0  
> **生成日期**：2026-07-06  
> **数据来源**：FlyingProbeTesting.json（飞针网表） + Gerber 274X 板框层 + Excellon 钻孔文件  
> **验证状态**：已按更新后的脚本证据修正关键板框、电源、阻值与连接器信息；仍建议以 `scan_gerber_folder.py --evidence-only` 输出作为后续维护依据  
> **更新记录**：v1.0 完成板级硬件映射；v2.0 新增芯片级功能完整分析（§八：Deep Sleep / Touch / SPI / I²C / UART / ADC / DAC / PWM / RMT / 中断 / SD）
