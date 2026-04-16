const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ROOT_DIR = __dirname;
const TRACKER_SCRIPT = path.join(ROOT_DIR, "earpods_tracker.py");
const VENV_PYTHON = path.join(
  ROOT_DIR,
  ".venv",
  process.platform === "win32" ? "Scripts" : "bin",
  process.platform === "win32" ? "python.exe" : "python",
);
const PYTHON_BIN =
  process.env.PYTHON_BIN ||
  (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : process.platform === "win32" ? "python" : "python3");
const args = [TRACKER_SCRIPT, ...process.argv.slice(2)];

const child = spawn(PYTHON_BIN, args, {
  cwd: ROOT_DIR,
  stdio: "inherit",
  windowsHide: false,
});

child.on("error", (error) => {
  console.error(`Could not start Python using "${PYTHON_BIN}".`);
  console.error("Set PYTHON_BIN if Python uses a different command on your machine.");
  console.error(`Original error: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 0);
});
