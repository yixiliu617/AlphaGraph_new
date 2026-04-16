from abc import ABC, abstractmethod
from backend.app.models.api_contracts import AgentResponseBlock


class QueryExecutor(ABC):
    """
    PORT: Abstract base for a single query executor.

    Each executor owns exactly one data-source strategy (DataAgent, Pinecone, ...).
    Adding a new data source = add a new subclass, register it in dependencies.py --
    zero changes to existing executors or the EngineAgent.

    tool_call format: {"name": str, "input": dict}
      Matches the Anthropic tool-use response shape directly.

    execute() returns (block, summary_for_llm):
      block           -- AgentResponseBlock sent to the frontend as a rendered card
      summary_for_llm -- compact text (~30 tokens) passed back to Claude for synthesis.
                         Full data never flows back through the LLM -- token efficiency.
    """

    @abstractmethod
    def can_handle(self, tool_call: dict) -> bool:
        """Return True if this executor should run for the given tool call."""
        ...

    @abstractmethod
    def execute(self, tool_call: dict) -> tuple:
        """
        Execute the tool call.
        Returns (AgentResponseBlock, summary_str).
        """
        ...
