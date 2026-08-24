#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const pkgRoot = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);
const venvDir =
  process.env.CLIPTUDE_VENV || path.join(os.homedir(), ".cliptude", "venv");

function pythonCandidates() {
  if (process.platform === "win32") {
    return ["py", "python", "python3"];
  }
  return ["python3", "python"];
}

function findPython() {
  for (const cmd of pythonCandidates()) {
    const probe = spawnSync(
      cmd,
      ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
      { encoding: "utf8" },
    );
    if (probe.status === 0) {
      return cmd;
    }
  }
  return null;
}

function venvPython() {
  if (process.platform === "win32") {
    return path.join(venvDir, "Scripts", "python.exe");
  }
  return path.join(venvDir, "bin", "python");
}

function packageVersion() {
  try {
    return JSON.parse(fs.readFileSync(path.join(pkgRoot, "package.json"), "utf8"))
      .version;
  } catch {
    return "";
  }
}

function markerPath() {
  return path.join(venvDir, ".cliptude-cli-version");
}

function importOk(py) {
  const check = spawnSync(py, ["-c", "import cliptude_mcp.cli"], {
    encoding: "utf8",
  });
  return check.status === 0;
}

function installedVersionMatches(py) {
  if (!importOk(py)) {
    return false;
  }
  const want = packageVersion();
  if (!want) {
    return true;
  }
  try {
    return fs.readFileSync(markerPath(), "utf8").trim() === want;
  } catch {
    return false;
  }
}

function run(py, argv) {
  return spawnSync(py, argv, { stdio: "inherit" });
}

function ensureVenv(systemPy) {
  const py = venvPython();
  if (fs.existsSync(py) && installedVersionMatches(py)) {
    return py;
  }

  fs.mkdirSync(path.dirname(venvDir), { recursive: true });
  if (!fs.existsSync(py)) {
    const created = run(systemPy, ["-m", "venv", venvDir]);
    if (created.status !== 0 || !fs.existsSync(py)) {
      console.error(
        "Could not create a Python virtualenv at " +
          venvDir +
          ". Install Python 3.10+.",
      );
      process.exit(created.status ?? 1);
    }
  }

  const pyproject = path.join(pkgRoot, "pyproject.toml");
  if (!fs.existsSync(pyproject)) {
    console.error("Cliptude CLI package is missing pyproject.toml.");
    process.exit(1);
  }

  let install = run(py, ["-m", "pip", "install", "--upgrade", "--quiet", pkgRoot]);
  if (install.status !== 0) {
    run(py, ["-m", "ensurepip", "--upgrade"]);
    install = run(py, ["-m", "pip", "install", "--upgrade", "--quiet", pkgRoot]);
  }
  if (install.status !== 0) {
    console.error("Could not install the Cliptude Python CLI into " + venvDir);
    process.exit(install.status ?? 1);
  }

  const version = packageVersion();
  if (version) {
    fs.writeFileSync(markerPath(), version + "\n");
  }
  return py;
}

const systemPy = findPython();
if (!systemPy) {
  console.error(
    "Python 3.10+ is required. Install it, then re-run: npm i -g @cliptude/cli",
  );
  process.exit(1);
}

const py = ensureVenv(systemPy);
const result = run(py, ["-m", "cliptude_mcp.cli", ...args]);
process.exit(result.status ?? 1);
