from backend.app.agents.executors.base import QueryExecutor
from backend.app.interfaces.quant_repository import QuantRepository
from backend.app.models.api_contracts import AgentResponseBlock


class DuckDBExecutor(QueryExecutor):
    """
    Executes structured SQL queries against the DuckDB/Parquet quant data lake.

    NOTE: This executor is superseded by DataAgentExecutor for the Engine agent.
    DataAgentExecutor routes through the topline/calculated layer via DataAgent,
    which is schema-aware and doesn't require LLM-generated SQL.

    DuckDBExecutor is retained for direct SQL use cases (e.g. ad-hoc debug queries).
    It is not registered in the Engine agent's ExecutorRegistry.
    """

    def __init__(self, quant: QuantRepository):
        self.quant = quant

    def can_handle(self, tool_call: dict) -> bool:
        # Not used by the Engine agent -- always returns False in the new tool-use system
        return False

    def execute(self, tool_call: dict) -> tuple:
        sql = tool_call.get("input", {}).get("sql", "")
        print(f"[DuckDBExecutor] Running SQL: {sql}")
        results = self.quant.execute_query(sql)
        block = AgentResponseBlock(
            block_type="chart",
            title="Quantitative Analysis",
            data=results,
        )
        return block, f"DuckDB query returned {len(results) if results else 0} rows."
