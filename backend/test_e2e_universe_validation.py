import uuid
import os
import sys

# Ensure the project root is in the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.extraction_engine.runner import ExtractionRunner
from backend.app.api.dependencies import get_db_repo, get_llm_provider, get_vector_repo, get_graph_repo
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.db.session import SessionLocal, init_db
from backend.app.models.domain.universe import UniverseFilter

TENANT_ID = "institutional-alpha-1"

def validate_e2e_universe():
    # 0. Ensure tables exist
    try:
        init_db()
    except Exception as e:
        print(f"Database Initialization Error: {e}")

    db_session = SessionLocal()
    db_repo = get_db_repo(db_session)
    
    # 1. Initialize Adapters with error handling
    try:
        llm = get_llm_provider()
        has_llm = True
    except Exception as e:
        print(f"Warning: LLM Provider not available: {e}")
        has_llm = False

    try:
        vector = get_vector_repo()
        has_vector = True
    except Exception as e:
        print(f"Warning: Vector Repository not available: {e}")
        has_vector = False

    try:
        graph = get_graph_repo()
        has_graph = True
    except Exception as e:
        print(f"Warning: Graph Repository not available: {e}")
        has_graph = False
    
    # Mocking for demo purposes if services are down
    if not has_llm or not has_vector or not has_graph:
        print("\n--- RUNNING IN MOCK MODE (Offline) ---")
        # In a real test, we'd use proper mocks here.
        # For this demo, we'll just skip the parts that require live connections.

    if has_llm and has_vector and has_graph:
        runner = ExtractionRunner(db=db_repo, llm=llm, vector_db=vector, graph_db=graph)
    else:
        print("\nSkipping E2E execution: Required AI/Vector/Graph services are not configured or available.")
        db_session.close()
        return

    # 2. Ensure the Master Recipe exists
    recipe = ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="Universe Validation Recipe",
        ingestor_type="TEST",
        llm_prompt_template="Extract company relationships. Focus on NVDA and its partners.",
        expected_schema={
            "type": "object",
            "properties": {
                "entity_name": {"type": "string"},
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_entity": {"type": "string"},
                            "relationship_type": {"type": "string"},
                            "context": {"type": "string"},
                            "direction": {"type": "string"}
                        }
                    }
                }
            }
        }
    )
    db_repo.save_recipe(recipe)

    # 2. Simulate Ingestion of a partner report
    # Content mentions NVDA as a customer
    raw_text = "TSM is a key foundry partner for NVDA, providing high-end CoWoS packaging."
    source_info = {"name": "TSM_Partner_Report.pdf", "type": "broker_report", "location": "p1"}

    print(f"--- Running E2E Ingestion for {TENANT_ID} ---")
    fragment_id = runner.run_recipe_on_text(
        recipe_id=recipe.recipe_id,
        raw_text=raw_text,
        source_info=source_info
    )

    if fragment_id:
        print(f"Success: Fragment {fragment_id} created and fanned out.")
        
        # 3. Verify Filtered Topology via direct Adapter call (Simulating API)
        # We want to see NVDA's neighbors but ONLY if they are in the 'Semiconductors' subsector.
        filters = UniverseFilter(subsectors=["Semiconductors"])
        
        print("\n--- Verifying Filtered Topology Display ---")
        # In a real test, TSM should show up because it's in our public_universe.csv as Semiconductors
        neighbors = graph.get_filtered_neighbors("NVDA", filters.model_dump())
        
        print(f"Found {len(neighbors)} neighbors for NVDA with 'Semiconductors' filter.")
        for n in neighbors:
            print(f"Neighbor: {n['id']} | Rel: {n['relationship']}")

    db_session.close()
    graph.close()

if __name__ == "__main__":
    validate_e2e_universe()
