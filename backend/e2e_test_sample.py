from backend.app.models.domain.data_fragment import DataFragment, TenantTier, SourceType
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.services.extraction_engine.runner import ExtractionRunner
from backend.app.adapters.llm.gemini_adapter import GeminiAdapter
from backend.app.adapters.db.postgres_adapter import PostgresAdapter
from backend.app.adapters.vector.pinecone_adapter import PineconeAdapter
from backend.app.adapters.db.duckdb_adapter import DuckDBAdapter
from backend.app.agents.router_agent import UnifiedRouterAgent
import uuid

# --- SETUP MOCK DATA ---

TENANT_ID = "institutional-alpha-1"

# 1. Create a real-world Extraction Recipe (The 'Recipe-as-Data' approach)
recipe = ExtractionRecipe(
    tenant_id=TENANT_ID,
    name="SEC 10-K Revenue Logic",
    target_sectors=["Technology"],
    ingestor_type="SEC_XBRL",
    llm_prompt_template="""
        Extract the following from the SEC text:
        1. Total Revenue for the most recent fiscal year.
        2. Any mention of revenue growth percentage.
        3. The primary risk factor mentioned for revenue.
    """,
    expected_schema={
        "type": "object",
        "properties": {
            "revenue": {"type": "number", "description": "Absolute revenue in USD"},
            "revenue_growth_pct": {"type": "number", "description": "YoY growth percentage"},
            "primary_risk": {"type": "string", "description": "Primary revenue risk factor"}
        },
        "required": ["revenue"]
    }
)

print(f"--- CREATED SAMPLE RECIPE ---")
print(f"ID: {recipe.recipe_id}")
print(f"Schema: {recipe.expected_schema}")

# 2. Sample Raw Data (Simulated 10-K snippet)
SAMPLE_10K_TEXT = """
For the fiscal year ended December 31, 2023, our total revenue was $55.3 billion, 
representing a 12% increase compared to $49.4 billion in 2022. 
While we saw strong demand, macroeconomic uncertainty and potential supply chain 
disruptions remain the primary risks to our revenue outlook for 2024.
"""

print(f"\n--- SIMULATING E2E INGESTION ---")
print(f"Processing snippet for {TENANT_ID}...")

# 3. Running the pipeline would look like this (Pseudo-code as we need real API keys for Gemini/Pinecone):
"""
runner = ExtractionRunner(db=postgres, llm=gemini, vector_db=pinecone)
fragment_id = runner.run_recipe_on_text(
    recipe_id=recipe.recipe_id,
    raw_text=SAMPLE_10K_TEXT,
    source_info={
        "type": "sec_filing",
        "name": "AAPL_10K_2023.pdf",
        "location": "Page 42"
    }
)
"""

print(f"\n--- PIPELINE READY ---")
print(f"The 'Firewall' is set up. Data will be transformed from text into validated JSON DataFragments.")
