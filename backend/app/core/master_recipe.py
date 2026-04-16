from typing import Dict, Any, List
import uuid

# This is the "Gold Standard" logic that we will save into our Postgres 'extraction_recipes' table.
MASTER_FINANCIAL_RECIPE = {
    "name": "AlphaGraph Master Intelligence Extractor",
    "ingestor_type": "MULTIMODAL_FINANCIAL",
    "expected_schema": {
        "type": "object",
        "properties": {
            "entity_type": {"type": "string", "enum": ["sector", "theme", "company"]},
            "event_date": {"type": "string", "format": "date", "description": "The date the actual event occurred (e.g. 2025-03-18 for GTC)"},
            "summary": {
                "type": "object",
                "properties": {
                    "key_points": {"type": "array", "items": {"type": "string"}},
                    "supporting_evidence": {"type": "array", "items": {"type": "string"}}
                }
            },
            # SECTION 2.1 & 2.3: Relationships & Topology (Feeds Neo4j)
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_entity": {"type": "string"},
                        "relationship_type": {"type": "string", "enum": ["supplier", "customer", "peer", "competitor", "upstream", "downstream"]},
                        "context": {"type": "string"},
                        "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]}
                    }
                }
            },
            # SECTION 2.1.4 & 2.3.4: Catalyst Tracking (Feeds Thesis Ledger)
            "catalysts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "date": {"type": "string", "format": "date"},
                        "is_future": {"type": "boolean"},
                        "impact_reason": {"type": "string"}
                    }
                }
            },
            # SECTION 2.1.7 & 2.3.6: Causal Impacts (The 'Because' Logic)
            "causal_impacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "factor": {"type": "string", "description": "e.g. Component Shortage, Inflation"},
                        "outcome": {"type": "string", "description": "e.g. Stock Underperformed"},
                        "evidence_sentence": {"type": "string"}
                    }
                }
            },
            # SECTION 2.1.1 & 2.3.3: Metrics & Guidance (Feeds DuckDB)
            "extracted_metrics": {
                "type": "object",
                "additionalProperties": {"type": "number"}
            }
        },
        "required": ["entity_type", "event_date", "summary"]
    }
}
