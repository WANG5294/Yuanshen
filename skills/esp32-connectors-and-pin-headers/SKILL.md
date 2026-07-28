---
name: esp32-connectors-and-pin-headers
description: ESP32-D0WD-V3 开发板 J1 至 J11 的物理连接权威表，涵盖电源排针、双排 GPIO、3P 功能排针、电机接口与蜂鸣器跳线。查询针号、接线位置、电压或跳线定义时加载；GPIO 能力另查 GPIO Skill。
---

# 连接器与排针

本表只描述网表确认的物理连接，不解释 GPIO 复用、Strapping 或电气能力；选脚前同时
加载 `esp32-gpio-capabilities`。

## 电源排针

| 连接器 | 结构 | 针脚 |
|---|---|---|
| J1 | 8P 单排，2.54mm | 1~8 全部为 VCC（3.3V） |
| J2 | 8P 单排，2.54mm | 1~8 全部为 +5V |
| J3 | 8P 单排，2.54mm | 1~8 全部为 GND |

VCC 和 +5V 不是同一网络。外接模块必须核对额定电压并与 J3 共地。

## J4 GPIO 排针 A

16P、8×2；同一行的两针连接同一 GPIO。

| 针 | 信号 | 针 | 信号 |
|:---:|---|:---:|---|
| 1 | GPIO5 | 2 | GPIO5 |
| 3 | GPIO12 | 4 | GPIO12 |
| 5 | GPIO14 | 6 | GPIO14 |
| 7 | GPIO18 | 8 | GPIO18 |
| 9 | GPIO19 | 10 | GPIO19 |
| 11 | GPIO21 | 12 | GPIO21 |
| 13 | GPIO22 | 14 | GPIO22 |
| 15 | GPIO23 | 16 | GPIO23 |

## J5 GPIO 排针 B

16P、8×2；同一行的两针连接同一 GPIO。

| 针 | 信号 | 针 | 信号 |
|:---:|---|:---:|---|
| 1 | GPIO17 | 2 | GPIO17 |
| 3 | GPIO16 | 4 | GPIO16 |
| 5 | GPIO25 | 6 | GPIO25 |
| 7 | GPIO35 | 8 | GPIO35 |
| 9 | GPIO34 | 10 | GPIO34 |
| 11 | GPIO33 | 12 | GPIO33 |
| 13 | GPIO32 | 14 | GPIO32 |
| 15 | GPIO0 | 16 | GPIO0 |

## 3P 功能排针

针序均为“信号 / 电源 / GND”：

| 连接器 | 1 | 2 | 3 |
|:---:|---|---|---|
| J7 | GPIO2 | VCC 3.3V | GND |
| J8 | GPIO4 | VCC 3.3V | GND |
| J9 | GPIO13 | +5V | GND |
| J10 | GPIO15 | +5V | GND |

注意 J9/J10 的电源针是 5V，但其 GPIO 信号仍是 3.3V 逻辑，不能互相短接。

## J6 电机接口

| 针 | 信号 | 定义 |
|:---:|---|---|
| 1 | GND | 功率地 |
| 2 | A1 | MX620B H 桥输出 1 |
| 3 | A2 | MX620B H 桥输出 2 |
| 4 | BAT | 电机电源总线，标称约 5V |

电机接 A1/A2，不要把电机跨接 GPIO 与 GND；BAT 是供电针而非控制信号。

## J11 蜂鸣器跳线

| 针 | 网络 |
|:---:|---|
| 1 | R13_2（Q3 蜂鸣器驱动输入侧） |
| 2 | GPIO25 |

插帽：GPIO25 控制蜂鸣器；拔帽：GPIO25 与蜂鸣器驱动隔离。J11 默认连通，位于蜂鸣器
附近。蜂鸣器电路和 GPIO25 的用法见 `esp32-led-key-buzzer`。
