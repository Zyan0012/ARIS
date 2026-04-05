#!/usr/bin/env python3
"""Unit tests for the codex-review MCP server logic."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp-servers" / "codex-review" / "server.py"
FAKE_SESSION_ID = "11111111-1111-1111-1111-111111111111"


def load_server_module() -> types.ModuleType:
    source = SERVER_PATH.read_text(encoding="utf-8")
    source = source.replace('sys.stdout = os.fdopen(sys.stdout.fileno(), "wb", buffering=0)\n', "")
    source = source.replace('sys.stdin = os.fdopen(sys.stdin.fileno(), "rb", buffering=0)\n', "")
    module = types.ModuleType("codex_review_test_module")
    module.__file__ = str(SERVER_PATH)
    exec(compile(source, str(SERVER_PATH), "exec"), module.__dict__)
    return module


class FakeWorker:
    def __init__(self, pid: int = 43210):
        self.pid = pid


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object], status: int = 200):
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class TestCodexReviewServer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="codex-review-test-"))
        self.fake_script = self.temp_dir / "fake_codex.py"
        self.fake_cmd = self.temp_dir / "fake-codex.cmd"
        self.codex_home = self.temp_dir / "codex-home"
        self.state_dir = self.temp_dir / "state"
        self.debug_log = self.temp_dir / "debug.log"

        self.codex_home.mkdir(parents=True, exist_ok=True)
        (self.codex_home / "config.toml").write_text(
            'model = "test-model"\nforced_login_method = "api"\nopenai_base_url = "https://example.test/codex"\n',
            encoding="utf-8",
        )
        (self.codex_home / "auth.json").write_text(
            json.dumps({"OPENAI_API_KEY": "sk-test-123", "auth_mode": "apikey"}),
            encoding="utf-8",
        )

        self.fake_script.write_text(
            textwrap.dedent(
                f"""\
                import json
                import sys
                from pathlib import Path

                FAKE_SESSION_ID = "{FAKE_SESSION_ID}"

                args = sys.argv[1:]
                assert args and args[0] == "exec", args
                mode = "fresh"
                session_id = FAKE_SESSION_ID
                if len(args) > 1 and args[1] == "resume":
                    mode = "resume"
                    session_id = args[-2]
                output_path = None
                image_count = 0
                i = 0
                while i < len(args):
                    token = args[i]
                    if token == "--output-last-message":
                        output_path = args[i + 1]
                        i += 2
                        continue
                    if token == "-i":
                        image_count += 1
                        i += 2
                        continue
                    i += 1

                prompt = sys.stdin.read()
                print("OpenAI Codex vtest", file=sys.stderr)
                print(f"session id: {{session_id}}", file=sys.stderr)

                if "FAIL" in prompt:
                    print(json.dumps({{"error": "Settlement blocked", "message": "provider failure", "code": "SETTLEMENT_UNKNOWN_MODEL"}}), file=sys.stderr)
                    sys.exit(17)

                response = f"{{mode}}:{{prompt.strip()}}"
                if image_count:
                    response += f" [images={{image_count}}]"
                Path(output_path).write_text(response, encoding="utf-8")
                sys.exit(0)
                """
            ),
            encoding="utf-8",
        )
        self.fake_cmd.write_text(
            f'@echo off\r\n"{sys.executable}" "{self.fake_script}" %*\r\n',
            encoding="utf-8",
        )

        self.env_patch = patch.dict(
            os.environ,
            {
                "CODEX_BIN": str(self.fake_cmd),
                "CODEX_REVIEW_STATE_DIR": str(self.state_dir),
                "CODEX_REVIEW_DEBUG_LOG": str(self.debug_log),
                "CODEX_REVIEW_MODEL": "test-model",
                "CODEX_REVIEW_CODEX_HOME": str(self.codex_home),
                "CODEX_REVIEW_DISABLE_FAST_MODE": "0",
            },
            clear=False,
        )
        self.env_patch.start()
        self.server = load_server_module()

    def tearDown(self) -> None:
        self.env_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialize_and_tools_list(self) -> None:
        init = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        self.assertEqual(init["result"]["serverInfo"]["name"], "codex-review")

        tools = self.server.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        tool_names = [tool["name"] for tool in tools["result"]["tools"]]
        self.assertEqual(
            tool_names,
            ["review", "review_reply", "review_start", "review_reply_start", "review_status"],
        )

    def test_sync_review_and_reply(self) -> None:
        payload, error = self.server.run_codex_review("hello world")
        self.assertIsNone(error)
        self.assertEqual(payload["threadId"], FAKE_SESSION_ID)
        self.assertEqual(payload["response"], "fresh:hello world")

        reply_payload, error = self.server.run_codex_review(
            "follow up",
            session_id=payload["threadId"],
            image_paths=["img1.png", "img2.png"],
        )
        self.assertIsNone(error)
        self.assertEqual(reply_payload["threadId"], FAKE_SESSION_ID)
        self.assertEqual(reply_payload["response"], "resume:follow up [images=2]")

    def test_sync_error_is_surfaceable(self) -> None:
        payload, error = self.server.run_codex_review("please FAIL now")
        self.assertIsNone(payload)
        self.assertIsNotNone(error)
        self.assertIn("SETTLEMENT_UNKNOWN_MODEL", error)

    def test_subprocess_env_defaults_to_wsl_codex_home(self) -> None:
        env = self.server.build_subprocess_env()
        expected_home = os.environ.get("HOME") or str(Path.home())
        self.assertEqual(env["HOME"], expected_home)
        self.assertEqual(env["CODEX_HOME"], str(self.codex_home))

    def test_load_codex_http_context_reads_temp_codex_home(self) -> None:
        context, error = self.server.load_codex_http_context()
        self.assertIsNone(error)
        self.assertEqual(context["base_url"], "https://example.test/codex")
        self.assertEqual(context["api_key"], "sk-test-123")
        self.assertEqual(context["model"], "test-model")

    def test_http_fallback_after_cli_failure(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["auth"] = request.headers.get("Authorization")
            return FakeHttpResponse(
                {
                    "id": "resp_test_123",
                    "model": "test-model",
                    "output": [
                        {
                            "content": [
                                {"type": "output_text", "text": "fallback ok"},
                            ]
                        }
                    ],
                    "status": "completed",
                }
            )

        with patch.object(
            self.server,
            "run_codex_cli_review",
            return_value=(None, "SETTLEMENT_UNKNOWN_MODEL: provider failure", "", "failed to connect to websocket"),
        ), patch.object(self.server.urllib_request, "urlopen", side_effect=fake_urlopen):
            payload, error = self.server.run_codex_review("hello via fallback", session_id="resp_prev_456")

        self.assertIsNone(error)
        self.assertEqual(payload["threadId"], "resp_test_123")
        self.assertEqual(payload["response"], "fallback ok")
        self.assertEqual(payload["backend"], "responses-http")
        self.assertEqual(captured["url"], "https://example.test/codex/responses")
        self.assertEqual(captured["auth"], "Bearer sk-test-123")
        self.assertEqual(captured["body"]["previous_response_id"], "resp_prev_456")
        self.assertEqual(captured["body"]["input"], "hello via fallback")

    def test_async_review_lifecycle(self) -> None:
        with patch.object(self.server.subprocess, "Popen", return_value=FakeWorker()):
            payload, error = self.server.start_async_review("long review")
        self.assertIsNone(error)
        self.assertEqual(payload["status"], "queued")
        job_id = payload["jobId"]

        rc = self.server.run_async_job(job_id)
        self.assertEqual(rc, 0)

        status_payload, error = self.server.get_review_status(job_id, wait_seconds=0)
        self.assertIsNone(error)
        self.assertTrue(status_payload["done"])
        self.assertEqual(status_payload["threadId"], FAKE_SESSION_ID)
        self.assertEqual(status_payload["response"], "fresh:long review")

    def test_handle_request_tool_call(self) -> None:
        response = self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "review",
                    "arguments": {"prompt": "hello via tool"},
                },
            }
        )
        self.assertFalse(response["result"].get("isError", False))
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["response"], "fresh:hello via tool")


if __name__ == "__main__":
    unittest.main()
