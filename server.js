const http = require("node:http");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ROOT_DIR = __dirname;
const TRACKER_SCRIPT = path.join(ROOT_DIR, "earpods_tracker.py");
const HOST = process.env.HOST || "127.0.0.1";
const PORT = Number(process.env.PORT || 3000);
const VENV_PYTHON = path.join(
  ROOT_DIR,
  ".venv",
  process.platform === "win32" ? "Scripts" : "bin",
  process.platform === "win32" ? "python.exe" : "python",
);
const PYTHON_BIN =
  process.env.PYTHON_BIN ||
  (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : process.platform === "win32" ? "python" : "python3");

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

class HttpError extends Error {
  constructor(statusCode, message, details) {
    super(message);
    this.statusCode = statusCode;
    this.details = details;
  }
}

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload, null, 2);
  res.writeHead(statusCode, {
    ...CORS_HEADERS,
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function sendError(res, error) {
  const statusCode = error.statusCode || 500;
  sendJson(res, statusCode, {
    ok: false,
    error: error.message || "Unexpected server error.",
    details: error.details,
  });
}

function getTextParam(searchParams, name) {
  const value = searchParams.get(name);
  if (value === null || value.trim() === "") {
    return null;
  }
  return value.trim();
}

function getBooleanParam(searchParams, name) {
  const value = searchParams.get(name);
  if (value === null) {
    return false;
  }
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function appendNumberArg(args, searchParams, queryName, flag, options = {}) {
  const value = searchParams.get(queryName);
  if (value === null || value.trim() === "") {
    return;
  }

  const numberValue = Number(value);
  const min = options.min ?? Number.NEGATIVE_INFINITY;
  const max = options.max ?? Number.POSITIVE_INFINITY;

  if (!Number.isFinite(numberValue)) {
    throw new HttpError(400, `${queryName} must be a number.`);
  }
  if (options.integer && !Number.isInteger(numberValue)) {
    throw new HttpError(400, `${queryName} must be an integer.`);
  }
  if (numberValue < min || numberValue > max) {
    throw new HttpError(400, `${queryName} must be between ${min} and ${max}.`);
  }

  args.push(flag, String(numberValue));
}

function appendScannerOptions(args, searchParams) {
  appendNumberArg(args, searchParams, "scanTime", "--scan-time", { min: 0.1, max: 30 });
  appendNumberArg(args, searchParams, "refreshInterval", "--refresh-interval", {
    min: 0,
    max: 10,
  });
  appendNumberArg(args, searchParams, "windowSize", "--window-size", {
    integer: true,
    min: 1,
    max: 50,
  });
  appendNumberArg(args, searchParams, "referenceRssi", "--reference-rssi", {
    integer: true,
    min: -120,
    max: -1,
  });
  appendNumberArg(args, searchParams, "pathLoss", "--path-loss", { min: 0.1, max: 10 });
  appendNumberArg(args, searchParams, "maxMissed", "--max-missed", {
    integer: true,
    min: 1,
    max: 100,
  });

  if (getBooleanParam(searchParams, "fast")) {
    args.push("--fast");
  }
}

function buildListArgs(searchParams) {
  const args = [TRACKER_SCRIPT, "--list", "--json"];
  appendNumberArg(args, searchParams, "scanTime", "--scan-time", { min: 0.1, max: 30 });
  return args;
}

function buildDiagnoseArgs(searchParams) {
  const args = [TRACKER_SCRIPT, "--diagnose", "--json"];
  appendNumberArg(args, searchParams, "scanTime", "--scan-time", { min: 0.1, max: 30 });
  return args;
}

function buildTrackArgs(searchParams) {
  const targetName = getTextParam(searchParams, "targetName");
  const targetAddress = getTextParam(searchParams, "targetAddress");

  if (!targetName && !targetAddress) {
    throw new HttpError(400, "Provide targetName or targetAddress for live tracking.");
  }

  const args = [TRACKER_SCRIPT, "--json-lines"];
  if (targetName) {
    args.push("--target-name", targetName);
  }
  if (targetAddress) {
    args.push("--target-address", targetAddress);
  }

  appendScannerOptions(args, searchParams);
  return args;
}

function spawnPython(args) {
  try {
    return spawn(PYTHON_BIN, args, {
      cwd: ROOT_DIR,
      windowsHide: true,
    });
  } catch (error) {
    throw new HttpError(
      500,
      `Could not start Python. Set PYTHON_BIN if "${PYTHON_BIN}" is not available.`,
      error.message,
    );
  }
}

function runPythonJson(args, timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnPython(args);
    } catch (error) {
      reject(error);
      return;
    }

    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;

    const finish = (callback) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      callback();
    };

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });

    child.on("error", (error) => {
      finish(() => {
        reject(
          new HttpError(
            500,
            `Could not start Python. Set PYTHON_BIN if "${PYTHON_BIN}" is not available.`,
            error.message,
          ),
        );
      });
    });

    child.on("close", (code) => {
      finish(() => {
        if (timedOut) {
          reject(new HttpError(504, "Python scan timed out."));
          return;
        }
        if (code !== 0) {
          reject(new HttpError(500, "Python tracker failed.", stderr.trim() || stdout.trim()));
          return;
        }

        try {
          resolve(JSON.parse(stdout));
        } catch (error) {
          reject(
            new HttpError(
              500,
              "Python returned a response that was not valid JSON.",
              stdout.trim() || error.message,
            ),
          );
        }
      });
    });
  });
}

function sendSse(res, eventName, payload) {
  res.write(`event: ${eventName}\n`);
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function handleTrackStream(req, res, searchParams) {
  const args = buildTrackArgs(searchParams);
  const child = spawnPython(args);

  res.writeHead(200, {
    ...CORS_HEADERS,
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });

  let closed = false;
  let buffer = "";
  const heartbeat = setInterval(() => {
    if (!closed) {
      sendSse(res, "heartbeat", { timestamp: new Date().toISOString() });
    }
  }, 15000);

  const closeStream = () => {
    if (closed) {
      return;
    }
    closed = true;
    clearInterval(heartbeat);
    if (!child.killed) {
      child.kill();
    }
    res.end();
  };

  sendSse(res, "ready", {
    ok: true,
    pid: child.pid,
    message: "Live tracking started.",
  });

  child.stdout.on("data", (chunk) => {
    buffer += chunk.toString("utf8");
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmedLine = line.trim();
      if (!trimmedLine) {
        continue;
      }

      try {
        const payload = JSON.parse(trimmedLine);
        sendSse(res, payload.type || "message", payload);
      } catch (error) {
        sendSse(res, "raw", { line: trimmedLine });
      }
    }
  });

  child.stderr.on("data", (chunk) => {
    sendSse(res, "stderr", { message: chunk.toString("utf8").trim() });
  });

  child.on("error", (error) => {
    sendSse(res, "error", {
      message: `Could not start Python. Set PYTHON_BIN if "${PYTHON_BIN}" is not available.`,
      details: error.message,
    });
    closeStream();
  });

  child.on("close", (code) => {
    if (!closed) {
      sendSse(res, "closed", {
        code,
        message: "Python tracker process stopped.",
      });
      closeStream();
    }
  });

  req.on("close", closeStream);
}

function rootPayload() {
  return {
    ok: true,
    service: "Earpods BLE Tracker Backend",
    python: PYTHON_BIN,
    endpoints: [
      "GET /health",
      "GET /api/devices?scanTime=2",
      "GET /api/diagnostics?scanTime=2",
      "GET /api/track/stream?targetName=My%20Earbuds",
      "GET /api/track/stream?targetAddress=<BLUETOOTH_ADDRESS>&fast=true",
    ],
  };
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS_HEADERS);
    res.end();
    return;
  }

  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);

  try {
    if (req.method !== "GET") {
      throw new HttpError(405, "Only GET and OPTIONS are supported.");
    }

    if (url.pathname === "/" || url.pathname === "/api") {
      sendJson(res, 200, rootPayload());
      return;
    }

    if (url.pathname === "/health") {
      sendJson(res, 200, {
        ok: true,
        service: "Earpods BLE Tracker Backend",
        tracker: TRACKER_SCRIPT,
        python: PYTHON_BIN,
      });
      return;
    }

    if (url.pathname === "/api/devices") {
      const payload = await runPythonJson(buildListArgs(url.searchParams));
      sendJson(res, 200, { ok: true, ...payload });
      return;
    }

    if (url.pathname === "/api/diagnostics") {
      const payload = await runPythonJson(buildDiagnoseArgs(url.searchParams));
      sendJson(res, 200, { ok: true, ...payload });
      return;
    }

    if (url.pathname === "/api/track/stream") {
      handleTrackStream(req, res, url.searchParams);
      return;
    }

    throw new HttpError(404, "Endpoint not found.");
  } catch (error) {
    sendError(res, error);
  }
});

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    console.error(`Port ${PORT} is already in use on ${HOST}.`);
    console.error("Stop the process using that port or start this server with another PORT value.");
    process.exit(1);
  }

  console.error(error);
  process.exit(1);
});

server.listen(PORT, HOST, () => {
  console.log(`Earpods backend is running at http://${HOST}:${PORT}`);
});
