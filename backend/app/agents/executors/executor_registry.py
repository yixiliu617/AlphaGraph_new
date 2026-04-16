from typing import List
from backend.app.agents.executors.base import QueryExecutor
from backend.app.models.api_contracts import AgentResponseBlock


class ExecutorRegistry:
    """
    Registry that holds all registered QueryExecutors and dispatches
    tool calls to the matching executor.

    To add a new data source:
      1. Create <Name>Executor(QueryExecutor) in executors/
      2. Register it in dependencies.py
      3. Zero changes here or to existing executors.

    run_all() returns (blocks, summaries):
      blocks    -- AgentResponseBlocks sent directly to the frontend
      summaries -- compact text summaries returned to Claude for synthesis
                   (full data is never passed back through the LLM)
    """

    def __init__(self, executors: List[QueryExecutor]):
        self._executors = executors

    def run_all(
        self,
        tool_calls: list,
    ) -> tuple:
        """
        Dispatch each tool call to its matching executor.
        Returns (blocks: list[AgentResponseBlock], summaries: list[str]).
        """
        blocks: list[AgentResponseBlock] = []
        summaries: list[str] = []

        for tool_call in tool_calls:
            for executor in self._executors:
                if executor.can_handle(tool_call):
                    block, summary = executor.execute(tool_call)
                    blocks.append(block)
                    summaries.append(summary)
                    break  # one executor per tool call

        return blocks, summaries
