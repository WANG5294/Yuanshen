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
  设备通道: list_ports / get_port / set_port / upload / run_script /
            repl_exec / device_ls / device_rm / soft_reset
  音频闭环: mic_check / record_audio / play_and_record /
            analyze_wav / compare_audio

依赖: mpremote, arecord(alsa-utils), numpy。分析全部用 numpy, 不需要 scipy。
"""

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
MIC_DEVICE = "default"          # PipeWire; 录到全零请检查 VirtualBox 音频输入
SAMPLE_RATE = 44100


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


def _mpremote_exe() -> str:
    """定位 mpremote：优先当前解释器旁的同名可执行文件（venv 内必然存在），
    避免裸依赖 PATH——直接运行 yuanshen.py 时 PATH 未必包含 venv/Scripts。"""
    name = "mpremote.exe" if os.name == "nt" else "mpremote"
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else "mpremote"


def _mpremote(*args, timeout=30, retries=2):
    """调用 mpremote。端口被占用类错误会自动重试 retries 次（间隔 1s），
    因为占用方可能正在释放；重试耗尽后抛出带诊断建议的 RuntimeError。"""
    last_output = ""
    for attempt in range(retries + 1):
        try:
            r = subprocess.run(
                [_mpremote_exe(), "connect", PORT, *args],
                capture_output=True, text=True, timeout=timeout,
            )
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
    return f"当前串口: {PORT}" + ("（自动探测）" if PORT == "auto" else "")


def set_port(port: str) -> str:
    """切换连接 ESP32 的串口，如 'COM5' 或 '/dev/ttyACM0'；传 'auto' 恢复自动探测。"""
    global PORT
    port = port.strip()
    if not port:
        return "Error: 串口名不能为空"
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
    dest = ":" + (remote_name or Path(local_path).name)
    return _mpremote("cp", local_path, dest, timeout=120)


def run_script(path: str, timeout_s: float = 20) -> str:
    """运行本地脚本并捕获输出。超时自动 Ctrl-C 复位(应对 while True 主循环)。"""
    try:
        return _mpremote("run", path, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        soft_reset()
        out = (e.stdout or b"")
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return "(超时截断, 已软复位)\n" + out


def repl_exec(code: str, timeout_s: float = 20) -> str:
    """在板上执行一段 MicroPython 代码, 如 'import gc; print(gc.mem_free())'。
    也是软件触发播放的入口: 'import piano; piano.play()'。"""
    return _mpremote("exec", code, timeout=timeout_s)


def device_ls() -> str:
    """列出板上文件系统。"""
    return _mpremote("fs", "ls")


def device_rm(name: str) -> str:
    """删除板上文件(典型: 改 CACHE_VERSION 后清理陈旧音色缓存 .bin/.json)。"""
    return _mpremote("fs", "rm", ":" + name)


def soft_reset() -> str:
    """软复位: 打断死循环, 重新进入 REPL。"""
    try:
        return _mpremote("soft-reset", timeout=10)
    except subprocess.TimeoutExpired:
        return ("软复位超时。若板上 main.py 上电自动运行并抢占 REPL，恢复顺序："
                "1) 确认无其他程序占用串口；2) 拔插 USB 后立即执行 repl_exec 或 "
                "device_ls（mpremote 会在连接时发 Ctrl-C 抢在 main.py 前接管）；"
                "3) 仍无效则 device_rm main.py 断掉自动运行。")


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

TOOL_FUNCS = [list_ports, get_port, set_port, upload, run_script, repl_exec,
              device_ls, device_rm, soft_reset, mic_check, record_audio,
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
