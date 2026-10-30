"""An LLM-driven agent: a minimal, auditable tool loop over the Claude Messages API.

Three tools are exposed to the model, all executed through the sandbox port so the
model never touches the host: ``bash``, ``write_file`` and ``str_replace``. The loop is
deliberately plain (no framework, no retries beyond the SDK's own) so every step is easy
to read in a transcript.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

import anthropic
from anthropic.types import Message, MessageParam, ToolParam, ToolResultBlockParam, ToolUseBlock
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from crucible.deadline import Deadline
from crucible.domain.schema import AgentOutcome, Task
from crucible.errors import AgentError, SandboxFileNotFoundError
from crucible.ports.sandbox import Sandbox

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a software engineer working alone inside an isolated Linux container.

Rules of the environment:
- Your working directory is {workdir}. The codebase you must change lives there.
- There is no network access. You cannot install packages or look anything up.
- You have three tools: `bash` runs a shell command in the working directory;
  `write_file` creates or overwrites a file; `str_replace` replaces one exact occurrence
  of a string in a file. Relative paths are resolved against the working directory.
- Only the state of the working directory when you stop is graded, by tests you cannot
  see. Visible tests, if any, are a subset of what will be checked.

Work method: read the relevant code before changing it, make the change, run the
project's tests, and stop when you are confident the task is complete. When you stop,
reply with a short summary of what you changed. Do not ask questions; nobody will answer.
"""

_TOOL_OUTPUT_LIMIT = 16_000


class BashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(description="Shell command to run with `sh -c` in the working directory.")


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path, absolute or relative to the working directory.")
    content: str = Field(description="Complete new contents of the file.")


class StrReplaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="File path, absolute or relative to the working directory.")
    old_str: str = Field(description="Exact text to replace. Must occur exactly once in the file.")
    new_str: str = Field(description="Replacement text.")


def _tool(name: str, description: str, model: type[BaseModel]) -> ToolParam:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema["properties"].values():
        prop.pop("title", None)
    return ToolParam(name=name, description=description, input_schema=schema, strict=True)


TOOLS: list[ToolParam] = [
    _tool("bash", "Run a shell command. Returns exit code, stdout and stderr.", BashInput),
    _tool("write_file", "Create or overwrite a file with the given content.", WriteFileInput),
    _tool(
        "str_replace",
        "Replace exactly one occurrence of old_str with new_str in a file.",
        StrReplaceInput,
    ),
]


class MessagesClient(Protocol):
    """The slice of ``anthropic.Anthropic().messages`` the agent uses; fakes implement it."""

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: Iterable[ToolParam],
        messages: Iterable[MessageParam],
    ) -> Message: ...


class LlmAgent:
    """Drives a Claude model through the tool loop until it stops, errs, or runs out of budget.

    Args:
        client: the Messages API (or a fake with the same ``create`` signature).
        model: model id; recorded in results so runs are attributable.
        max_steps: hard cap on model turns per trial.
        max_tokens: per-turn output cap.
        command_timeout: per-``bash`` call timeout, further clamped by the trial deadline.
    """

    def __init__(
        self,
        client: MessagesClient,
        *,
        model: str,
        max_steps: int,
        max_tokens: int,
        command_timeout: float = 120.0,
    ) -> None:
        self._client = client
        self._model = model
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        self._command_timeout = command_timeout

    @property
    def name(self) -> str:
        return "llm"

    def solve(self, sandbox: Sandbox, task: Task, deadline: Deadline) -> AgentOutcome:
        system = SYSTEM_PROMPT.format(workdir=task.workdir)
        messages: list[MessageParam] = [{"role": "user", "content": task.instruction}]
        input_tokens = output_tokens = 0

        for step in range(1, self._max_steps + 1):
            if deadline.expired():
                return self._outcome(step - 1, input_tokens, output_tokens, "deadline")
            try:
                response = self._client.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                )
            except anthropic.APIError as exc:
                msg = f"{self._model} request failed at step {step}: {exc}"
                raise AgentError(msg) from exc
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            log.debug("step %d: stop_reason=%s", step, response.stop_reason)

            if response.stop_reason == "refusal":
                return self._outcome(step, input_tokens, output_tokens, "refusal")
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "pause_turn":
                    continue
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                return self._outcome(
                    step, input_tokens, output_tokens, response.stop_reason or "end_turn", text
                )
            results = [
                self._run_tool(sandbox, task.workdir, block, deadline) for block in tool_uses
            ]
            messages.append({"role": "user", "content": results})

        return self._outcome(self._max_steps, input_tokens, output_tokens, "max_steps")

    def _run_tool(
        self, sandbox: Sandbox, workdir: str, block: ToolUseBlock, deadline: Deadline
    ) -> ToolResultBlockParam:
        try:
            text = self._dispatch(sandbox, workdir, block, deadline)
        except (ValidationError, ToolError, SandboxFileNotFoundError) as exc:
            log.debug("tool %s failed: %s", block.name, exc)
            return _tool_result(block.id, f"error: {exc}", is_error=True)
        return _tool_result(block.id, _truncate(text))

    def _dispatch(
        self, sandbox: Sandbox, workdir: str, block: ToolUseBlock, deadline: Deadline
    ) -> str:
        if block.name == "bash":
            bash = BashInput.model_validate(block.input)
            result = sandbox.exec(
                bash.command, timeout=deadline.clamp(self._command_timeout), workdir=workdir
            )
            return (
                f"exit code: {result.exit_code}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if block.name == "write_file":
            write = WriteFileInput.model_validate(block.input)
            sandbox.write_file(_resolve(workdir, write.path), write.content.encode())
            return f"wrote {write.path}"
        if block.name == "str_replace":
            replace = StrReplaceInput.model_validate(block.input)
            path = _resolve(workdir, replace.path)
            original = sandbox.read_file(path).decode("utf-8", errors="replace")
            count = original.count(replace.old_str)
            if count != 1:
                msg = f"old_str occurs {count} times in {replace.path}; it must occur exactly once"
                raise ToolError(msg)
            sandbox.write_file(path, original.replace(replace.old_str, replace.new_str, 1).encode())
            return f"edited {replace.path}"
        msg = f"unknown tool {block.name!r}"
        raise ToolError(msg)

    @staticmethod
    def _outcome(
        steps: int, input_tokens: int, output_tokens: int, stop_reason: str, summary: str = ""
    ) -> AgentOutcome:
        log.info("llm agent stopped after %d step(s): %s", steps, stop_reason)
        return AgentOutcome(
            steps=steps,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
            summary=summary or None,
        )


class ToolError(Exception):
    """A tool call the model made was invalid; reported back to the model, not fatal."""


def _tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> ToolResultBlockParam:
    result = ToolResultBlockParam(type="tool_result", tool_use_id=tool_use_id, content=content)
    if is_error:
        result["is_error"] = True
    return result


def _resolve(workdir: str, path: str) -> str:
    return path if path.startswith("/") else f"{workdir}/{path}"


def _truncate(text: str) -> str:
    if len(text) <= _TOOL_OUTPUT_LIMIT:
        return text
    half = _TOOL_OUTPUT_LIMIT // 2
    return text[:half] + "\n[... truncated ...]\n" + text[-half:]
