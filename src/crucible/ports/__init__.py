"""Ports: the abstractions the core depends on. Adapters implement them.

Each port is a :class:`typing.Protocol`, so any object with the right shape satisfies
it; adapters need not inherit from anything in this package.
"""

from crucible.ports.agent import Agent
from crucible.ports.sandbox import ExecResult, ImageRef, Sandbox, SandboxBackend, WorkspaceSnapshot
from crucible.ports.verifier import Verifier

__all__ = [
    "Agent",
    "ExecResult",
    "ImageRef",
    "Sandbox",
    "SandboxBackend",
    "Verifier",
    "WorkspaceSnapshot",
]
