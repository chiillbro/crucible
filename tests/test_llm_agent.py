"""LlmAgent tool-loop tests against a scripted Messages client and the fake sandbox."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2 as httpx
import pytest
from anthropic.types import Message, MessageParam, TextBlock, ToolParam, ToolUseBlock, Usage

from crucible.adapters.agents.llm import TOOLS, LlmAgent
from crucible.deadline import Deadline
from crucible.domain.schema import Task, load_task
from crucible.errors import AgentError, SandboxTimeoutError
from crucible.ports.sandbox import ExecResult
from tests.conftest import TaskDirFactory
from tests.fakes import FakeSandbox, ok


def _message(*blocks: TextBlock | ToolUseBlock, stop_reason: str = "tool_use") -> Message:
    return Message(
        id="msg",
        type="message",
        role="assistant",
        model="fake",
        content=list(blocks),
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=5),
    )


def text(value: str) -> TextBlock:
    return TextBlock(type="text", text=value)


def tool(name: str, tool_id: str = "t1", **kwargs: Any) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=tool_id, name=name, input=kwargs)


@dataclass
class ScriptedClient:
    responses: list[Message | Exception]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        tools: Iterable[ToolParam],
        messages: Iterable[MessageParam],
    ) -> Message:
        self.calls.append(
            {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "tools": list(tools),
                "messages": [dict(m) for m in messages],
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def task(make_task_dir: TaskDirFactory) -> Task:
    return load_task(make_task_dir(instruction="Fix the parser."))


def _agent(client: ScriptedClient, **overrides: Any) -> LlmAgent:
    options: dict[str, Any] = {"model": "claude-test", "max_steps": 10, "max_tokens": 2048}
    options.update(overrides)
    return LlmAgent(client, **options)


def test_tools_are_strict_with_closed_schemas() -> None:
    assert [t["name"] for t in TOOLS] == ["bash", "write_file", "str_replace"]
    for tool_param in TOOLS:
        schema: dict[str, Any] = dict(tool_param["input_schema"])
        assert tool_param["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_bash_write_replace_then_done(task: Task) -> None:
    sandbox = FakeSandbox(
        handler=lambda cmd: (
            ok(stdout=f"ran: {cmd}\n") if cmd != "false" else ExecResult(1, "", "no", 0.0)
        ),
        files={"/workspace/mod.py": b"x = 1\ny = 2\n"},
    )
    client = ScriptedClient(
        [
            _message(text("Looking around."), tool("bash", "t1", command="ls")),
            _message(tool("write_file", "t2", path="new.py", content="print(1)\n")),
            _message(
                tool(
                    "str_replace", "t3", path="/workspace/mod.py", old_str="x = 1", new_str="x = 10"
                )
            ),
            _message(text("Done. Fixed x."), stop_reason="end_turn"),
        ]
    )
    outcome = _agent(client).solve(sandbox, task, Deadline.after(600))

    assert outcome.steps == 4
    assert outcome.stop_reason == "end_turn"
    assert outcome.summary == "Done. Fixed x."
    assert outcome.input_tokens == 40
    assert outcome.output_tokens == 20

    command, timeout, workdir = sandbox.commands[0]
    assert (command, workdir) == ("ls", "/workspace")
    assert timeout == 120  # command timeout, well inside the deadline
    assert sandbox.files["/workspace/new.py"] == b"print(1)\n"
    assert sandbox.files["/workspace/mod.py"] == b"x = 10\ny = 2\n"

    first = client.calls[0]
    assert first["model"] == "claude-test"
    assert first["max_tokens"] == 2048
    assert "/workspace" in first["system"]
    assert first["messages"] == [{"role": "user", "content": "Fix the parser."}]
    assert first["tools"] == TOOLS

    # Transcript grows append-only: user, assistant, tool results, ...
    last = client.calls[-1]["messages"]
    assert [m["role"] for m in last] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    bash_result = last[2]["content"][0]
    assert bash_result["tool_use_id"] == "t1"
    assert bash_result["content"] == "exit code: 0\nstdout:\nran: ls\n\nstderr:\n"
    assert "is_error" not in bash_result


def test_invalid_tool_calls_are_reported_to_the_model_not_fatal(task: Task) -> None:
    sandbox = FakeSandbox(files={"/workspace/a.py": b"aa"})
    client = ScriptedClient(
        [
            _message(
                tool("str_replace", "t1", path="a.py", old_str="a", new_str="b"),
                tool("str_replace", "t2", path="missing.py", old_str="a", new_str="b"),
                tool("bash", "t3", cmd="ls"),
                tool("teleport", "t4"),
            ),
            _message(text("giving up"), stop_reason="end_turn"),
        ]
    )
    outcome = _agent(client).solve(sandbox, task, Deadline.after(600))
    assert outcome.stop_reason == "end_turn"
    results = client.calls[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3", "t4"]
    assert all(r["is_error"] for r in results)
    assert "occurs 2 times" in results[0]["content"]
    assert "no such file" in results[1]["content"]
    assert "command" in results[2]["content"]
    assert "unknown tool 'teleport'" in results[3]["content"]
    assert sandbox.files["/workspace/a.py"] == b"aa"


def test_stops_at_max_steps(task: Task) -> None:
    client = ScriptedClient([_message(tool("bash", command="true")) for _ in range(3)])
    outcome = _agent(client, max_steps=3).solve(FakeSandbox(), task, Deadline.after(600))
    assert outcome.stop_reason == "max_steps"
    assert outcome.steps == 3
    assert len(client.calls) == 3


def test_stops_before_calling_model_when_deadline_expired(task: Task) -> None:
    client = ScriptedClient(
        [_message(tool("bash", command="true")), _message(text("x"), stop_reason="end_turn")]
    )
    clock = {"now": 0.0}
    deadline = Deadline.after(5, clock=lambda: clock["now"])
    sandbox = FakeSandbox()
    original = sandbox.exec

    def slow_exec(command: str, *, timeout: float, workdir: str | None = None) -> ExecResult:
        clock["now"] += 10
        return original(command, timeout=timeout, workdir=workdir)

    sandbox.exec = slow_exec  # type: ignore[method-assign]
    outcome = _agent(client).solve(sandbox, task, deadline)
    assert outcome.stop_reason == "deadline"
    assert outcome.steps == 1
    assert len(client.calls) == 1


def test_bash_timeout_is_clamped_to_deadline(task: Task) -> None:
    client = ScriptedClient(
        [_message(tool("bash", command="sleep 1")), _message(text("x"), stop_reason="end_turn")]
    )
    sandbox = FakeSandbox()
    _agent(client).solve(sandbox, task, Deadline.after(30))
    _, timeout, _ = sandbox.commands[0]
    assert 29 < timeout <= 30


def test_sandbox_timeout_propagates(task: Task) -> None:
    def hang(_cmd: str) -> ExecResult:
        raise SandboxTimeoutError("killed")

    client = ScriptedClient([_message(tool("bash", command="sleep 999"))])
    with pytest.raises(SandboxTimeoutError):
        _agent(client).solve(FakeSandbox(handler=hang), task, Deadline.after(30))


def test_refusal_and_max_tokens_stop_reasons(task: Task) -> None:
    refused = _agent(ScriptedClient([_message(stop_reason="refusal")])).solve(
        FakeSandbox(), task, Deadline.after(30)
    )
    assert refused.stop_reason == "refusal"
    truncated = _agent(ScriptedClient([_message(text("partial"), stop_reason="max_tokens")])).solve(
        FakeSandbox(), task, Deadline.after(30)
    )
    assert truncated.stop_reason == "max_tokens"
    assert truncated.summary == "partial"


def test_pause_turn_without_tools_continues(task: Task) -> None:
    client = ScriptedClient(
        [_message(stop_reason="pause_turn"), _message(text("ok"), stop_reason="end_turn")]
    )
    outcome = _agent(client).solve(FakeSandbox(), task, Deadline.after(30))
    assert outcome.stop_reason == "end_turn"
    assert len(client.calls) == 2


def test_api_errors_become_agent_errors(task: Task) -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIStatusError(
        "overloaded", response=httpx.Response(529, request=request), body=None
    )
    with pytest.raises(AgentError, match="request failed at step 1: overloaded"):
        _agent(ScriptedClient([error])).solve(FakeSandbox(), task, Deadline.after(30))


def test_real_sdk_client_satisfies_the_protocol() -> None:
    client: Any = anthropic.Anthropic(api_key="sk-test")
    LlmAgent(client.messages, model="m", max_steps=1, max_tokens=256)


def test_long_tool_output_is_truncated_in_the_middle(task: Task) -> None:
    sandbox = FakeSandbox(handler=lambda _c: ok(stdout="a" * 20_000 + "END"))
    client = ScriptedClient(
        [_message(tool("bash", command="cat big")), _message(text("x"), stop_reason="end_turn")]
    )
    _agent(client).solve(sandbox, task, Deadline.after(30))
    content = client.calls[1]["messages"][2]["content"][0]["content"]
    assert "[... truncated ...]" in content
    assert "END\nstderr:" in content
    assert len(content) < 17_000
