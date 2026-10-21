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

const MINOR_VERSIONS = [14, 13, 12, 11, 10];

function pythonCandidates() {
  const fromEnv = process.env.CLIPTUDE_PYTHON ? [process.env.CLIPTUDE_PYTHON] : [];
  if (process.platform === "win32") {
    return [...fromEnv, "py", "python", "python3"];
  }
  // macOS ships python3 3.9 at /usr/bin; a newer Homebrew, pyenv or python.org
  // install is usually only on PATH under its versioned name.
  const versioned = MINOR_VERSIONS.map((minor) => `python3.${minor}`);
  const dirs = ["/opt/homebrew/bin", "/usr/local/bin"];
  const absolute = dirs.flatMap((dir) =>
    versioned.map((name) => path.join(dir, name)),
  );
  return [...fromEnv, ...versioned, "python3", "python", ...absolute];
}

function isPython310(cmd) {
  const probe = spawnSync(
    cmd,
    ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
    { encoding: "utf8" },
  );
  return probe.status === 0;
}

function findPython() {
  return pythonCandidates().find(isPython310) || null;
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

function resolvePython() {
  // A working install needs no system Python at all.
  const existing = venvPython();
  if (fs.existsSync(existing) && installedVersionMatches(existing)) {
    return existing;
  }
  const systemPy = findPython();
  if (!systemPy) {
    console.error(
      "Python 3.10+ is required and none was found (tried " +
        pythonCandidates().slice(0, 7).join(", ") +
        ", ...). Install it, or point CLIPTUDE_PYTHON at one, then re-run: npm i -g @cliptude/cli",
    );
    process.exit(1);
  }
  return ensureVenv(systemPy);
}

const py = resolvePython();
const result = run(py, ["-m", "cliptude_mcp.cli", ...args]);
process.exit(result.status ?? 1);
