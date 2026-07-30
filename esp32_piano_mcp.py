#!/usr/bin/env python3
"""esp32_piano_mcp.py - ESP32 数字钢琴调试 MCP 服务器（带麦克风闭环）

两种运行方式:
  1. MCP 服务器 (需 pip install mcp):
       claude mcp add esp32-piano -- python3 esp32_piano_mcp.py
  2. 命令行直接调用 (无需 mcp 包, 便于单独测试):
       python3 esp32_piano_mcp.py mic_check
       python3 esp32_piano_mcp.py record_audio out.wav 3
       python3 esp32_piano_mcp.py analyze_wav GUOJIGE2_preview.wav
       python3 esp32_piano_mcp.py play_and_record piano.play rec.wav 4
       python3 esp32_piano_mcp.py compare_audio rec.wav preview.wav

工具分两组:
  设备通道: list_ports / get_port / set_port / check_port /
            connect_device / disconnect_device（长连接管理）/ upload /
            run_script / repl_exec / device_ls / device_rm / soft_reset
  音频闭环: mic_check / record_audio / play_and_record /
            analyze_wav / compare_audio

设备通道默认长连接模式（ESP32_DEVICE_MODE=persistent，pyserial 持久
raw REPL 会话，MCP 进程常驻故跨工具调用存活；CLI 模式每次进程内自闭环）。
设 ESP32_DEVICE_MODE=mpremote 回退为每次调用起 mpremote 子进程。

依赖: mpremote, arecord(alsa-utils), numpy。分析全部用 numpy, 不需要 scipy。
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

# 串口：默认 auto 由 mpremote 自动探测；可用环境变量 ESP32_PORT 指定，
# 运行期由 Agent 主程序通过 set_port 工具热切换（对应 /port 命令）。
PORT = os.environ.get("ESP32_PORT", "auto").strip() or "auto"
# 设备通道模式：persistent（长连接，pyserial 持久 raw REPL 会话，默认）/
# mpremote（每次调用起 mpremote 子进程，回退路径）。
DEVICE_MODE = os.environ.get("ESP32_DEVICE_MODE", "persistent").strip() or "persistent"
MIC_DEVICE = "default"          # PipeWire; 录到全零请检查 VirtualBox 音频输入
SAMPLE_RATE = 44100


# =============================================================================
# 长连接会话：pyserial 持久 raw REPL（MCP 服务器进程常驻，会话跨工具调用存活）
# =============================================================================

class _ReplSession:
    """持有串口的 raw REPL 长连接。惰性连接、出错自动重连一次。

    协议要点（MicroPython raw REPL）：
      进入: Ctrl-C ×2 打断运行中的程序，Ctrl-A 进 raw 模式，
            设备回 'raw REPL; CTRL-B to exit\\r\\n>'；
      执行: 发送代码 + Ctrl-D，设备回 'OK' + stdout + \\x04 + stderr + \\x04 + '>'；
      复位: Ctrl-B 退出 raw 模式后 Ctrl-D 软复位。
    Windows 打开串口前置 DTR/RTS=False，避免 CP210x 打开毛刺；Linux 保持
    驱动默认值，避免 CH9102/CH340 被持续置于复位/下载状态。"""

    def __init__(self):
        self.ser = None
        self.port = None
        self._buf = b""                 # 读剩的响应缓存（设备常一次发完所有帧）

    # ---- 底层 ----

    def close(self):
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.write(b"\x02")      # Ctrl-B 退出 raw REPL
                    if os.name == "nt":
                        # 与 mpremote 的 Windows 关闭顺序一致，避免 CP210x
                        # 在关闭端口时因控制线先后顺序触发意外复位。
                        self.ser.rts = False
                        self.ser.dtr = False
                    self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.port = None
        self._buf = b""

    def _read_until(self, token: bytes, timeout: float) -> bytes:
        """读到 token 为止；token 之后的多余字节留在 _buf 供下次使用。"""
        deadline = time.monotonic() + timeout
        while True:
            if token in self._buf:
                i = self._buf.index(token) + len(token)
                out, self._buf = self._buf[:i], self._buf[i:]
                return out
            if time.monotonic() >= deadline:
                out, self._buf = self._buf, b""
                return out
            chunk = self.ser.read(256)
            if chunk:
                self._buf += chunk

    def _open(self):
        import serial
        global PORT
        if PORT == "auto":
            from serial.tools import list_ports as serial_list_ports
            candidates = [
                p.device for p in serial_list_ports.comports()
                if (os.name == "nt" and p.vid is not None)
                or p.device.startswith(("/dev/ttyACM", "/dev/ttyUSB"))
            ]
            if len(candidates) != 1:
                shown = ", ".join(candidates) or "无"
                raise RuntimeError(
                    "自动探测需要恰好一个 USB 串口，当前候选为："
                    f"{shown}。请用 set_port 明确选择。"
                )
            PORT = candidates[0]
        if self.ser is not None and self.port == PORT and self.ser.is_open:
            return
        self.close()
        try:
            s = serial.Serial()
            s.port = PORT
            s.baudrate = 115200
            s.timeout = 0.1
            # Linux 下 CH9102/CH340 的 DTR/RTS 极性与 CP210x 不同，预先
            # 强制 False 可能把 ESP32 持续按在复位/下载状态。仅保留原本
            # 针对 Windows 打开端口毛刺的规避。
            if os.name == "nt":
                s.dtr = False
                s.rts = False
            s.open()
            if os.name == "nt":
                # CP210x Windows 驱动首次打开时需先清零再按此顺序恢复，
                # 以避免 DTR/RTS 不同时生效形成复位脉冲。
                s.dtr = True
                s.rts = True
        except Exception as e:
            raise RuntimeError(_classify_mpremote_error(str(e))) from None
        self.ser, self.port = s, PORT
        try:
            s.reset_input_buffer()
            # 上一个客户端若异常退出，设备可能仍停在 raw REPL。先 Ctrl-B
            # 统一回 friendly REPL，再 Ctrl-C 打断 main.py；否则 raw 状态下
            # 再发 Ctrl-A 不会重新输出 banner，容易被误判为无应答。
            s.write(b"\r\x02\x03\x03")
            time.sleep(0.3)
            s.reset_input_buffer()
            s.write(b"\x01")             # Ctrl-A 进入 raw REPL
            banner = self._read_until(b"raw REPL; CTRL-B to exit", 3)
            if b"raw REPL" not in banner:
                raise RuntimeError(
                    f"已打开 {PORT} 但进不了 raw REPL（握手无应答）。"
                    "板上 MicroPython 可能未在运行，或该串口对面不是 ESP32；"
                    "Ctrl-C 可中断任何循环，不要归因于 main.py 死循环。")
            self._read_until(b">", 1)
        except Exception:
            self.close()
            raise

    def _ensure(self):
        """惰性连接。失败原样上报，避免一次调用暗中重复握手。"""
        self._open()

    # ---- 上层操作 ----

    def exec(self, code: str, timeout_s: float = 20) -> str:
        self._ensure()
        try:
            self.ser.reset_input_buffer()
            self._buf = b""
            self.ser.write(code.encode("utf-8") + b"\x04")
            out = self._read_until(b"\x04", timeout_s)
            if b"\x04" not in out:
                # 代码跑超时（如 while True）：Ctrl-C 打断，回收已有输出
                self.ser.write(b"\x03")
                time.sleep(0.2)
                rest = self._read_until(b"\x04", 2)
                self._read_until(b">", 1)
                body = (out + rest).replace(b"OK", b"", 1)
                return ("(执行超时, 已 Ctrl-C 打断)\n"
                        + body.replace(b"\x04", b"").decode(errors="replace").strip())
            if not out.startswith(b"OK"):
                raise RuntimeError(
                    "raw REPL 响应帧损坏或失步：缺少 OK 前缀；"
                    f"响应={out[:120]!r}。"
                )
            err = self._read_until(b"\x04", 2)
            prompt = self._read_until(b">", 1)
            if not err.endswith(b"\x04") or not prompt.endswith(b">"):
                raise RuntimeError(
                    "raw REPL 响应帧不完整（缺少 stderr EOT 或提示符）"
                )
            stdout = out[2:-1]
            stderr = err.replace(b"\x04", b"")
            text = stdout.decode(errors="replace").strip()
            if stderr.strip():
                detail = stderr.decode(errors="replace").strip()
                raise RuntimeError(
                    "ESP32 执行代码失败："
                    + (f"\nstdout:\n{text}" if text else "")
                    + f"\nstderr:\n{detail}"
                )
            return text or "OK"
        except RuntimeError:
            self.close()
            raise
        except Exception as e:            # 串口掉线等：重连一次再报错
            self.close()
            raise RuntimeError(
                f"长连接会话中断（{e}）。已释放串口，下次调用会自动重连；"
                "若反复失败，用 check_port 检查占用、list_ports 确认串口号。"
            ) from None

    def soft_reset_device(self) -> str:
        self._ensure()
        try:
            self.ser.write(b"\x02")       # Ctrl-B 回 friendly REPL
            time.sleep(0.1)
            self.ser.write(b"\x04")       # Ctrl-D 软复位
            time.sleep(1.2)               # 等固件启动 + main.py 自启
            self.ser.write(b"\r\x03\x03") # 打断自启的 main.py
            time.sleep(0.5)
            self.ser.reset_input_buffer() # 丢掉启动横幅等噪声
            self.ser.write(b"\x01")
            banner = self._read_until(b"raw REPL; CTRL-B to exit", 3)
            if b"raw REPL" not in banner:
                raise RuntimeError("软复位后进不了 raw REPL（握手无应答）")
            self._read_until(b">", 1)
            return "已软复位并重新进入 REPL"
        except Exception as e:
            self.close()
            raise RuntimeError(f"软复位失败（{e}），已释放串口，下次调用自动重连") from None

    def upload(self, local_path: str, remote_name: str = "") -> str:
        data = Path(local_path).read_bytes()
        dest = remote_name or Path(local_path).name
        temp = "." + dest + ".uploading"
        backup = "." + dest + ".backup"
        expected_sha256 = hashlib.sha256(data).hexdigest()
        self.exec(f"f=open({temp!r},'wb')")
        try:
            for i in range(0, len(data), 512):
                # MicroPython 的 bytes 没有 CPython 的 fromhex()。
                self.exec(
                    "f.write(__import__('ubinascii').unhexlify("
                    f"'{data[i:i+512].hex()}'))",
                          timeout_s=30)
        finally:
            self.exec("f.close()")
        verify_code = (
            "import os\n"
            f"_p={temp!r}\n"
            "_f=open(_p,'rb'); _n=0\n"
            "_h=__import__('uhashlib').sha256()\n"
            "while True:\n"
            " b=_f.read(512)\n"
            " if not b: break\n"
            " _n+=len(b); _h.update(b)\n"
            "_f.close()\n"
            "_hex=__import__('ubinascii').hexlify(_h.digest()).decode()\n"
            "print(str(_n)+' '+_hex)"
        )
        remote = self.exec(verify_code, timeout_s=30).strip().splitlines()[-1]
        expected = f"{len(data)} {expected_sha256}"
        if remote != expected:
            try:
                self.exec(f"import os; os.remove({temp!r})")
            except RuntimeError:
                pass
            raise RuntimeError(
                f"上传校验失败：设备返回 {remote!r}，期望 {expected!r}；"
                f"原 {dest} 未替换。"
            )
        commit_code = (
            "import os\n"
            f"_dst={dest!r}; _tmp={temp!r}; _bak={backup!r}\n"
            "try:\n os.remove(_bak)\n"
            "except OSError:\n pass\n"
            "_had=False\n"
            "try:\n os.rename(_dst,_bak); _had=True\n"
            "except OSError:\n pass\n"
            "try:\n os.rename(_tmp,_dst)\n"
            "except Exception:\n"
            " if _had: os.rename(_bak,_dst)\n"
            " raise\n"
            "if _had: os.remove(_bak)\n"
            "print('COMMIT_OK')"
        )
        committed = self.exec(commit_code, timeout_s=30)
        if committed.strip() != "COMMIT_OK":
            raise RuntimeError(f"设备未确认文件替换：{committed!r}")
        final_size = self.exec(
            f"import os; print(os.stat({dest!r})[6])", timeout_s=10
        ).strip()
        if final_size != str(len(data)):
            raise RuntimeError(
                f"替换后大小复核失败：设备为 {final_size!r}，期望 {len(data)}"
            )
        return (
            f"已原子上传并校验 {local_path} -> :{dest}"
            f"（{len(data)} 字节，SHA-256 {expected_sha256[:12]}…）"
        )


_SESSION = _ReplSession()


def connect_device() -> str:
    """建立到 ESP32 的长连接（raw REPL）。之后所有设备工具复用此连接，
    不再每次握手；会话期间本进程独占串口，其他程序请勿同时连接。"""
    _SESSION._ensure()
    return f"长连接已建立: {PORT}（raw REPL，后续设备操作零握手开销）"


def disconnect_device() -> str:
    """断开长连接并释放串口。任务结束或需要把串口让给其他工具时调用。"""
    _SESSION.close()
    return "长连接已断开，串口已释放"


# =============================================================================
# 设备通道
# =============================================================================

def _classify_mpremote_error(output: str) -> str:
    """把 mpremote 原始报错翻译成可操作的诊断建议（借鉴数科工具链的报错设计）。"""
    low = output.lower()
    if "in use by another program" in low or "failed to access" in low \
            or "permissionerror" in low or "access is denied" in low:
        return (f"串口 {PORT} 被其他程序独占（Windows 串口同一时刻只允许一个进程打开）。"
                "常见占用方：另一个 Kimi 会话的 esp32-mcp 长连接（connect_esp32 后未 "
                "disconnect_esp32）、串口监视器、Pymakr、另一个 Yuanshen 实例。"
                "请先断开对应连接/关闭相关程序再重试，不要原地反复重试同一命令。")
    if "could not open port" in low or "no such file" in low \
            or "cannot find" in low or "系统找不到" in low:
        return (f"串口 {PORT} 不存在或已掉线。请用 list_ports 重新枚举，"
                "确认 USB 线连接后通过 set_port（对应主程序 /port 命令）切换到正确串口。")
    if "no device found" in low:
        return (f"mpremote 找不到设备 {PORT}。在 Windows 上这通常意味着："
                "1) 串口正被其他程序独占（另一个 Kimi 会话的 esp32-mcp 长连接、"
                "串口监视器、Pymakr 等，先断开/关闭再试）；"
                "2) 板子掉线，请重新拔插 USB；"
                "3) 串口号变了，用 list_ports 重新枚举并 set_port 切换。"
                "不要原地反复重试同一命令。")
    return output


def _classify_silent_timeout(timeout_s: float) -> str:
    """subprocess 看门狗杀掉 mpremote（静默超时）的诊断。

    关键事实：此时 mpremote 已成功打开串口（否则几秒内就会带错误退出），
    死的是打开之后的 REPL 握手——它对着一条无应答的信道干等。这与板上
    main.py 是否死循环无关：mpremote 连接时发的 Ctrl-C 是字节码级中断，
    可以打断任何 while True（包括零让步循环）。正确归因方向：
    1) CH9102/CH340（WCH）驱动不强制独占——另一进程占着串口时，本进程
       仍能"打开成功"但拿到死信道，表现恰好是这种无报错的静默挂起。
       最大嫌疑人：esp32-mcp 长连接（connect_esp32 未 disconnect）、
       串口监视器、Thonny/Pymakr、未退出的 mpremote。
    2) 串口选错（如 auto 探测到蓝牙虚拟串口）：端口能开但对面不是 ESP32。
    处置：先用 check_port 探测占用情况，用 list_ports + set_port 固定正确
    串口；确认无占用后再重试。禁止把它诊断为"main.py 死循环锁死 REPL"
    并围绕复位/覆盖 main.py 空转。"""
    return (f"mpremote 静默超时（{timeout_s}s 无任何输出被看门狗终止）：串口已打开"
            "但 REPL 握手无应答。这不是 main.py 死循环（Ctrl-C 可中断任何循环）。"
            "最可能是另一进程占着串口（WCH CH9102/CH340 驱动不强制独占，二次打开"
            "会拿到无应答的死信道）或串口选错。请先用 check_port 探测，并用 "
            "list_ports 确认当前连接的是 ESP32 所在串口（蓝牙虚拟串口能打开但"
            "永远无应答）。")


def check_port() -> str:
    """主动探测当前串口是否可用，在动手前区分"被占用/不存在/空闲"。

    用 pyserial 直接尝试打开-关闭当前 PORT：
    - 打开失败 → 端口被独占或不存在（WCH 驱动除外，见下）；
    - 打开成功 → 仅说明驱动层空闲。注意 CH9102/CH340 驱动不强制独占，
      另一进程占用时这里也可能打开成功，需结合 list_ports 和实际 REPL
      应答判断；本工具能确定的结论是"打不开=一定被占或不存在"。"""
    if _SESSION.ser is not None and _SESSION.ser.is_open:
        return (f"{_SESSION.port} 正被本进程的长连接持有（这是正常状态，"
                "不是被外部占用）。外部程序此时无法使用该串口；"
                "要释放请调用 disconnect_device。")
    try:
        import serial
    except ImportError:
        return "Error: 需要 pyserial（pip install pyserial）"
    if PORT == "auto":
        return ("当前为 auto 自动探测，无法定点检查。建议先 set_port 指定串口"
                "（如 COM5），避免 auto 误选蓝牙等虚拟串口。")
    try:
        s = serial.Serial(PORT, 115200, timeout=1)
        s.close()
        return (f"{PORT} 可以打开（驱动层空闲）。提示：CH9102/CH340 驱动不强制独占，"
                "若后续 REPL 仍静默超时，仍说明有另一进程持有该端口或对面不是 ESP32。")
    except Exception as e:
        return _classify_mpremote_error(str(e))


def _mpremote_exe() -> str:
    """定位 mpremote：优先当前解释器旁的同名可执行文件（venv 内必然存在），
    避免裸依赖 PATH——直接运行 yuanshen.py 时 PATH 未必包含 venv/Scripts。"""
    name = "mpremote.exe" if os.name == "nt" else "mpremote"
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else "mpremote"


def _mpremote(*args, timeout=30, retries=2, script_timeout=False):
    """调用 mpremote。端口被占用类错误会自动重试 retries 次（间隔 1s），
    因为占用方可能正在释放；重试耗尽后抛出带诊断建议的 RuntimeError。
    script_timeout=True 时（run_script 跑长脚本）不转换 TimeoutExpired，
    由调用方按"脚本自身跑超时"处理（软复位+返回部分输出）。"""
    last_output = ""
    for attempt in range(retries + 1):
        try:
            r = subprocess.run(
                [_mpremote_exe(), "connect", PORT, *args],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if script_timeout:
                raise
            # 静默超时：串口打开了但 REPL 无应答，与 mpremote 报错分开诊断
            raise RuntimeError(_classify_silent_timeout(timeout)) from None
        except FileNotFoundError:
            raise RuntimeError(
                "找不到 mpremote。请在 Yuanshen 的 venv 中安装："
                "pip install mpremote"
            ) from None
        output = (r.stdout + r.stderr).strip() or "OK"
        if r.returncode == 0:
            return output
        last_output = output
        low = output.lower()
        busy = ("in use by another program" in low or "failed to access" in low
                or "access is denied" in low or "no device found" in low)
        if busy and attempt < retries:
            time.sleep(1)
            continue
        break
    raise RuntimeError(_classify_mpremote_error(last_output))


def _metrics(summary: str, **values) -> str:
    """在人类可读摘要后附加稳定 JSON，供 Agent 程序解析。"""
    return summary + "\nMETRICS_JSON:" + json.dumps(
        values, ensure_ascii=False, separators=(",", ":")
    )


def get_port() -> str:
    """查看当前使用的串口（auto 表示 mpremote 自动探测）。"""
    mode = "长连接" if DEVICE_MODE == "persistent" else "mpremote 短连接"
    held = "，长连接持有中" if (_SESSION.ser is not None
                              and _SESSION.ser.is_open) else ""
    return f"当前串口: {PORT}" + ("（自动探测）" if PORT == "auto" else "") \
        + f"，设备通道: {mode}{held}"


def set_port(port: str) -> str:
    """切换连接 ESP32 的串口，如 'COM5' 或 '/dev/ttyACM0'；传 'auto' 恢复自动探测。
    切换会释放当前长连接，下次设备调用时在新串口上自动重连。"""
    global PORT
    port = port.strip()
    if not port:
        return "Error: 串口名不能为空"
    if port != PORT:
        _SESSION.close()
    PORT = port
    return f"串口已切换为: {PORT}" + ("（自动探测）" if PORT == "auto" else "")


def list_ports() -> str:
    """列出可用串口设备。"""
    try:
        from serial.tools import list_ports as _lp
        ports = [f"{p.device}  {p.description}" for p in _lp.comports()]
    except ImportError:
        import glob
        ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    return ("\n".join(ports) if ports else "未发现串口设备(检查USB线；Linux 还需 dialout 权限)") \
        + f"\n当前使用: {PORT}"


def upload(local_path: str, remote_name: str = "") -> str:
    """上传文件到 ESP32。remote_name 缺省用本地文件名。"""
    if DEVICE_MODE == "persistent":
        return _SESSION.upload(local_path, remote_name)
    dest = ":" + (remote_name or Path(local_path).name)
    return _mpremote("cp", local_path, dest, timeout=120)


def run_script(path: str, timeout_s: float = 20) -> str:
    """运行本地脚本并捕获输出。超时自动 Ctrl-C 复位(应对 while True 主循环)。"""
    if DEVICE_MODE == "persistent":
        code = Path(path).read_text(encoding="utf-8")
        return _SESSION.exec(code, timeout_s)
    try:
        return _mpremote("run", path, timeout=timeout_s, script_timeout=True)
    except subprocess.TimeoutExpired as e:
        soft_reset()
        out = (e.stdout or b"")
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return "(超时截断, 已软复位)\n" + out


def repl_exec(code: str, timeout_s: float = 20) -> str:
    """在板上执行一段 MicroPython 代码, 如 'import gc; print(gc.mem_free())'。
    也是软件触发播放的入口: 'import piano; piano.play()'。"""
    if DEVICE_MODE == "persistent":
        return _SESSION.exec(code, timeout_s)
    return _mpremote("exec", code, timeout=timeout_s)


def device_ls() -> str:
    """列出板上文件系统。"""
    if DEVICE_MODE == "persistent":
        return _SESSION.exec("import os; print('\\n'.join(os.listdir()))")
    return _mpremote("fs", "ls")


def device_rm(name: str) -> str:
    """删除板上文件(典型: 改 CACHE_VERSION 后清理陈旧音色缓存 .bin/.json)。"""
    if DEVICE_MODE == "persistent":
        return _SESSION.exec(f"import os; os.remove({name!r}); print('已删除 {name}')")
    return _mpremote("fs", "rm", ":" + name)


def soft_reset() -> str:
    """软复位: 打断死循环, 重新进入 REPL。
    注意: 超时归因见 _classify_silent_timeout——不要把超时归因于 main.py
    抢占 REPL（Ctrl-C 可中断任何循环）。"""
    if DEVICE_MODE == "persistent":
        return _SESSION.soft_reset_device()
    return _mpremote("soft-reset", timeout=10)


# =============================================================================
# 音频闭环: 录音
# =============================================================================

def _read_wav(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data / 32768.0, rate


def _start_arecord(out_path, duration_s):
    return subprocess.Popen(
        ["arecord", "-q", "-D", MIC_DEVICE, "-f", "S16_LE",
         "-r", str(SAMPLE_RATE), "-c", "1",
         "-d", str(int(np.ceil(duration_s))), str(out_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def mic_check(duration_s: float = 1.5) -> str:
    """录一小段环境音, 检查麦克风是否真的在采集(全零=输入未启用)。"""
    tmp = Path("/tmp/mic_check.wav")
    p = _start_arecord(tmp, duration_s)
    p.wait(timeout=duration_s + 5)
    data, _ = _read_wav(tmp)
    rms = float(np.sqrt((data ** 2).mean()))
    peak = float(np.abs(data).max())
    # 注意: 宿主机静音(如 Fn+F4)后 VirtualBox 仍送流, 只是只剩 ±1 LSB 抖动,
    # 峰值不为绝对零。故不能只判 peak==0, 要用 RMS 阈值(正常噪声底实测 ~0.003)。
    if peak == 0.0:
        return _metrics(("麦克风录到全零(通道未开启)! 排查:\n"
                "1. VirtualBox 菜单: 设备→音频→勾选'音频输入'\n"
                "2. pavucontrol 的'输入设备'里确认电平表在动"),
                kind="mic_check", valid=False, rms=rms, peak=peak)
    if rms < 0.0005:
        return _metrics(("麦克风信号近乎全零(RMS=%.6f), 疑似被静音! 排查:\n"
                "1. 宿主机静音键(如 Fn+F4)是否按下\n"
                "2. 宿主机系统设置里麦克风音量/静音状态\n"
                "3. pavucontrol 的'输入设备'里确认电平表在动" % rms),
                kind="mic_check", valid=False, rms=rms, peak=peak)
    return _metrics(
        "麦克风正常。噪声底 RMS=%.5f 峰值=%.4f (把喇叭凑近麦克风效果更好)"
        % (rms, peak), kind="mic_check", valid=True, rms=rms, peak=peak
    )


def record_audio(out_path: str, duration_s: float = 3) -> str:
    """从麦克风录音 duration_s 秒到 WAV 文件。"""
    p = _start_arecord(out_path, duration_s)
    p.wait(timeout=duration_s + 5)
    data, _ = _read_wav(out_path)
    return "已录 %s (%.1fs, RMS=%.5f)" % (out_path, duration_s,
                                          float(np.sqrt((data ** 2).mean())))


def play_and_record(trigger_code: str, out_path: str,
                    duration_s: float = 4) -> str:
    """闭环核心: 先启动录音, 再通过 REPL 软件触发播放, 录下喇叭实际声音。

    trigger_code 例: 'import piano; piano.play()'
    (KEY1 是物理按键, agent 按不到 -- 播放逻辑必须封装成可 import 的函数)
    录音提前 ~0.5s 启动以包住起振瞬间, 后续用互相关对齐。"""
    p = _start_arecord(out_path, duration_s + 1)
    time.sleep(0.5)
    try:
        trig_out = repl_exec(trigger_code, timeout_s=duration_s + 10)
    finally:
        p.wait(timeout=duration_s + 10)
    data, _ = _read_wav(out_path)
    peak = float(np.abs(data).max())
    note = "" if peak > 0.01 else "\n警告: 录音峰值过低(%.4f), 可能没录到声音" % peak
    return "触发输出: %s\n录音已存 %s%s" % (trig_out, out_path, note)


# =============================================================================
# 音频闭环: 分析 (纯 numpy)
# =============================================================================

def _f0_fft(data, rate, lo=50.0, hi=2500.0):
    """FFT 主峰 + 抛物线插值估计基频。取信号最响的中段。"""
    n = len(data)
    seg = data[n // 4: n // 4 + min(n // 2, rate)]  # 最多 1s
    if len(seg) < 1024:
        seg = data
    seg = seg * np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(len(seg), 1.0 / rate)
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any() or spec[band].max() == 0:
        return 0.0, spec, freqs
    k = np.flatnonzero(band)[np.argmax(spec[band])]
    if 1 <= k < len(spec) - 1:      # 抛物线插值细化
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        denom = a - 2 * b + c
        delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
        return float((k + delta) * rate / len(seg)), spec, freqs
    return float(freqs[k]), spec, freqs


def _find_clicks(data, rate):
    """哒哒音检测: 一阶差分能量的孤立尖峰。返回尖峰时刻(秒)列表。"""
    d = np.abs(np.diff(data))
    win = max(1, rate // 200)                     # 5ms 平滑
    env = np.convolve(d, np.ones(win) / win, mode="same")
    med = np.median(env)
    mad = np.median(np.abs(env - med)) + 1e-12
    thresh = med + 10 * mad
    above = env > thresh
    clicks, i = [], 0
    min_gap = int(0.005 * rate)
    while i < len(above):
        if above[i]:
            j = i
            while j < len(above) and above[j]:
                j += 1
            clicks.append((i + np.argmax(env[i:j])) / rate)
            i = j + min_gap
        else:
            i += 1
    return clicks


def analyze_wav(path: str, expect_f0: float = 0) -> str:
    """分析 WAV: 基频/谐波/包络/哒哒音。expect_f0 给定时报告偏差。

    哒哒音判据: 尖峰间隔规律(变异系数<0.25)说明是分块接缝的周期性
    电荷亏空, 报告推算的块周期, 可与 CHUNK/RATE 对照。"""
    data, rate = _read_wav(path)
    dur = len(data) / rate
    rms = float(np.sqrt((data ** 2).mean()))
    lines = ["%s: %.2fs @ %dHz, RMS=%.5f, 峰值=%.4f"
             % (path, dur, rate, rms, float(np.abs(data).max()))]
    peak = float(np.abs(data).max())
    if peak == 0.0:
        return _metrics(
            lines[0] + "\n全零信号 -- 先运行 mic_check 排查录音输入",
            kind="analyze_wav", valid=False, rms=rms, peak=peak,
            sample_rate=rate, duration_s=dur, f0=0.0, cents=None,
        )

    f0, spec, freqs = _f0_fft(data, rate)
    lines.append("基频 f0 = %.2f Hz" % f0)
    cents = None
    if expect_f0:
        cents = 1200 * np.log2(f0 / expect_f0) if f0 > 0 else float("inf")
        lines.append("与期望 %.2f Hz 偏差 %.1f 音分(±10内算准)" % (expect_f0, cents))
    if f0 > 0:
        base = spec[np.argmin(np.abs(freqs - f0))] + 1e-12
        harm = ["H%d=%.1fdB" % (h, 20 * np.log10(
            spec[np.argmin(np.abs(freqs - h * f0))] / base + 1e-12))
            for h in range(2, 6) if h * f0 < rate / 2]
        lines.append("谐波(相对基频): " + " ".join(harm))

    # 包络: 20ms 窗 RMS, 报告起音/衰减形态
    win = int(0.02 * rate)
    n_win = len(data) // win
    env = np.sqrt((data[: n_win * win].reshape(n_win, win) ** 2).mean(axis=1))
    peak_at = int(np.argmax(env))
    lines.append("包络: 峰值在 %.2fs, 尾端/峰值 = %.2f"
                 % (peak_at * 0.02, float(env[-1] / (env.max() + 1e-12))))

    clicks = _find_clicks(data, rate)
    if len(clicks) >= 3:
        iv = np.diff(clicks)
        cv = float(iv.std() / (iv.mean() + 1e-12))
        if cv < 0.25:
            lines.append("检测到 %d 个规律哒声, 平均间隔 %.1fms (变异系数%.2f)"
                         % (len(clicks), iv.mean() * 1000, cv))
            lines.append("→ 周期性接缝噪声! 对照 CHUNK/采样率: 块周期=CHUNK/RATE。"
                         "周期波形改 loop(True) 硬件循环, 非周期波形做块尾电荷补偿")
        else:
            lines.append("检测到 %d 个不规律瞬态(可能是环境噪声/起止爆音)" % len(clicks))
    elif clicks:
        lines.append("检测到 %d 个孤立瞬态 @ %s s -- 若在首尾, 查直流斜坡"
                     % (len(clicks), ["%.2f" % t for t in clicks]))
    else:
        lines.append("未检测到哒声/爆音")
    return _metrics(
        "\n".join(lines), kind="analyze_wav", valid=True, rms=rms, peak=peak,
        sample_rate=rate, duration_s=dur, f0=f0,
        cents=float(cents) if cents is not None and np.isfinite(cents) else None,
    )


def compare_audio(recorded: str, reference: str) -> str:
    """录音 vs 主机预览对比。先用包络互相关对齐, 再比 f0/包络形态/哒声。

    注意蜂鸣器/小喇叭+麦克风会严重改变频谱绝对形状, 只比相对特征:
    基频偏差(音分)、包络相关系数、哒声有无。"""
    rec, rr = _read_wav(recorded)
    ref, fr = _read_wav(reference)
    if float(np.abs(rec).max()) == 0.0:
        return "录音是全零 -- 先 mic_check"

    def env_of(x, rate):
        win = int(0.01 * rate)
        n = len(x) // win
        return np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))

    e1, e2 = env_of(rec, rr), env_of(ref, fr)   # 都是 100Hz 包络序列
    corr = np.correlate(e1 - e1.mean(), e2 - e2.mean(), mode="full")
    lag = int(np.argmax(corr)) - (len(e2) - 1)
    lines = ["对齐: 录音相对参考滞后 %.2fs" % (lag * 0.01)]
    a = e1[max(lag, 0):]
    b = e2[max(-lag, 0):]
    m = min(len(a), len(b))
    if m > 10:
        c = float(np.corrcoef(a[:m], b[:m])[0, 1])
        lines.append("包络相关系数 = %.3f (>0.8 算形态一致)" % c)

    f0r, _, _ = _f0_fft(rec, rr)
    f0f, _, _ = _f0_fft(ref, fr)
    if f0r > 0 and f0f > 0:
        lines.append("基频: 录音 %.2fHz vs 参考 %.2fHz, 偏差 %.1f 音分"
                     % (f0r, f0f, 1200 * np.log2(f0r / f0f)))
    ck_r, ck_f = _find_clicks(rec, rr), _find_clicks(ref, fr)
    lines.append("瞬态尖峰: 录音 %d 个 / 参考 %d 个%s"
                 % (len(ck_r), len(ck_f),
                    " -- 录音多出的尖峰=实机才有的问题(接缝/复位/供电)"
                    if len(ck_r) > len(ck_f) + 2 else ""))
    return "\n".join(lines)


# =============================================================================
# 入口: MCP 服务器 或 命令行
# =============================================================================

TOOL_FUNCS = [list_ports, get_port, set_port, check_port, connect_device,
              disconnect_device, upload, run_script, repl_exec, device_ls,
              device_rm, soft_reset, mic_check, record_audio,
              play_and_record, analyze_wav, compare_audio]


def main():
    if len(sys.argv) > 1:                        # CLI 模式
        name = sys.argv[1]
        funcs = {f.__name__: f for f in TOOL_FUNCS}
        if name not in funcs:
            sys.exit("可用工具: " + ", ".join(funcs))
        args = [float(a) if a.replace(".", "", 1).isdigit() else a
                for a in sys.argv[2:]]
        print(funcs[name](*args))
        return

    try:                                          # MCP stdio 模式
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.exit("MCP 模式需要: pip install mcp\n"
                 "或用 CLI 模式: python3 esp32_piano_mcp.py <工具名> [参数...]")
    server = FastMCP("esp32-piano")
    for f in TOOL_FUNCS:
        server.tool()(f)
    server.run()


if __name__ == "__main__":
    main()
