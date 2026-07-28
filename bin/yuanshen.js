#!/usr/bin/env node
/* Yuanshen v1.0 正式版启动器：首次运行自动创建 Python 虚拟环境并装依赖，然后启动 agent。
 * 需要系统已装 python3（Linux/macOS）。串口访问需用户在 dialout 组。
 *
 * 数据目录：~/.yuanshen/（项目、API Key、历史记录）
 * 包目录：npm 安装位置（只读，存放代码和技能库） */
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const pkgDir = path.resolve(__dirname, "..");
const venvDir = path.join(pkgDir, ".venv");
const venvBin = path.join(venvDir, "bin");
const venvPy = path.join(venvBin, "python");

// 用户数据目录
const yuanshenDir = path.join(os.homedir(), ".yuanshen");
const yuanshenEnv = path.join(yuanshenDir, ".env");

function run(cmd, args, extraEnv) {
  return spawnSync(cmd, args, {
    stdio: "inherit",
    env: { ...process.env, PATH: venvBin + ":" + process.env.PATH, ...extraEnv },
  });
}

// 确保 python3 可用
if (spawnSync("python3", ["--version"]).status !== 0) {
  console.error("需要 python3，请先安装（如 sudo apt install python3 python3-venv）");
  process.exit(1);
}

// 创建虚拟环境
if (!fs.existsSync(venvPy)) {
  console.log("首次运行：创建 Python 虚拟环境并安装依赖（约 1~2 分钟）…");
  if (run("python3", ["-m", "venv", venvDir]).status !== 0) process.exit(1);
}

// 兼容包升级后新增 Python 依赖的情况
const depsReady = run(venvPy, ["-c",
  "import anthropic, dotenv, mcp, numpy, prompt_toolkit, openai, rich"]).status === 0;
if (!depsReady) {
  console.log("正在补齐或更新 Python 依赖…");
  if (run(venvPy, ["-m", "pip", "install", "-q", "-r",
                   path.join(pkgDir, "requirements.txt")]).status !== 0) {
    console.error("依赖安装失败，请检查网络后重试");
    process.exit(1);
  }
}

// 确保 ~/.yuanshen/ 存在
try {
  fs.mkdirSync(yuanshenDir, { recursive: true });
  fs.mkdirSync(path.join(yuanshenDir, "projects"), { recursive: true });
} catch (e) {
  // 如果 home 不可写（容器环境），回退到包目录
  console.log("⚠ 无法创建 ~/.yuanshen/，将使用包内目录（功能正常但项目数据在包目录下）");
}

// 首次运行：创建 ~/.yuanshen/.env
if (!fs.existsSync(yuanshenEnv)) {
  const exampleEnv = path.join(pkgDir, ".env.example");
  if (fs.existsSync(exampleEnv)) {
    fs.copyFileSync(exampleEnv, yuanshenEnv);
    console.log("📁 已创建 " + yuanshenEnv + "，请编辑填入 API Key");
    console.log("   或运行后使用 /api-key 命令设置");
  }
}

// 启动 Agent
const r = run(venvPy, [path.join(pkgDir, "yuanshen.py"), ...process.argv.slice(2)]);
process.exit(r.status === null ? 0 : r.status);
