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


class TestCodexReviewServer(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="codex-review-test-"))
        self.fake_script = self.temp_dir / "fake_codex.py"
        self.fake_cmd = self.temp_dir / "fake-codex.cmd"
        self.state_dir = self.temp_dir / "state"
        self.debug_log = self.temp_dir / "debug.log"

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
        self.assertEqual(env["CODEX_HOME"], str(Path(expected_home) / ".codex"))

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
