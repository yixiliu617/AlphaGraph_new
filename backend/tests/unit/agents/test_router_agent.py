import pytest
from unittest.mock import MagicMock

from backend.app.agents.router_agent import UnifiedRouterAgent, RoutingPlan
from backend.app.agents.executors.executor_registry import ExecutorRegistry
from backend.app.models.api_contracts import AgentResponseBlock, ChatResponse


def _make_plan(**kwargs) -> dict:
    """Build a minimal raw plan dict as the LLM would return it."""
    defaults = {
        "requires_duckdb":    False,
        "duckdb_sql":         None,
        "requires_pinecone":  False,
        "pinecone_search_term": None,
        "reasoning":          "Test reasoning.",
    }
    return {**defaults, **kwargs}


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock(spec=ExecutorRegistry)
    registry.run_all.return_value = []
    return registry


@pytest.fixture
def agent(mock_llm, mock_registry) -> UnifiedRouterAgent:
    return UnifiedRouterAgent(llm=mock_llm, registry=mock_registry)


@pytest.mark.unit
class TestGeneratePlan:

    def test_calls_llm_structured_output(self, agent, mock_llm):
        mock_llm.generate_structured_output.return_value = _make_plan()
        agent._generate_plan("What is AAPL revenue?")
        mock_llm.generate_structured_output.assert_called_once()

    def test_returns_routing_plan_instance(self, agent, mock_llm):
        mock_llm.generate_structured_output.return_value = _make_plan()
        plan = agent._generate_plan("What is AAPL revenue?")
        assert isinstance(plan, RoutingPlan)

    def test_routing_schema_passed_to_llm(self, agent, mock_llm):
        mock_llm.generate_structured_output.return_value = _make_plan()
        agent._generate_plan("test query")
        schema = mock_llm.generate_structured_output.call_args.kwargs["output_schema"]
        # Schema should contain the RoutingPlan field names
        assert "requires_duckdb" in str(schema)
        assert "requires_pinecone" in str(schema)


@pytest.mark.unit
class TestProcessQuery:

    def test_returns_chat_response(self, agent, mock_llm):
        mock_llm.generate_structured_output.return_value = _make_plan()
        mock_llm.generate_response.return_value = "Here is my analysis."
        response = agent.process_query("Tell me about AAPL.")
        assert isinstance(response, ChatResponse)

    def test_no_executor_match_falls_back_to_llm_generate(
        self, agent, mock_llm, mock_registry
    ):
        mock_llm.generate_structured_output.return_value = _make_plan()
        mock_registry.run_all.return_value = []
        mock_llm.generate_response.return_value = "Fallback analysis."
        agent.process_query("General question.")
        mock_llm.generate_response.assert_called_once()

    def test_executor_blocks_included_in_response(
        self, agent, mock_llm, mock_registry
    ):
        mock_llm.generate_structured_output.return_value = _make_plan(requires_duckdb=True)
        chart_block = AgentResponseBlock(block_type="chart", title="Revenue Trend", data={})
        mock_registry.run_all.return_value = [chart_block]

        response = agent.process_query("Show AAPL revenue.")

        assert len(response.blocks) == 1
        assert response.blocks[0].block_type == "chart"

    def test_fallback_block_added_when_no_executors_run(
        self, agent, mock_llm, mock_registry
    ):
        mock_llm.generate_structured_output.return_value = _make_plan()
        mock_registry.run_all.return_value = []
        mock_llm.generate_response.return_value = "Fallback."

        response = agent.process_query("Explain the thesis.")

        assert len(response.blocks) == 1
        assert response.blocks[0].block_type == "text"

    def test_session_id_preserved_when_provided(self, agent, mock_llm):
        mock_llm.generate_structured_output.return_value = _make_plan()
        mock_llm.generate_response.return_value = "ok"
        response = agent.process_query("test", session_id="session-abc-123")
        assert response.session_id == "session-abc-123"

    def test_new_session_id_generated_when_none(self, agent, mock_llm):
        mock_llm.generate_structured_output.return_value = _make_plan()
        mock_llm.generate_response.return_value = "ok"
        response = agent.process_query("test", session_id=None)
        assert response.session_id is not None
        assert len(response.session_id) > 0

    def test_answer_includes_reasoning(self, agent, mock_llm, mock_registry):
        mock_llm.generate_structured_output.return_value = _make_plan(
            reasoning="Quantitative query detected."
        )
        mock_registry.run_all.return_value = []
        mock_llm.generate_response.return_value = "analysis"

        response = agent.process_query("AAPL revenue?")

        assert "Quantitative query detected." in response.answer

    def test_registry_run_all_called_with_plan(self, agent, mock_llm, mock_registry):
        mock_llm.generate_structured_output.return_value = _make_plan(requires_duckdb=True)
        mock_llm.generate_response.return_value = "ok"
        agent.process_query("Show metrics.")
        mock_registry.run_all.assert_called_once()
        plan_arg = mock_registry.run_all.call_args.args[0]
        assert isinstance(plan_arg, RoutingPlan)
