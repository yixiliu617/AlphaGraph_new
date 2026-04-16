import os
import sys
import uuid
import json
from datetime import datetime

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.models.domain.extraction_recipe import ExtractionRecipe
from backend.app.models.domain.data_fragment import DataFragment
from backend.app.services.extraction_engine.validators import ExtractionValidator
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.interfaces.graph_repository import VectorRepository, GraphRepository
from backend.app.interfaces.db_repository import DBRepository
from backend.app.core.master_recipe import MASTER_FINANCIAL_RECIPE

class MockLLM(LLMProvider):
    def generate_response(self, prompt, system_message=""): return "Mock Synthesis"
    def get_embeddings(self, text): return [[0.1] * 768]
    def stream_response(self, prompt): yield "Mock Stream"
    def classify_intent(self, query): return "qual"
    
    def generate_structured_output(self, prompt, output_schema):
        # SIMULATING the result of Section 2.3 (One Company) from our logic
        return {
            "entity_type": "company",
            "event_date": "2025-03-18",
            "summary": {
                "key_points": ["NVDA GTC 2025 showcased Blackwell mass production.", "Enterprise AI demand accelerating."],
                "supporting_evidence": ["Blackwell systems are in full production.", "H200 supply remains tight."]
            },
            "relationships": [
                {"target_entity": "TSM", "relationship_type": "supplier", "context": "Exclusive foundry for Blackwell", "direction": "positive"},
                {"target_entity": "AMZN", "relationship_type": "customer", "context": "Early adopter of Blackwell clusters", "direction": "positive"}
            ],
            "catalysts": [
                {"description": "Blackwell full volume ramp", "date": "2025-06-01", "is_future": True, "impact_reason": "Margin expansion"}
            ],
            "causal_impacts": [
                {"factor": "Blackwell production", "outcome": "EPS Outperformance", "evidence_sentence": "Volume ramp expected to beat consensus."}
            ],
            "extracted_metrics": {
                "revenue_guidance": 30000000000,
                "gross_margin": 76.5
            }
        }

class MockDB(DBRepository):
    def save_fragment(self, f): print(f"--- MOCK: Saved Fragment {f.fragment_id} to DB ---"); return True
    def get_fragment(self, id): return None
    def get_tenant_fragments(self, tid, l=50): return []
    def save_recipe(self, r): return True
    def get_recipe(self, id): 
        return ExtractionRecipe(
            tenant_id="test-tenant",
            name="Master Financial Logic",
            ingestor_type="SIMULATION",
            expected_schema=MASTER_FINANCIAL_RECIPE["expected_schema"],
            llm_prompt_template="Test Prompt"
        )
    def get_ledger(self, tid): return None
    def update_ledger(self, l): return True
    def get_public_companies(self, s=None): return []
    def get_user_universe(self, tid): return []
    def save_public_company(self, c): return True

class MockVector(VectorRepository):
    def upsert_vectors(self, v, m): print("--- MOCK: Upserted to Pinecone ---"); return True
    def query_vectors(self, v, k=5, f=None): return []
    def delete_vectors(self, f): return True

class MockGraph(GraphRepository):
    def get_neighbors(self, id, r=None): return []
    def find_paths(self, s, e, d=3): return []
    def add_relationship(self, s, t, r, m=None):
        print(f"--- MOCK: NEO4J EDGE: ({s}) -[{r}]-> ({t}) | Context: {m.get('context')}")
        return True

def run_simulation():
    print("=== ALPHAGRAPH EXTRACTION SIMULATION ===")
    
    # 1. Setup Simulation Logic
    llm = MockLLM()
    db = MockDB()
    vector = MockVector()
    graph = MockGraph()
    validator = ExtractionValidator()
    
    # 2. Sample "Raw File" Content
    sample_text = "NVIDIA's GTC 2025 event confirmed that Blackwell is ramping in volume with TSM as the exclusive foundry."
    source_info = {"name": "GTC_Analysis.pdf", "type": "broker_report", "location": "Page 1"}
    
    # 3. Simulate Pipeline
    print(f"\nProcessing File: {source_info['name']}...")
    
    # Get recipe (Mocked)
    recipe = db.get_recipe(uuid.uuid4())
    
    # Generate structured output from LLM (Mocked)
    raw_json = llm.generate_structured_output(sample_text, recipe.expected_schema)
    
    # Pass through FIREWALL
    fragment = validator.create_fragment(raw_json, recipe, source_info)
    
    print("\n--- VALIDATED DATA FRAGMENT (Pydantic V2 Output) ---")
    print(json.dumps(fragment.model_dump(mode='json'), indent=2))
    
    # 4. Perform Fan-Out
    print("\n--- PERFORMING FAN-OUT ---")
    db.save_fragment(fragment)
    vector.upsert_vectors([[0.1]], [{"fragment_id": str(fragment.fragment_id)}])
    
    # Extract relationships from the complex nested logic
    rels = fragment.content["extracted_metrics"].get("relationships", [])
    for rel in rels:
        graph.add_relationship(
            "NVDA", # Hardcoded for demo
            rel["target_entity"],
            rel["relationship_type"],
            {"context": rel["context"]}
        )

    print("\n=== SIMULATION COMPLETE ===")
    print("Logic successfully handled Entity Identification, Relationship Mapping, and Schema-based Metric Extraction.")

if __name__ == "__main__":
    run_simulation()
