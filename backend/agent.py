"""Run the `finance-run` Claude Code workflow and parse its result."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from config import Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PROMPT = "/finance-run"
JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)

CLI = "backend/.venv/bin/python backend/finance_cli.py"

# Passed on the command line as well as declared in .claude/settings.json, because
# settings permissions are ignored until the workspace has been trusted interactively.
ALLOWED_TOOLS = (
    f"Bash({CLI} context:*)",
    f"Bash({CLI} send:*)",
    f"Bash({CLI} record:*)",
)


class AgentError(Exception):
    """The workflow could not be run, or returned something unusable."""


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the workflow's final JSON object out of its closing message."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [*reversed(fenced)]

    match = JSON_OBJECT_PATTERN.search(text)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise AgentError(f"Workflow did not return JSON. Output was: {text[:400]}")


def _with_hint(message: str, stderr_text: str) -> str:
    """Attach the fix for the two setup problems that produce confusing errors."""
    lowered = f"{message} {stderr_text}".lower()
    hints = []
    if "authenticate" in lowered or "oauth" in lowered:
        hints.append("Run `claude` in a terminal and sign in, then try again.")
    if "not been trusted" in lowered:
        hints.append(
            "Run `claude` once in this directory and accept the trust prompt so "
            ".claude/settings.json applies."
        )
    return " ".join([message, *hints]) if hints else message


async def run_workflow(settings: Settings | None = None) -> dict[str, Any]:
    """Invoke `claude -p /finance-run` headlessly and return its structured result."""
    settings = settings or get_settings()

    # No --bare: it forces API-key-only auth and would break an OAuth login.
    # Tool permissions come from .claude/settings.json, so anything the workflow is
    # not explicitly allowed to run is refused rather than prompted for.
    command = [
        settings.claude_bin,
        "-p",
        SKILL_PROMPT,
        "--output-format",
        "json",
        "--max-turns",
        "16",
        "--allowed-tools",
        *ALLOWED_TOOLS,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AgentError(
            f"`{settings.claude_bin}` was not found. Install Claude Code or set CLAUDE_BIN in .env."
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.claude_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AgentError(
            f"The workflow did not finish within {settings.claude_timeout_seconds:.0f}s."
        ) from exc

    raw = stdout.decode(errors="replace").strip()
    error_output = stderr.decode(errors="replace").strip()

    # --output-format json wraps the session; the closing message is in `result`.
    # Parse it before checking the exit code, because the envelope carries the real
    # reason a run failed while stderr often holds only an unrelated warning.
    envelope: Any = None
    if raw:
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            envelope = None

    if isinstance(envelope, dict):
        result = envelope.get("result")
        if envelope.get("is_error"):
            reason = str(result or "Claude Code reported an error.")
            raise AgentError(_with_hint(reason, error_output))
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            return _extract_json(result)

    if process.returncode != 0:
        detail = (error_output or raw or "no output").strip()
        raise AgentError(
            _with_hint(f"Claude Code exited with {process.returncode}: {detail[:400]}", error_output)
        )

    if not raw:
        raise AgentError("Claude Code produced no output.")

    return _extract_json(raw)
