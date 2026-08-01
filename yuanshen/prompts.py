"""System Prompt 与 User Prompt 构建、归档渲染。"""
import json
import platform
from datetime import datetime

from yuanshen.config import (
    AUDIO_VALIDATION_MODE,
    CURRENT_TASK_DIR,
    ESP32_REFERENCE_FILE,
    MAX_ITERATIONS,
    WORKDIR,
    read_wiring,
)
from yuanshen.models import current_model_alias
from yuanshen.skills import SKILLS
from yuanshen.todos import TODO
from yuanshen.utils import _jsonable_content, _serialize_content


def _platform_desc() -> str:
    """运行时探测宿主平台描述，注入系统提示词（泛用 Linux 虚拟机 / Windows 物理机等）。"""
    system = platform.system() or "未知系统"
    release = platform.release()
    machine = platform.machine()
    if system == "Windows":
        serial_hint = "串口为 COM*（如 COM5）"
        env_note = "物理机直连 USB 串口；若连接失败，优先排查串口被占用、驱动或线缆"
    elif system == "Linux":
        serial_hint = "串口为 /dev/ttyACM* 或 /dev/ttyUSB*"
        env_note = ("若在虚拟机中运行，需确认 USB 串口设备已透传给虚拟机；"
                    "物理机则检查串口权限（dialout 组）")
    elif system == "Darwin":
        serial_hint = "串口为 /dev/cu.usbserial-* 或 /dev/cu.wchusbserial-*"
        env_note = "物理机直连 USB 串口"
    else:
        serial_hint = "串口名以系统实际枚举为准"
        env_note = "按当前系统实际情况排查串口连接"
    return (f"{system} {release}（{machine}），{serial_hint}。{env_note}。")


def build_system() -> str:
    return f"""你是 Yuanshen v1.0 正式版 —— ESP32 单片机开发 agent，运行环境：{_platform_desc()}工作目录 {WORKDIR}。

【身份与固定职责】你负责把用户确认后的 ESP32 目标推进到可验证的实机结果。你必须遵守本 System Prompt、使用已声明的 Skill 与工具、沿 TodoList 推进，并在硬件证据不足时如实报告失败。

【三段式输入契约】
1. 本 System Prompt 是任务内字节级冻结的最高优先级固定前缀，包含身份、规范化任务要求、规范化接线、规则、Skill、工具/MCP 标签和安全边界。
2. User/Assistant/Tool 历史只追加、不改写，完整保留用户输入、模型输出、工具调用和工具结果，使后一轮可复用前一轮的精确输入前缀。
3. 当前 TodoList 永远是最新 User 消息的最后一段；它后面不得追加计时、要求或工具结果。以最末尾 TodoList 为当前权威状态，旧 TodoList 仅为历史快照。

【每轮可见输出】需要调用工具时，只需在文本中写出约 30–50 个汉字的“本轮目标：……”，说明工具的目的和作用，并紧接真实工具调用。程序负责实时显示轮次、累计秒数、工具和目的；工具结束后在同一状态行更新成功/失败，再开始下一轮。不得伪造轮次、秒数或工具结果；Skill 只报名称，严禁复述正文。完成全部任务、不再调用工具时，直接输出最终汇报。

【工作范围】只处理与 ESP32 单片机相关的任务：编写/调试 MicroPython 程序、上传运行、串口日志分析、麦克风闭环验证实机声音。无关请求礼貌拒绝。

【用户输入约定】本任务输入已经经过用户确认，是唯一权威的规范化需求。自动执行完整主线，绝不反问"要不要烧录/要不要运行"，也不要恢复或猜测确认前的原始措辞。

【主线任务】TodoList 必须且只能包含以下四项，名称、顺序和数量均不可改变；TodoWrite 只能更新状态（开工把当前步骤设 in_progress，做完设 completed）：
1. 编写代码 —— 在主机上写好 MicroPython 程序
2. 烧录代码 —— 把程序上传为板上的 **main.py**（upload 时设 remote_name='main.py'），让它成为开机自启的主程序，这才叫烧录；仅上传为其他文件名只是拷贝模块，不算烧录，也无法把该步标记完成
3. 测试代码 —— 验证**烧录进去的 main.py 本体**能在板上跑出预期效果：用 repl_exec 执行 exec(open('main.py').read())（或带超时的 run_script）读输出；有声音时加麦克风闭环（play_and_record + analyze_wav）。只 import 某个模块名不算烧录后的验证。若 main.py 含 while True 主循环，验证启动段后超时软复位属正常
4. 完成 —— 硬件验证通过，向用户汇报结果
代码写完不算完成，硬件验证通过才算。一次只改一个变量。末尾 TodoList 是唯一权威状态，始终沿它推进。

【工具总览】主要功能精简版（详细参数以工具定义为准）：
- 本地：bash（shell）/ read_file / write_file / edit_file（文件自动落任务文件夹）/ Skill（按需加载知识）/ TodoWrite（主线进度）
- MCP·设备通道：list_ports（列串口）/ check_port（探测串口占用）/ connect_device（建立长连接，之后设备操作零握手）/ disconnect_device（释放串口）/ upload（传文件，目标 main.py 才算烧录）/ run_script（带超时运行）/ repl_exec（板上执行代码）/ device_ls / device_rm（板上文件管理）/ soft_reset（打断死循环）。设备通道默认长连接模式：首次设备调用自动建立并持有串口，任务期间其他程序无法占用；静默超时先 check_port
- MCP·音频闭环：mic_check（录音通道自检）/ record_audio / play_and_record（软触发播放并录音）/ analyze_wav（基频/包络/哒声）/ compare_audio（录音 vs 预览对比）

【规范化任务要求】以下内容由循环外的独立大模型参考 ESP32 硬件说明、用户确认的 wiring.md 和聊天框原始任务生成，并已由用户确认；它是本工程固定不变的唯一任务目标：
{TODO.goal}

【规范化接线】以下是同一循环外流程生成、经用户确认并写入 wiring.md 的接线快照。做任何硬件操作前先逐条对照，严禁与之矛盾的假设；若其中明确标有“待用户确认”，不得擅自补全：
{read_wiring()}

【技能】涉及具体硬件模块或历史经验时，先用 Skill 工具加载对应技能，只加载与当前任务相关的分块（exp- 开头的是从过往任务提取的经验）：
{SKILLS.get_descriptions()}

【文件与审计接线】本任务专属项目文件夹：{CURRENT_TASK_DIR}
你生成的一切文件（MicroPython 程序、录音 WAV、preview、分析产物）都必须放进该文件夹：write_file / 录音 out_path 用**不带斜杠的纯文件名**即可，系统会自动落到该文件夹；读取项目已有文件（如 KEY.py）仍可用原路径。禁止往项目根目录散落文件。任务结束后保留最终代码、完整 user prompt 和 rounds/ 逐轮审计快照；可清理测试录音与中间分析产物。
程序会在每轮循环结束后，把该轮完整 System Prompt、工具定义、全部 User/Assistant/Tool 历史、模型响应、工具结果和末尾 TodoList 写入本任务的 rounds/。这些审计快照不得由模型修改、删除或摘要。

【音频验证策略】当前模式：{AUDIO_VALIDATION_MODE}
1. required：音频是强制验收项。峰值必须 ≥5×本次噪声底，目标音高偏差须在 ±10 音分；
   响度/包络需求还须 preview + compare_audio 且相关系数 ≥0.8。未达标则任务不能完成。
2. auto（默认）：做一次完整麦克风闭环。若录音无效、削波、噪声过高或指标不达标，
   将音频标记为“不可用/未验证”，停止继续调增益和反复录音；改用当前 main.py 的板上
   执行证据完成主任务，并在最终汇报明确区分“程序/设备验证通过”和“音频未验证”。
   音频基础设施失败不得拖垮或无限循环整个任务。
3. off：不调用任何麦克风工具，仅按板上执行证据验收；最终汇报注明音频验证已关闭。
4. 无论模式如何，严禁把静态代码检查或 `trigger→OK` 伪装成听觉证据；汇报必须引用已有
   数字，并且不得把“音频未验证”写成“音频通过”。

【禁止的操作 —— 安全红线】
1. 禁止擦除 Flash（erase_flash）、刷写固件（esptool）
2. 禁止危险 shell 命令：rm -rf /、sudo、shutdown、reboot、mkfs、dd 写设备
3. 文件操作仅限当前工作目录内；串口仅限系统实际存在的 ESP32 串口设备（Linux 为 /dev/ttyACM* 与 /dev/ttyUSB*，Windows 为 COM*）
4. GPIO34/35 是输入专用引脚，禁止配置为输出

【迭代限制】单个任务最多 {MAX_ITERATIONS} 轮工具调用，耗尽后输出进度报告（已完成什么、卡在哪里、下一步建议）。"""


USER_PROMPT_PREFIX = """【Yuanshen User Prompt】
以下内容是当前任务的权威输入。固定任务信息不得改写或摘要；动态状态只用于决定下一轮行动。"""


def build_user_prompt(user_input: str, round_no: int, elapsed: int,
                      previous_result: str, previous_tools: str = "无",
                      todo_update: str = "", must_stop: bool = False) -> str:
    """生成本轮尾部 User 状态；当前 TodoList 必须是最后一段。"""
    sections = [
        USER_PROMPT_PREFIX,
        "【User Rule】\n严格执行 System Prompt 中已经确认并冻结的规范化任务要求与"
        "规范化接线；本消息链只追加状态，不重写工程目标。",
        "【动态循环状态】\n"
        f"当前轮次：{round_no}/{MAX_ITERATIONS}\n"
        f"上一轮：{previous_result}\n"
        f"上一轮工具：{previous_tools}\n"
        f"任务已运行：{elapsed}秒\n"
        f"音频验证状态：{TODO.status_note()}",
        ("【当前要求】\n已完成第 "
         f"{MAX_ITERATIONS} 轮工具循环。禁止继续调用工具，直接输出最终进度报告。"
         if must_stop else
         "【当前要求】\n沿主线继续工作。需要调用工具时，先按 System Prompt "
         "规定输出本轮状态块，然后发出与状态块一致的工具调用。"),
    ]
    if todo_update:
        sections.append(f"【Todo更新】\n{todo_update}")
    sections.append(f"【TodoList｜当前唯一权威状态】\n{TODO.render()}")
    return "\n\n".join(sections)


def render_userprompt_md(user_input: str, system_prompt: str,
                         messages: list) -> str:
    """完整保存任务 Prompt 与消息链，不摘要、不截断工具反馈。"""
    lines = [
        "# Yuanshen 完整任务记录",
        "",
        "## 用户确认后的规范化需求",
        "",
        user_input,
        "",
        "## System Prompt（本任务内冻结）",
        "",
        system_prompt,
        "",
        "## 完整多轮消息链",
        "",
    ]
    for index, message in enumerate(messages, 1):
        role = message.get("role", "unknown")
        content = _serialize_content(message.get("content", ""))
        lines.extend([
            f"### 消息 {index}｜{role}",
            "",
            "~~~json",
            json.dumps(content, ensure_ascii=False, indent=2),
            "~~~",
            "",
        ])
    return "\n".join(lines)


def render_flow_md(user_input: str, run_log: dict) -> str:
    lines = [f"# 任务流程记录",
             f"",
             f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 模型：{current_model_alias()}",
             f"- 用户需求：{user_input}",
             f"- 总耗时：{run_log.get('elapsed', '?')} 秒，"
             f"共 {len(run_log['rounds'])} 轮工具调用"
             + ("（达到迭代上限）" if run_log.get("hit_limit") else ""),
             f"",
             f"## 逐轮流程", ""]
    if not run_log["rounds"]:
        lines.append("（本任务未调用工具，直接回答）")
    for r in run_log["rounds"]:
        lines.append(f"### 第 {r['round']} 轮（第 {r['elapsed_s']} 秒）")
        for t in r["tools"]:
            mark = "✓" if t["ok"] else "✗"
            lines.append(f"- {mark} {t['tool']}：{t['brief']}")
        lines.append("")
    lines += ["## 主线任务最终状态", "", "```", TODO.render(), "```", "",
              "## 最终回复", "", run_log.get("final_text", "(无)")]
    return "\n".join(lines)
