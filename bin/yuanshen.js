#!/usr/bin/env node
/* Yuanshen v1.0 正式版启动器：首次运行自动创建 Python 虚拟环境并装依赖，然后启动 agent。
 * 需要系统已装 python3（Linux/macOS）。串口访问需用户在 dialout 组。 */
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const pkgDir = path.resolve(__dirname, "..");
const venvDir = path.join(pkgDir, ".venv");
const venvBin = path.join(venvDir, "bin");
const venvPy = path.join(venvBin, "python");

function run(cmd, args, extraEnv) {
  return spawnSync(cmd, args, {
    stdio: "inherit",
    env: { ...process.env, PATH: venvBin + ":" + process.env.PATH, ...extraEnv },
  });
}

if (spawnSync("python3", ["--version"]).status !== 0) {
  console.error("需要 python3，请先安装（如 sudo apt install python3 python3-venv）");
  process.exit(1);
}

if (!fs.existsSync(venvPy)) {
  console.log("首次运行：创建 Python 虚拟环境并安装依赖（约 1~2 分钟）…");
  if (run("python3", ["-m", "venv", venvDir]).status !== 0) process.exit(1);
}

// 兼容包升级后新增 Python 依赖的情况，不要求用户手动重建已有虚拟环境。
const depsReady = run(venvPy, ["-c",
  "import anthropic, dotenv, mcp, numpy, prompt_toolkit, openai"]).status === 0;
if (!depsReady) {
  console.log("正在补齐或更新 Python 依赖…");
  if (run(venvPy, ["-m", "pip", "install", "-q", "-r",
                   path.join(pkgDir, "requirements.txt")]).status !== 0) {
    console.error("依赖安装失败，请检查网络后重试");
    process.exit(1);
  }
}

const wiring = path.join(pkgDir, "wiring.md");
if (!fs.existsSync(wiring)) fs.writeFileSync(wiring, "没有插线\n");

const envFile = path.join(pkgDir, ".env");
if (!fs.existsSync(envFile)) {
  console.log("提示：还没有 .env（API Key）。请 cp .env.example .env 并填入你的 Key：");
  console.log("  " + pkgDir);
}

const r = run(venvPy, [path.join(pkgDir, "yuanshen.py"), ...process.argv.slice(2)]);
process.exit(r.status === null ? 0 : r.status);
