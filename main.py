"""
ESP32 按键控制LED
KEY1(GPIO35) → 红色LED(GPIO33)
KEY2(GPIO34) → 绿色LED(GPIO32)
按住亮，释放灭，含消抖
"""

from machine import Pin
import time

# LED: 共阳，低电平点亮，初始高电平熄灭
red = Pin(33, Pin.OUT, value=1)
green = Pin(32, Pin.OUT, value=1)

# 按键: 仅输入，板载外部上拉，按下=0
key1 = Pin(35, Pin.IN)
key2 = Pin(34, Pin.IN)

def read_key(pin):
    """读取按键并消抖，返回稳定后的值"""
    v1 = pin.value()
    time.sleep_ms(20)
    v2 = pin.value()
    return v2 if v1 == v2 else v1  # 不一致则用第一次的值

while True:
    k1 = read_key(key1)
    k2 = read_key(key2)

    # 按下=0 → LED亮=0；释放=1 → LED灭=1
    red.value(k1)
    green.value(k2)

    time.sleep_ms(10)
