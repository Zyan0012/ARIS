#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


BUSY_NEEDLES = (
    "ERROR: busy",
    "User error (browser-automation): busy",
    "failed: busy",
    '"error":"busy"',
)
LOCK_PATH = Path(os.environ.get("ORACLE_SAFE_LOCK_PATH", str(Path.home() / ".oracle" / "oracle-safe.lock")))


def default_oracle_command() -> list[str]:
    oracle_on_path = shutil.which("oracle")
    if oracle_on_path:
        return [oracle_on_path]

    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    for version_dir in sorted(nvm_root.glob("v*"), key=nvm_version_key, reverse=True):
        oracle_bin = version_dir / "bin" / "oracle"
        if oracle_bin.exists():
            return [str(oracle_bin)]

        node = version_dir / "bin" / "node"
        cli = version_dir / "lib" / "node_modules" / "@steipete" / "oracle" / "dist" / "bin" / "oracle-cli.js"
        if node.exists() and cli.exists():
            return [str(node), str(cli)]

    npx_on_path = shutil.which("npx")
    if npx_on_path:
        return [npx_on_path, "-y", "@steipete/oracle"]

    return ["oracle"]


def nvm_version_key(path: Path) -> tuple[int, int, int]:
    match = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", path.name)
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def parse_wrapper_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--safe-wait-seconds", type=int, default=int(os.environ.get("ORACLE_SAFE_WAIT_SECONDS", "1800")))
    parser.add_argument("--safe-poll-seconds", type=int, default=int(os.environ.get("ORACLE_SAFE_POLL_SECONDS", "30")))
    parser.add_argument("--safe-oracle-bin", default=os.environ.get("ORACLE_SAFE_ORACLE_BIN", ""))
    parser.add_argument("-h", "--help", action="store_true")
    args, rest = parser.parse_known_args(argv)
    if args.help:
        print(
            "Usage: oracle-safe [--safe-wait-seconds N] [--safe-poll-seconds N] [oracle args...]\n"
            "Runs oracle. If browser mode reports busy, polls `oracle session <slug>` instead of retrying."
        )
        raise SystemExit(0)
    return args, rest


def extract_slug(args: list[str]) -> str:
    for idx, token in enumerate(args):
        if token == "--slug" and idx + 1 < len(args):
            return args[idx + 1]
        if token.startswith("--slug="):
            return token.split("=", 1)[1]
    return ""


def is_busy(text: str) -> bool:
    return any(needle in text for needle in BUSY_NEEDLES)


def chatgpt_appears_active() -> bool:
    port_file = Path("/mnt/c/Users/MX/.oracle/browser-profile/DevToolsActivePort")
    if not port_file.exists():
        return False
    try:
        port = port_file.read_text(encoding="utf-8").splitlines()[0].strip()
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=2)
        conn.request("GET", "/json")
        response = conn.getresponse()
        pages = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    if not isinstance(pages, list):
        return False
    return any(isinstance(page, dict) and "chatgpt.com/c/" in str(page.get("url", "")) for page in pages)


def wait_for_browser_idle(wait_seconds: int, poll_seconds: int) -> None:
    if wait_seconds <= 0:
        return
    deadline = time.time() + wait_seconds
    poll_seconds = max(1, poll_seconds)
    while chatgpt_appears_active() and time.time() < deadline:
        print("[oracle-safe] ChatGPT browser page is still active; waiting before starting a new run.")
        sys.stdout.flush()
        time.sleep(poll_seconds)


@contextlib.contextmanager
def oracle_safe_lock(wait_seconds: int, poll_seconds: int):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    poll_seconds = max(1, poll_seconds)
    deadline = time.time() + max(0, wait_seconds)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError(f"Timed out waiting for oracle-safe lock: {LOCK_PATH}")
                print(f"[oracle-safe] another Oracle browser run is queued/running; waiting for lock {LOCK_PATH}")
                sys.stdout.flush()
                time.sleep(poll_seconds)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_streaming(command: list[str]) -> tuple[int, str]:
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    return proc.wait(), "".join(chunks)


def read_session_meta(slug: str) -> dict:
    path = Path.home() / ".oracle" / "sessions" / slug / "meta.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def model_still_running(meta: dict) -> bool:
    models = meta.get("models")
    if not isinstance(models, list):
        return False
    return any(isinstance(item, dict) and item.get("status") == "running" for item in models)


def session_completed(meta: dict) -> bool:
    if meta.get("status") == "completed":
        return True
    return any(
        isinstance(item, dict) and item.get("status") == "completed"
        for item in meta.get("models", [])
        if isinstance(meta.get("models"), list)
    )


def oracle_command(override: str) -> list[str]:
    if override:
        return [override]
    return default_oracle_command()


def run_session_command(oracle_cmd: list[str], slug: str) -> tuple[int, str]:
    proc = subprocess.run([*oracle_cmd, "session", slug], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout


def poll_session(oracle_cmd: list[str], slug: str, wait_seconds: int, poll_seconds: int) -> int:
    deadline = time.time() + max(0, wait_seconds)
    poll_seconds = max(1, poll_seconds)
    print(f"\n[oracle-safe] Browser bridge is busy; polling existing session `{slug}` instead of retrying.")
    while True:
        meta = read_session_meta(slug)
        if session_completed(meta):
            code, output = run_session_command(oracle_cmd, slug)
            print("\n[oracle-safe] Session completed; returning stored Oracle result.\n")
            print(output)
            return 0 if code == 0 else code
        if meta and meta.get("status") == "error" and not model_still_running(meta):
            code, output = run_session_command(oracle_cmd, slug)
            print("\n[oracle-safe] Session is an error and no model is still running.\n")
            print(output)
            return code or 1
        if time.time() >= deadline:
            code, output = run_session_command(oracle_cmd, slug)
            print(f"\n[oracle-safe] Timed out waiting for `{slug}` after {wait_seconds}s.\n")
            print(output)
            return code or 124
        status = meta.get("status", "unknown") if meta else "not-created-yet"
        print(f"[oracle-safe] waiting: session={slug} status={status} model_running={model_still_running(meta)}")
        sys.stdout.flush()
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    wrapper_args, oracle_args = parse_wrapper_args(argv or sys.argv[1:])
    slug = extract_slug(oracle_args)
    oracle_cmd = oracle_command(wrapper_args.safe_oracle_bin)
    try:
        with oracle_safe_lock(wrapper_args.safe_wait_seconds, wrapper_args.safe_poll_seconds):
            wait_for_browser_idle(min(wrapper_args.safe_wait_seconds, 600), wrapper_args.safe_poll_seconds)
            code, output = run_streaming([*oracle_cmd, *oracle_args])
            if code == 0:
                return 0
            if not is_busy(output):
                return code
            if not slug:
                print("\n[oracle-safe] Oracle reported busy, but no --slug was provided, so there is no session to poll.")
                return code
            return poll_session(oracle_cmd, slug, wrapper_args.safe_wait_seconds, wrapper_args.safe_poll_seconds)
    except TimeoutError as error:
        print(f"[oracle-safe] {error}")
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
