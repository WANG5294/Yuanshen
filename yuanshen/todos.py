"""TodoManager 主线任务状态。"""
import json
import re
from pathlib import Path

import yuanshen.config as _cfg

MAINLINE = ["编写代码", "烧录代码", "测试代码", "完成"]


class TodoManager:
    """记住用户确认后的规范化需求（goal），维护主线任务清单。
    每个新任务自动初始化四个固定主线步骤；模型只能更新其状态。"""

    def __init__(self):
        self.goal = ""
        self.audio_required = False
        self.items = []
        self.deployed_main = False
        self.device_verified = False
        self.deployed_hash = None
        self.evidence_log = []
        self.mic_noise_rms = None
        self.audio_signal_ratio = None
        self.audio_pitch_ok = None
        self.audio_analysis_attempted = False
        self.audio_degraded_reason = ""

    def start(self, goal: str, audio_required: bool = False):
        self.goal = goal
        self.audio_required = audio_required
        self.deployed_main = False        # 本任务是否已把程序部署为板上 main.py
        self.device_verified = False
        self.deployed_hash = None
        self.evidence_log = []
        self.mic_noise_rms = None
        self.audio_signal_ratio = None
        self.audio_pitch_ok = None
        self.audio_analysis_attempted = False
        self.audio_degraded_reason = ""
        self.items = [{"content": s, "status": "pending", "activeForm": s}
                      for s in MAINLINE]

    def requires_audio_validation(self) -> bool:
        return self.audio_required

    def observe_tool(self, name: str, args: dict, output: str, ok: bool):
        """从真实工具结果维护完成门禁；代码改变后旧烧录和验证立即失效。"""
        metrics = {}
        marker = re.search(r"(?:^|\n)METRICS_JSON:(\{[^\n]+\})", output)
        if marker:
            try:
                metrics = json.loads(marker.group(1))
            except (json.JSONDecodeError, TypeError):
                metrics = {}
        target = str(args.get("path") or args.get("local_path") or "")
        if name in ("write_file", "edit_file") and Path(target).name == "main.py" and ok:
            self.deployed_main = False
            self.device_verified = False
            self.audio_signal_ratio = None
            self.audio_pitch_ok = None
            self.audio_analysis_attempted = False
            self.audio_degraded_reason = ""
            self.evidence_log.append("main.py 内容改变，旧部署与验证证据失效")
        if name == "repl_exec":
            code = str(args.get("code", ""))
            verifies_main = bool(re.search(
                r"open\s*\(\s*['\"]main\.py['\"]\s*\)", code
            ))
        elif name == "play_and_record":
            code = str(args.get("trigger_code", ""))
            verifies_main = bool(re.search(
                r"open\s*\(\s*['\"]main\.py['\"]\s*\)", code
            ))
        else:
            verifies_main = False
        if verifies_main and ok and self.deployed_main:
            self.device_verified = True
            self.evidence_log.append(
                f"{name} 已执行板上 main.py，匹配当前部署证据"
            )
        if name == "mic_check" and ok:
            if metrics.get("kind") == "mic_check":
                rms = metrics.get("rms")
                if isinstance(rms, (int, float)) and rms >= 0:
                    self.mic_noise_rms = float(rms)
                if metrics.get("valid") is False:
                    self.audio_degraded_reason = output.splitlines()[0]
            elif output.startswith(("麦克风录到全零", "麦克风信号近乎全零")):
                self.audio_degraded_reason = output.splitlines()[0]
        if name == "analyze_wav" and ok:
            self.audio_analysis_attempted = True
            if metrics.get("kind") == "analyze_wav":
                peak = metrics.get("peak")
                cents = metrics.get("cents")
                if (isinstance(peak, (int, float)) and peak >= 0
                        and self.mic_noise_rms and self.mic_noise_rms > 0):
                    self.audio_signal_ratio = float(peak) / self.mic_noise_rms
                if isinstance(cents, (int, float)):
                    self.audio_pitch_ok = abs(float(cents)) <= 10
                if metrics.get("valid") is False:
                    self.audio_degraded_reason = output.splitlines()[0]
            if _cfg.AUDIO_VALIDATION_MODE == "auto":
                failure = self.audio_failure()
                if failure:
                    self.audio_degraded_reason = failure

    def audio_failure(self) -> str | None:
        if not self.audio_analysis_attempted:
            return "声音任务尚未完成一次录音分析"
        if self.audio_signal_ratio is None:
            return "录音无法计算峰值/噪声底比"
        if self.audio_signal_ratio < 5:
            return f"闭环峰值/噪声底比仅 {self.audio_signal_ratio:.2f}，要求至少 5.00"
        if self.audio_pitch_ok is False:
            return "录音基频偏差未达到 ±10 音分要求"
        if self.audio_pitch_ok is None:
            return "录音未提供可判定的目标音高结果"
        return None

    def validation_error(self) -> str | None:
        if not self.deployed_main:
            return "尚未把当前版本上传为板上 main.py"
        if not self.device_verified:
            return "尚无当前烧录版本的成功实机执行证据"
        if self.requires_audio_validation():
            if _cfg.AUDIO_VALIDATION_MODE == "off":
                return None
            if _cfg.AUDIO_VALIDATION_MODE == "auto" and self.audio_degraded_reason:
                return None
            failure = self.audio_failure()
            if failure:
                if _cfg.AUDIO_VALIDATION_MODE == "auto" and self.audio_analysis_attempted:
                    self.audio_degraded_reason = failure
                    return None
                return failure
        return None

    def update(self, items: list) -> str:
        if len(items) != len(MAINLINE):
            raise ValueError("TodoList 必须且只能包含 4 项固定主线任务")
        validated = []
        in_progress = 0
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            active = str(item.get("activeForm", content)).strip()
            if not content:
                raise ValueError(f"第 {i} 项: content 必填")
            if content != MAINLINE[i]:
                raise ValueError(
                    f"第 {i + 1} 项必须是“{MAINLINE[i]}”，不可改名、调序或插入子项"
                )
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"第 {i} 项: status 非法")
            if status == "in_progress":
                in_progress += 1
            validated.append({"content": content, "status": status,
                              "activeForm": active})
            if self.items:
                previous = self.items[i]["status"]
                rank = {"pending": 0, "in_progress": 1, "completed": 2}
                if rank[status] < rank[previous]:
                    raise ValueError(
                        f"{content} 状态不能从 {previous} 回退到 {status}"
                    )
        if in_progress > 1:
            raise ValueError("同时只能有一个任务 in_progress")
        # 卡点：没把程序部署为板上 main.py，不允许宣称"烧录"完成
        for v in validated:
            if ("烧录" in v["content"] and v["status"] == "completed"
                    and not self.deployed_main):
                raise ValueError(
                    "烧录代码不能标记完成：本任务还没有把程序上传为板上的 main.py"
                    "（upload 时设 remote_name='main.py'，开机自启才算烧录；"
                    "仅上传为其他文件名只是拷贝模块，不算烧录）")
        states = {step: validated[index]["status"]
                  for index, step in enumerate(MAINLINE)}
        for index, step in enumerate(MAINLINE):
            if states[step] == "completed":
                unfinished = [s for s in MAINLINE[:index]
                              if states[s] != "completed"]
                if unfinished:
                    raise ValueError(
                        f"{step}不能标记完成：前置步骤未完成：{', '.join(unfinished)}")
        if states["测试代码"] == "completed":
            error = self.validation_error()
            if error:
                raise ValueError(f"测试代码不能标记完成：{error}")
        if states["完成"] == "completed" and states["测试代码"] != "completed":
            raise ValueError("完成不能标记完成：测试代码尚未完成")
        self.items = validated
        return self.render()

    def is_complete(self) -> bool:
        return self.validation_error() is None and bool(self.items) and all(
            item["content"] == step and item["status"] == "completed"
            for item, step in zip(self.items, MAINLINE))

    def status_note(self) -> str:
        if self.requires_audio_validation() and _cfg.AUDIO_VALIDATION_MODE == "off":
            return "音频验证已关闭；任务仅按设备执行证据验收。"
        if self.audio_degraded_reason:
            return f"音频验证不可用且不阻塞主任务：{self.audio_degraded_reason}"
        return "无"

    def render(self) -> str:
        if not self.items:
            return "（无任务）"
        marks = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}
        lines = [f"{marks[t['status']]} {t['content']}" for t in self.items]
        return "\n".join(lines)


TODO = TodoManager()
AUDIO_TOOL_NAMES = {
    "mic_check", "record_audio", "play_and_record", "analyze_wav", "compare_audio",
}
