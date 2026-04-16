import os
import uuid
import sys

# Ensure the project root is in the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.extraction_engine.runner import ExtractionRunner
from backend.app.api.dependencies import get_db_repo, get_llm_provider, get_vector_repo, get_graph_repo
from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.db.session import SessionLocal, init_db

# --- CONFIGURATION ---
FILINGS_DIR = r"C:\Users\User\GeminiCLI\data"
NOTES_DIR = r"C:\Users\User\Documents\Financial_Files\Notes"
TENANT_ID = "institutional-alpha-1"

def bootstrap_test():
    # 0. Ensure tables exist
    try:
        init_db()
    except Exception as e:
        print(f"Database Initialization Error: {e}")

    # Initialize DB Session
    db_session = SessionLocal()
    
    # 1. Initialize Adapters & Services via our DI logic
    db_repo = get_db_repo(db_session)
    llm_provider = get_llm_provider()
    vector_repo = get_vector_repo()
    graph_repo = get_graph_repo()
    
    runner = ExtractionRunner(
        db=db_repo, 
        llm=llm_provider, 
        vector_db=vector_repo, 
        graph_db=graph_repo
    )

    # 2. Create the SEC Filing Recipe
    sec_recipe = ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="Institutional SEC Extractor",
        target_sectors=["All"],
        ingestor_type="SEC_TEXT",
        llm_prompt_template="Extract Revenue, Net Income, and the primary Risk Factor from this filing snippet.",
        expected_schema={
            "type": "object",
            "properties": {
                "revenue": {"type": "number"},
                "net_income": {"type": "number"},
                "primary_risk": {"type": "string"}
            }
        }
    )
    db_repo.save_recipe(sec_recipe)

    # 3. Create the Notes Recipe
    notes_recipe = ExtractionRecipe(
        tenant_id=TENANT_ID,
        name="Proprietary Notes Extractor",
        target_sectors=["Internal"],
        ingestor_type="USER_NOTES",
        llm_prompt_template="Extract the Tickers mentioned and the overall Sentiment (Bullish/Bearish) from these notes.",
        expected_schema={
            "type": "object",
            "properties": {
                "tickers": {"type": "array", "items": {"type": "string"}},
                "sentiment": {"type": "string", "enum": ["Bullish", "Bearish", "Neutral"]}
            }
        }
    )
    db_repo.save_recipe(notes_recipe)

    # 4. Ingest Filings
    print(f"\n--- Ingesting Filings from {FILINGS_DIR} ---")
    if os.path.exists(FILINGS_DIR):
        for file_name in os.listdir(FILINGS_DIR):
            if file_name.endswith((".txt", ".html", ".pdf")):
                file_path = os.path.join(FILINGS_DIR, file_name)
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()[:5000] # Process first 5000 chars for test
                    runner.run_recipe_on_text(
                        recipe_id=sec_recipe.recipe_id,
                        raw_text=content,
                        source_info={"name": file_name, "type": "sec_filing", "location": "Full Document"}
                    )
    else:
        print(f"Warning: Filings directory {FILINGS_DIR} not found.")

    # 5. Ingest Notes
    print(f"\n--- Ingesting Notes from {NOTES_DIR} ---")
    if os.path.exists(NOTES_DIR):
        for file_name in os.listdir(NOTES_DIR):
            file_path = os.path.join(NOTES_DIR, file_name)
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                runner.run_recipe_on_text(
                    recipe_id=notes_recipe.recipe_id,
                    raw_text=content,
                    source_info={"name": file_name, "type": "user_note", "location": "Personal Notes"}
                )
    else:
        print(f"Warning: Notes directory {NOTES_DIR} not found.")

    print("\n--- TEST INGESTION COMPLETE ---")
    db_session.close()

if __name__ == "__main__":
    bootstrap_test()
