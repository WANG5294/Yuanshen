---
name: esp32-board-hardware-foundations
description: ESP32-D0WD-V3 开发板的板级基础资料，涵盖尺寸、主控、晶振、Flash、USB、电源架构、元器件清单、PCB 板层和溯源。查询开发板规格、供电能力、器件型号、板框或硬件资料来源时加载。
---

# 开发板硬件基础

以项目根目录 `修正ESP32_D0WD_硬件开发手册.md` 的网表、Gerber 和钻孔分析作为
**板级连接来源**；芯片能力不要从网表推断，应加载相应功能 Skill 并以官方资料为准。

## 总览

| 属性 | 值 |
|---|---|
| 主控 | ESP32-D0WD-V3，双核 Xtensa LX6，最高 240MHz |
| 晶振 | X1，40MHz，连接 U4_44/U4_45 |
| Flash | ZB25VQ32，32Mbit（4MB） |
| USB-UART | CP2102N，Micro-USB |
| 板框 | 90.0mm × 40.0mm，四角约 R2.5mm |
| 铜层 | 2 层：TopLayer + BottomLayer |
| 元器件 | 82 个（不含 PCB 焊盘） |
| 设计工具 | EasyEDA Pro v3.2.149 |

## 电源架构

```text
Micro-USB VBUS
  └─ F1 500mA 自恢复保险丝 ─ D1(M7/1N4007) ─ BAT
       ├─ U1 AMS1085CM-5   ─ +5V
       ├─ U3 AMS1085CM-3.3 ─ VCC(3.3V)
       └─ MX620B 电机功率电源
```

| 网络 | 标称电压 | 用途 |
|---|---:|---|
| USB | 5V | VBUS 输入侧 |
| BAT | 约 5V，经过 F1/D1 | LDO 输入和电机电源 |
| +5V | 5V | J2 与 J9/J10 电源针 |
| VCC | 3.3V | ESP32、逻辑外设、J1 与 J7/J8 |
| VDD_SDIO | 3.3V | SPI Flash，经滤波 |
| GND | 0V | 公共地 |

AMS1085 的“3A”是器件等级，不是本板或 USB 可提供的电流；本板 USB 路径有
F1 500mA，且 LDO 压差、二极管压降与散热都会限制实际能力。外接设备必须共地，
不要把 5V 接入 3.3V 逻辑脚。

关键去耦：U1 为 C1/C2；U3 输入为 C4/C5、输出为 C6/C7；ESP32 邻近
C3/C8/C9/C28/C30；VDD_SDIO 为 C26；MPU6050 为 C23/C27；CP2102N 为 C25。
全板共 30 颗 MLCC。

## 主要元器件

| 位号 | 型号/器件 | 功能 |
|---|---|---|
| U4 | ESP32-D0WD-V3 | MCU |
| U5 | ZB25VQ32 | 4MB SPI Flash |
| U2 | CP2102N | USB-UART |
| U1/U3 | AMS1085CM-5 / -3.3 | 5V / 3.3V LDO |
| U6 | MX620B | H 桥电机驱动 |
| U7 | MPU6050 | 六轴 IMU |
| B1 | MLT-5020 | 压电蜂鸣器 |
| Q1/Q2 | MMSS8050 | 自动下载 |
| Q3 | MMSS8050 | 蜂鸣器驱动 |
| LED1/2/3 | 红/绿/红 LED | 电源、用户绿灯、用户红灯 |
| K1/K2/K3/K4 | 轻触开关 | BOOT、RESET、KEY2、KEY1 |
| EANT1 | PCB 天线 | Wi-Fi/Bluetooth |
| R1~R14 | 贴片电阻 | 限流、上拉、串联 |
| C1~C30 | MLCC | 滤波、去耦 |
| L1 | 贴片电感 | RF 匹配 |

## PCB 制造层

| Gerber | 内容 |
|---|---|
| `.GTL` / `.GBL` | 顶层 / 底层铜箔 |
| `.GTO` / `.GBO` | 顶层 / 底层丝印 |
| `.GTS` / `.GBS` | 顶层 / 底层阻焊 |
| `.GTP` | 顶层钢网 |
| `.GKO` | 90×40mm 板框 |

权威补充资料：ESP32 数据手册和技术参考手册、MicroPython ESP32 quick reference、
TDK/InvenSense MPU-6050 产品资料。具体 GPIO、电源针和接口分别加载
`esp32-gpio-capabilities` 与 `esp32-connectors-and-pin-headers`。
