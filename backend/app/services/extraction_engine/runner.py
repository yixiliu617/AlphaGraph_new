import uuid
from typing import Dict, Optional

from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.graph_repository import VectorRepository, GraphRepository

from backend.app.services.extraction_engine.steps.fetch_recipe import fetch_recipe
from backend.app.services.extraction_engine.steps.call_llm import call_llm
from backend.app.services.extraction_engine.steps.validate import validate_output
from backend.app.services.extraction_engine.steps.store_fragment import store_fragment
from backend.app.services.extraction_engine.steps.fanout import fanout_to_graph, fanout_to_ledger


class ExtractionRunner:
    """
    ORCHESTRATOR: Runs the five-step extraction pipeline in sequence.

    Each step is a standalone function in steps/ with a single responsibility.
    Changing any step (e.g. swapping the LLM, adding a new fanout target)
    does not touch the others.

    Pipeline:
        fetch_recipe → call_llm → validate_output → store_fragment → fanout
    """

    def __init__(
        self,
        db: DBRepository,
        llm: LLMProvider,
        vector_db: VectorRepository,
        graph_db: GraphRepository,
    ):
        self.db = db
        self.llm = llm
        self.vector_db = vector_db
        self.graph_db = graph_db

    def run_recipe_on_text(
        self,
        recipe_id: uuid.UUID,
        raw_text: str,
        source_info: Dict[str, str],
    ) -> Optional[uuid.UUID]:
        try:
            recipe   = fetch_recipe(recipe_id, self.db)
            raw_json = call_llm(recipe, raw_text, self.llm)
            fragment = validate_output(raw_json, recipe, source_info)

            store_fragment(fragment, self.db, self.llm, self.vector_db)
            fanout_to_graph(fragment, self.graph_db)
            fanout_to_ledger(fragment, self.db)

            print(f"Extraction & Fan-Out complete for Fragment: {fragment.fragment_id}")
            return fragment.fragment_id

        except Exception as e:
            print(f"Extraction Runner Error: {e}")
            return None
