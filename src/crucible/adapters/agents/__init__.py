"""Agent adapters."""

from crucible.adapters.agents.llm import LlmAgent
from crucible.adapters.agents.nop import NopAgent
from crucible.adapters.agents.oracle import OracleAgent

__all__ = ["LlmAgent", "NopAgent", "OracleAgent"]
