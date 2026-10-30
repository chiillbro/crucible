"""Exception hierarchy.

Every failure Crucible can raise derives from :class:`CrucibleError` so callers can
catch the family or a specific, actionable subclass. The runner converts trial-level
failures into recorded results (reward 0 plus a reason) instead of letting them
abort a whole run; run-level failures such as a broken image build propagate.
"""


class CrucibleError(Exception):
    """Base class for all Crucible errors."""


class TaskLoadError(CrucibleError):
    """A task directory is missing files or its ``task.toml`` is invalid."""


class ConfigError(CrucibleError):
    """Harness configuration is missing or invalid (for example, no API key)."""


class SandboxError(CrucibleError):
    """The sandbox backend failed: daemon unreachable, container start failed, exec failed."""


class BuildError(SandboxError):
    """An image build failed or exceeded its timeout."""


class SandboxTimeoutError(SandboxError):
    """A command exceeded its deadline. The sandbox is killed and unusable afterwards."""


class AgentError(CrucibleError):
    """The agent could not run or crashed (API failure, malformed tool call, ...)."""


class VerifierError(CrucibleError):
    """The verifier could not produce a verdict.

    This is an infrastructure failure, not a failed test run. Failing hidden tests are a
    normal ``reward = 0`` verdict, never an exception.
    """
