#!/usr/bin/env python3
"""Codex review MCP server for Claude Code ARIS workflows."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ ships tomllib
    tomllib = None


sys.stdout = os.fdopen(sys.stdout.fileno(), "wb", buffering=0)
sys.stdin = os.fdopen(sys.stdin.fileno(), "rb", buffering=0)

SERVER_NAME = os.environ.get("CODEX_REVIEW_SERVER_NAME", "codex-review")
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
DEFAULT_MODEL = os.environ.get("CODEX_REVIEW_MODEL", "")
DEFAULT_SYSTEM = os.environ.get("CODEX_REVIEW_SYSTEM", "")
DEFAULT_TIMEOUT_SEC = int(os.environ.get("CODEX_REVIEW_TIMEOUT_SEC", "600"))
DEFAULT_REASONING = os.environ.get("CODEX_REVIEW_REASONING_EFFORT", "xhigh").strip()
DEFAULT_CODEX_HOME = os.environ.get("CODEX_REVIEW_CODEX_HOME", "").strip()
DEFAULT_HTTP_FALLBACK = os.environ.get("CODEX_REVIEW_HTTP_FALLBACK", "1").lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_DISABLE_FAST_MODE = os.environ.get("CODEX_REVIEW_DISABLE_FAST_MODE", "1").lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_SKIP_GIT_REPO_CHECK = os.environ.get("CODEX_REVIEW_SKIP_GIT_REPO_CHECK", "1").lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_EXTRA_ARGS = shlex.split(os.environ.get("CODEX_REVIEW_EXTRA_ARGS", ""))
DEBUG_LOG = Path(
    os.environ.get(
        "CODEX_REVIEW_DEBUG_LOG",
        str(Path.home() / ".claude" / "state" / SERVER_NAME / f"{SERVER_NAME}-debug.log"),
    )
)
STATE_DIR = Path(
    os.environ.get(
        "CODEX_REVIEW_STATE_DIR",
        str(Path.home() / ".claude" / "state" / SERVER_NAME),
    )
)
JOBS_DIR = STATE_DIR / "jobs"

SESSION_ID_RE = re.compile(r"session id:\s*([0-9a-fA-F-]{36})", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_use_ndjson = False
TERMINAL_JOB_STATES = {"completed", "failed"}


def debug_log(message: str) -> None:
    try:
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{message}\n")
    except OSError:
        pass


def send_response(response: dict[str, Any]) -> None:
    global _use_ndjson

    payload = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    debug_log(f"SEND {payload.decode('utf-8', errors='replace')}")
    if _use_ndjson:
        sys.stdout.write(payload + b"\n")
    else:
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8")
        sys.stdout.write(header + payload)
    sys.stdout.flush()


def read_message() -> dict[str, Any] | None:
    global _use_ndjson

    line = sys.stdin.readline()
    if not line:
        return None

    line_text = line.decode("utf-8").rstrip("\r\n")
    if line_text.lower().startswith("content-length:"):
        try:
            content_length = int(line_text.split(":", 1)[1].strip())
        except ValueError:
            return None

        while True:
            header_line = sys.stdin.readline()
            if not header_line:
                return None
            if header_line in {b"\r\n", b"\n"}:
                break

        body = sys.stdin.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    if line_text.startswith("{") or line_text.startswith("["):
        _use_ndjson = True
        try:
            return json.loads(line_text)
        except json.JSONDecodeError:
            return None

    return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def job_state_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def is_pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result") or {}
    return {
        "jobId": job.get("jobId"),
        "status": job.get("status"),
        "done": job.get("status") in TERMINAL_JOB_STATES,
        "threadId": result.get("threadId"),
        "response": result.get("response"),
        "model": result.get("model"),
        "backend": result.get("backend"),
        "duration_ms": result.get("duration_ms"),
        "stop_reason": result.get("stop_reason"),
        "error": job.get("error"),
        "createdAt": job.get("createdAt"),
        "startedAt": job.get("startedAt"),
        "completedAt": job.get("completedAt"),
        "updatedAt": job.get("updatedAt"),
        "resumeHint": "Call review_status with this jobId until done=true.",
    }


def find_codex_bin() -> str | None:
    if Path(CODEX_BIN).is_file():
        return CODEX_BIN
    return shutil.which(CODEX_BIN)


def normalize_image_paths(raw_value: Any) -> tuple[list[str], str | None]:
    if raw_value is None:
        return [], None
    if isinstance(raw_value, str):
        candidate = raw_value.strip()
        return ([candidate] if candidate else []), None
    if not isinstance(raw_value, list):
        return [], "imagePaths must be a string or an array of strings"

    image_paths: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            return [], "imagePaths entries must be strings"
        candidate = item.strip()
        if candidate:
            image_paths.append(candidate)
    return image_paths, None


def resolve_workdir(raw_value: Any) -> tuple[str | None, str | None]:
    if raw_value is None:
        return None, None
    path = Path(str(raw_value)).expanduser()
    if not path.exists():
        return None, f"cwd does not exist: {raw_value}"
    if not path.is_dir():
        return None, f"cwd is not a directory: {raw_value}"
    return str(path), None


def build_effective_prompt(prompt: str, system: str | None) -> str:
    selected_system = (system or DEFAULT_SYSTEM).strip()
    if not selected_system:
        return prompt
    return "\n".join(
        [
            "## Reviewer System Instructions",
            selected_system,
            "",
            "## User Prompt",
            prompt,
        ]
    ).strip()


def build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    home_dir = env.get("HOME") or str(Path.home())
    env["HOME"] = home_dir
    env["CODEX_HOME"] = DEFAULT_CODEX_HOME or env.get("CODEX_HOME") or str(Path(home_dir) / ".codex")
    return env


def codex_home_dir() -> Path:
    return Path(build_subprocess_env()["CODEX_HOME"]).expanduser()


def sanitize_text(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "").strip()


def extract_session_id(*chunks: str) -> str | None:
    for chunk in chunks:
        match = SESSION_ID_RE.search(chunk)
        if match:
            return match.group(1)
    return None


def extract_json_error(*chunks: str) -> str | None:
    for chunk in chunks:
        for line in reversed([item.strip() for item in chunk.splitlines() if item.strip()]):
            start = line.find("{")
            if start == -1:
                continue
            try:
                payload = json.loads(line[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            message = str(payload.get("message") or payload.get("error") or "").strip()
            code = str(payload.get("code") or "").strip()
            if message and code:
                return f"{code}: {message}"
            if message:
                return message
    return None


def extract_tail_message(*chunks: str) -> str:
    skip_prefixes = (
        "OpenAI Codex",
        "--------",
        "workdir:",
        "model:",
        "provider:",
        "approval:",
        "sandbox:",
        "reasoning effort:",
        "reasoning summaries:",
        "session id:",
        "user",
        "assistant",
        "warning:",
        "Reading additional input from stdin",
    )
    candidates: list[str] = []
    for chunk in chunks:
        for raw_line in sanitize_text(chunk).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(skip_prefixes):
                continue
            candidates.append(line)
    return candidates[-1] if candidates else "Codex review failed"


def extract_error_message(stdout: str, stderr: str, return_code: int) -> str:
    json_error = extract_json_error(stderr, stdout)
    if json_error:
        return json_error
    tail = extract_tail_message(stderr, stdout)
    if tail:
        return tail
    return f"Codex review failed with exit code {return_code}"


def load_toml_payload(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("tomllib is not available in this Python runtime")
    with path.open("rb") as fh:
        payload = tomllib.load(fh)
    if isinstance(payload, dict):
        return payload
    return {}


def load_codex_http_context(model_override: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    codex_home = codex_home_dir()
    config_path = codex_home / "config.toml"
    auth_path = codex_home / "auth.json"

    config: dict[str, Any] = {}
    if config_path.exists():
        try:
            config = load_toml_payload(config_path)
        except Exception as exc:
            return None, f"Failed to read Codex config.toml for HTTP fallback: {exc}"

    base_url = str(config.get("openai_base_url") or "").strip().rstrip("/")
    forced_login_method = str(config.get("forced_login_method") or "").strip().lower()
    selected_model = str(model_override or config.get("model") or DEFAULT_MODEL or "").strip()
    if not base_url:
        return None, "Codex HTTP fallback is unavailable because openai_base_url is not configured"
    if not auth_path.exists():
        return None, "Codex HTTP fallback is unavailable because auth.json is missing"

    try:
        auth_payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"Failed to read Codex auth.json for HTTP fallback: {exc}"
    if not isinstance(auth_payload, dict):
        return None, "Codex HTTP fallback is unavailable because auth.json has an unexpected format"

    api_key = str(
        auth_payload.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return None, "Codex HTTP fallback is unavailable because no OpenAI API key was found"

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": selected_model,
        "forced_login_method": forced_login_method,
        "auth_mode": str(auth_payload.get("auth_mode") or "").strip().lower(),
    }, None


def should_try_http_fallback(
    cli_error: str | None,
    stdout: str,
    stderr: str,
    http_context: dict[str, Any] | None,
) -> bool:
    if not DEFAULT_HTTP_FALLBACK or not http_context:
        return False

    base_url = str(http_context.get("base_url") or "")
    forced_login_method = str(http_context.get("forced_login_method") or "")
    if forced_login_method == "api" and "backend-api" in base_url:
        return True

    haystack = "\n".join([cli_error or "", stdout, stderr]).lower()
    fallback_markers = (
        "settlement_unknown_model",
        "responses_websocket",
        "failed to connect to websocket",
        "http error: 400 bad request",
        "codex cli not found",
    )
    return any(marker in haystack for marker in fallback_markers)


def normalize_responses_endpoint(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/responses"):
        return stripped
    return f"{stripped}/responses"


def image_path_to_data_url(image_path: str) -> tuple[str | None, str | None]:
    path = Path(image_path).expanduser()
    if not path.exists():
        return None, f"image path does not exist: {image_path}"
    if not path.is_file():
        return None, f"image path is not a file: {image_path}"

    mime_type, _ = mimetypes.guess_type(str(path))
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type or 'image/png'};base64,{payload}", None


def build_http_input(prompt: str, image_paths: list[str]) -> tuple[Any, str | None]:
    if not image_paths:
        return prompt, None

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for image_path in image_paths:
        data_url, error = image_path_to_data_url(image_path)
        if error:
            return None, error
        content.append({"type": "input_image", "image_url": data_url})
    return [{"role": "user", "content": content}], None


def extract_http_output_text(payload: dict[str, Any]) -> str:
    output_text = str(payload.get("output_text") or "").strip()
    if output_text:
        return output_text

    chunks: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "output_text":
                    text = str(block.get("text") or "").strip()
                    if text:
                        chunks.append(text)
    return "\n".join(chunks).strip()


def build_http_error_message(payload: dict[str, Any] | None, default_message: str) -> str:
    if not isinstance(payload, dict):
        return default_message
    message = str(payload.get("message") or payload.get("error") or "").strip()
    code = str(payload.get("code") or "").strip()
    if message and code:
        return f"{code}: {message}"
    if message:
        return message
    return default_message


def build_command(
    output_path: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    image_paths: list[str],
) -> list[str]:
    bin_path = find_codex_bin()
    if not bin_path:
        raise FileNotFoundError(f"Codex CLI not found: {CODEX_BIN}")

    cmd = [bin_path, "exec"]
    if session_id:
        cmd.append("resume")

    if DEFAULT_DISABLE_FAST_MODE:
        cmd.extend(["--disable", "fast_mode"])
    selected_model = model or DEFAULT_MODEL
    if selected_model:
        cmd.extend(["-m", selected_model])
    if DEFAULT_REASONING:
        cmd.extend(["-c", f'model_reasoning_effort="{DEFAULT_REASONING}"'])
    if DEFAULT_SKIP_GIT_REPO_CHECK:
        cmd.append("--skip-git-repo-check")
    cmd.extend(["--color", "never", "--output-last-message", output_path])
    for image_path in image_paths:
        cmd.extend(["-i", image_path])
    cmd.extend(DEFAULT_EXTRA_ARGS)
    if session_id:
        cmd.append(session_id)
    cmd.append("-")
    return cmd


def run_codex_cli_review(
    prompt: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    system: str | None = None,
    tools: str | None = None,
    image_paths: Any = None,
    cwd: Any = None,
) -> tuple[dict[str, Any] | None, str | None, str, str]:
    del tools

    normalized_image_paths, image_error = normalize_image_paths(image_paths)
    if image_error:
        return None, image_error, "", ""

    workdir, cwd_error = resolve_workdir(cwd)
    if cwd_error:
        return None, cwd_error, "", ""

    effective_prompt = build_effective_prompt(prompt, system)
    try:
        with tempfile.TemporaryDirectory(prefix=f"{SERVER_NAME}-") as temp_dir:
            output_path = os.path.join(temp_dir, "last-message.txt")
            try:
                cmd = build_command(
                    output_path,
                    session_id=session_id,
                    model=model,
                    image_paths=normalized_image_paths,
                )
            except FileNotFoundError as exc:
                return None, str(exc), "", ""

            debug_log(f"RUN {' '.join(cmd)}")
            started = time.monotonic()
            try:
                result = subprocess.run(
                    cmd,
                    input=effective_prompt,
                    capture_output=True,
                    text=True,
                    timeout=DEFAULT_TIMEOUT_SEC,
                    check=False,
                    cwd=workdir,
                    env=build_subprocess_env(),
                )
                duration_ms = int((time.monotonic() - started) * 1000)
            except subprocess.TimeoutExpired:
                return None, f"Codex review timed out after {DEFAULT_TIMEOUT_SEC} seconds", "", ""

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            thread_id = extract_session_id(stderr, stdout) or session_id

            if result.returncode != 0:
                error_message = extract_error_message(stdout, stderr, result.returncode)
                return None, error_message, stdout, stderr

            response_path = Path(output_path)
            if not response_path.exists():
                return None, "Codex review completed without producing an output-last-message file", stdout, stderr
            response_text = response_path.read_text(encoding="utf-8").strip()
            if not response_text:
                return None, "Codex review completed but returned an empty response", stdout, stderr

            return {
                "threadId": thread_id,
                "response": response_text,
                "model": model or DEFAULT_MODEL,
                "duration_ms": duration_ms,
                "stop_reason": None,
                "backend": "codex-cli",
            }, None, stdout, stderr
    except OSError as exc:
        return None, f"Failed to run Codex review: {exc}", "", ""


def run_http_review(
    prompt: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    system: str | None = None,
    tools: str | None = None,
    image_paths: Any = None,
    cwd: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    del tools
    del cwd

    normalized_image_paths, image_error = normalize_image_paths(image_paths)
    if image_error:
        return None, image_error

    context, context_error = load_codex_http_context(model)
    if context_error:
        return None, context_error
    if not context:
        return None, "Codex HTTP fallback context is unavailable"

    effective_prompt = build_effective_prompt(prompt, system)
    input_payload, input_error = build_http_input(effective_prompt, normalized_image_paths)
    if input_error:
        return None, input_error

    request_payload: dict[str, Any] = {
        "model": str(context.get("model") or model or DEFAULT_MODEL or "").strip(),
        "input": input_payload,
    }
    if session_id:
        request_payload["previous_response_id"] = session_id

    endpoint = normalize_responses_endpoint(str(context.get("base_url") or ""))
    headers = {
        "Authorization": f"Bearer {context['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    request = urllib_request.Request(
        endpoint,
        data=json.dumps(request_payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    started = time.monotonic()
    try:
        with urllib_request.urlopen(request, timeout=DEFAULT_TIMEOUT_SEC) as response:
            duration_ms = int((time.monotonic() - started) * 1000)
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(body)
        except json.JSONDecodeError:
            error_payload = None
        return None, build_http_error_message(
            error_payload,
            f"Codex HTTP fallback failed with status {exc.code}",
        )
    except urllib_error.URLError as exc:
        return None, f"Codex HTTP fallback failed: {exc.reason}"
    except TimeoutError:
        return None, f"Codex HTTP fallback timed out after {DEFAULT_TIMEOUT_SEC} seconds"
    except OSError as exc:
        return None, f"Codex HTTP fallback failed: {exc}"

    response_text = extract_http_output_text(response_payload)
    if not response_text:
        return None, "Codex HTTP fallback completed but returned an empty response"

    return {
        "threadId": str(response_payload.get("id") or session_id or ""),
        "response": response_text,
        "model": str(response_payload.get("model") or request_payload["model"]),
        "duration_ms": duration_ms,
        "stop_reason": response_payload.get("status"),
        "backend": "responses-http",
    }, None


def run_codex_review(
    prompt: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    system: str | None = None,
    tools: str | None = None,
    image_paths: Any = None,
    cwd: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    cli_payload, cli_error, stdout, stderr = run_codex_cli_review(
        prompt,
        session_id=session_id,
        model=model,
        system=system,
        tools=tools,
        image_paths=image_paths,
        cwd=cwd,
    )
    if cli_payload or not DEFAULT_HTTP_FALLBACK:
        return cli_payload, cli_error

    http_context, context_error = load_codex_http_context(model)
    if not should_try_http_fallback(cli_error, stdout, stderr, http_context):
        return cli_payload, cli_error
    if context_error:
        return cli_payload, cli_error or context_error

    debug_log(f"HTTP_FALLBACK reason={cli_error!r}")
    http_payload, http_error = run_http_review(
        prompt,
        session_id=session_id,
        model=model,
        system=system,
        tools=tools,
        image_paths=image_paths,
        cwd=cwd,
    )
    if http_error:
        combined_error = cli_error or "Codex review failed"
        return None, f"{combined_error}; HTTP fallback also failed: {http_error}"
    return http_payload, None


def start_async_review(
    prompt: str,
    *,
    session_id: str | None = None,
    model: str | None = None,
    system: str | None = None,
    tools: str | None = None,
    image_paths: Any = None,
    cwd: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    normalized_image_paths, image_error = normalize_image_paths(image_paths)
    if image_error:
        return None, image_error

    job_id = uuid.uuid4().hex
    created_at = utc_now()
    job = {
        "jobId": job_id,
        "status": "queued",
        "createdAt": created_at,
        "startedAt": None,
        "completedAt": None,
        "updatedAt": created_at,
        "error": None,
        "result": None,
        "workerPid": None,
        "request": {
            "prompt": prompt,
            "threadId": session_id,
            "model": model,
            "system": system,
            "tools": tools,
            "imagePaths": normalized_image_paths,
            "cwd": cwd,
        },
    }

    job_path = job_state_path(job_id)
    write_json(job_path, job)

    try:
        worker = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--run-job", job_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        job["status"] = "failed"
        job["completedAt"] = utc_now()
        job["updatedAt"] = job["completedAt"]
        job["error"] = f"Failed to launch background review worker: {exc}"
        write_json(job_path, job)
        return None, job["error"]

    job["workerPid"] = worker.pid
    job["updatedAt"] = utc_now()
    write_json(job_path, job)
    debug_log(f"JOB_START job_id={job_id} worker_pid={worker.pid}")
    return serialize_job(job), None


def get_review_status(job_id: str, *, wait_seconds: int = 0) -> tuple[dict[str, Any] | None, str | None]:
    job_path = job_state_path(job_id)
    if not job_path.exists():
        return None, f"Unknown jobId: {job_id}"

    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        job = read_json(job_path)
        if job.get("status") in {"queued", "running"} and not is_pid_alive(job.get("workerPid")):
            job["status"] = "failed"
            job["error"] = "Background review worker exited before writing a final result"
            job["completedAt"] = utc_now()
            job["updatedAt"] = job["completedAt"]
            write_json(job_path, job)
        if job.get("status") in TERMINAL_JOB_STATES:
            return serialize_job(job), None
        if time.monotonic() >= deadline:
            return serialize_job(job), None
        time.sleep(min(0.5, max(deadline - time.monotonic(), 0.0)))


def run_async_job(job_id: str) -> int:
    job_path = job_state_path(job_id)
    if not job_path.exists():
        debug_log(f"JOB_MISSING job_id={job_id}")
        return 1

    job = read_json(job_path)
    job["status"] = "running"
    job["startedAt"] = utc_now()
    job["updatedAt"] = job["startedAt"]
    job["workerPid"] = os.getpid()
    write_json(job_path, job)
    debug_log(f"JOB_RUNNING job_id={job_id} worker_pid={os.getpid()}")

    request = job.get("request") or {}
    try:
        payload, error = run_codex_review(
            str(request.get("prompt", "")),
            session_id=request.get("threadId"),
            model=request.get("model"),
            system=request.get("system"),
            tools=request.get("tools"),
            image_paths=request.get("imagePaths"),
            cwd=request.get("cwd"),
        )
    except Exception as exc:
        payload = None
        error = f"Background review crashed: {exc}"
        debug_log(traceback.format_exc())

    finished_at = utc_now()
    job = read_json(job_path)
    job["updatedAt"] = finished_at
    job["completedAt"] = finished_at
    if error:
        job["status"] = "failed"
        job["error"] = error
        job["result"] = None
        debug_log(f"JOB_FAILED job_id={job_id} error={error}")
        write_json(job_path, job)
        return 1

    job["status"] = "completed"
    job["error"] = None
    job["result"] = payload
    debug_log(f"JOB_COMPLETED job_id={job_id} thread_id={(payload or {}).get('threadId')}")
    write_json(job_path, job)
    return 0


def tool_success(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        },
    }


def tool_error(request_id: Any, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps({"error": message}, ensure_ascii=False)}],
            "isError": True,
        },
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})
    debug_log(f"REQUEST id={request_id!r} method={method} params={json.dumps(params, ensure_ascii=False)}")

    if request_id is None:
        if method in {"notifications/initialized", "initialized"}:
            return None
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}

    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resourceTemplates": []}}

    if method in {"notifications/initialized", "initialized"}:
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        common_properties = {
            "prompt": {"type": "string", "description": "Reviewer prompt"},
            "system": {"type": "string", "description": "Optional reviewer system instructions to prepend to the prompt"},
            "model": {"type": "string", "description": "Optional Codex model override"},
            "tools": {"type": "string", "description": "Accepted for compatibility but ignored by codex-review"},
            "cwd": {"type": "string", "description": "Optional working directory for the Codex subprocess"},
            "imagePaths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional local image paths to attach to the Codex review prompt",
            },
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Alias of imagePaths",
            },
        }
        reply_properties = {
            "threadId": {"type": "string", "description": "Codex session id from a previous review call"},
            "thread_id": {"type": "string", "description": "Alias of threadId"},
            **common_properties,
        }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "review",
                        "description": "Run a fresh Codex review and return JSON containing threadId and response.",
                        "inputSchema": {
                            "type": "object",
                            "properties": common_properties,
                            "required": ["prompt"],
                        },
                    },
                    {
                        "name": "review_reply",
                        "description": "Continue a previous Codex review session using threadId.",
                        "inputSchema": {
                            "type": "object",
                            "properties": reply_properties,
                            "required": ["prompt"],
                        },
                    },
                    {
                        "name": "review_start",
                        "description": "Start a background Codex review job and return a resumable jobId immediately.",
                        "inputSchema": {
                            "type": "object",
                            "properties": common_properties,
                            "required": ["prompt"],
                        },
                    },
                    {
                        "name": "review_reply_start",
                        "description": "Start a background follow-up review job in an existing Codex thread and return a resumable jobId immediately.",
                        "inputSchema": {
                            "type": "object",
                            "properties": reply_properties,
                            "required": ["prompt"],
                        },
                    },
                    {
                        "name": "review_status",
                        "description": "Check whether a background review job has finished and fetch the final result when available.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "jobId": {"type": "string", "description": "Background review job id"},
                                "job_id": {"type": "string", "description": "Alias of jobId"},
                                "waitSeconds": {"type": "integer", "description": "Optional bounded wait before returning status"},
                            },
                            "required": ["jobId"],
                        },
                    },
                ]
            },
        }

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}

        if name == "review":
            payload, error = run_codex_review(
                str(args.get("prompt", "")),
                model=args.get("model"),
                system=args.get("system"),
                tools=args.get("tools"),
                image_paths=args.get("imagePaths") or args.get("image_paths"),
                cwd=args.get("cwd"),
            )
            return tool_error(request_id, error) if error else tool_success(request_id, payload or {})

        if name == "review_reply":
            thread_id = args.get("threadId") or args.get("thread_id")
            if not thread_id:
                return tool_error(request_id, "threadId or thread_id is required")
            payload, error = run_codex_review(
                str(args.get("prompt", "")),
                session_id=str(thread_id),
                model=args.get("model"),
                system=args.get("system"),
                tools=args.get("tools"),
                image_paths=args.get("imagePaths") or args.get("image_paths"),
                cwd=args.get("cwd"),
            )
            return tool_error(request_id, error) if error else tool_success(request_id, payload or {})

        if name == "review_start":
            payload, error = start_async_review(
                str(args.get("prompt", "")),
                model=args.get("model"),
                system=args.get("system"),
                tools=args.get("tools"),
                image_paths=args.get("imagePaths") or args.get("image_paths"),
                cwd=args.get("cwd"),
            )
            return tool_error(request_id, error) if error else tool_success(request_id, payload or {})

        if name == "review_reply_start":
            thread_id = args.get("threadId") or args.get("thread_id")
            if not thread_id:
                return tool_error(request_id, "threadId or thread_id is required")
            payload, error = start_async_review(
                str(args.get("prompt", "")),
                session_id=str(thread_id),
                model=args.get("model"),
                system=args.get("system"),
                tools=args.get("tools"),
                image_paths=args.get("imagePaths") or args.get("image_paths"),
                cwd=args.get("cwd"),
            )
            return tool_error(request_id, error) if error else tool_success(request_id, payload or {})

        if name == "review_status":
            job_id = args.get("jobId") or args.get("job_id")
            if not job_id:
                return tool_error(request_id, "jobId or job_id is required")
            wait_seconds_raw = args.get("waitSeconds", 0)
            try:
                wait_seconds = int(wait_seconds_raw)
            except (TypeError, ValueError):
                return tool_error(request_id, "waitSeconds must be an integer")
            payload, error = get_review_status(str(job_id), wait_seconds=max(wait_seconds, 0))
            return tool_error(request_id, error) if error else tool_success(request_id, payload or {})

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Unknown tool: {name}"},
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--run-job":
        raise SystemExit(run_async_job(sys.argv[2]))

    debug_log(f"=== {SERVER_NAME} starting ===")
    while True:
        try:
            request = read_message()
            if request is None:
                debug_log("EOF")
                break
            response = handle_request(request)
            if response is not None:
                send_response(response)
        except Exception:
            debug_log(traceback.format_exc())
            break


if __name__ == "__main__":
    main()
