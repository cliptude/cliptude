#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const pkgRoot = path.resolve(__dirname, "..", "..");
const args = process.argv.slice(2);

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

function ensurePackage(py) {
  const check = spawnSync(py, ["-c", "import cliptude_mcp.cli"], {
    encoding: "utf8",
  });
  if (check.status === 0) {
    return;
  }
  const pyproject = path.join(pkgRoot, "pyproject.toml");
  if (!fs.existsSync(pyproject)) {
    console.error("Cliptude CLI package is missing pyproject.toml.");
    process.exit(1);
  }
  const install = spawnSync(
    py,
    ["-m", "pip", "install", "--quiet", pkgRoot],
    { stdio: "inherit" },
  );
  if (install.status !== 0) {
    console.error("Could not install the Cliptude Python CLI. Install Python 3.10+ and pip.");
    process.exit(install.status ?? 1);
  }
}

const py = findPython();
if (!py) {
  console.error("Python 3.10+ is required. Install it, then re-run: npm i -g @cliptude/cli");
  process.exit(1);
}

ensurePackage(py);
const result = spawnSync(py, ["-m", "cliptude_mcp.cli", ...args], {
  stdio: "inherit",
});
process.exit(result.status ?? 1);
