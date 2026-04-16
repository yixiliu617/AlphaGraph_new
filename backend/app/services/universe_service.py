from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.graph_repository import GraphRepository
from typing import Dict, Any, Optional

class UniverseService:
    """
    Manages the Public and User-Defined Universes.
    Syncs metadata to Neo4j to enable focused topological views.
    """
    def __init__(self, db: DBRepository, graph: GraphRepository):
        self.db = db
        self.graph = graph

    def sync_company_metadata_to_graph(self, ticker: str, tenant_id: Optional[str] = None):
        """
        Pulls GICS and User categories from Postgres and 'stamps' them 
        onto the Neo4j node.
        """
        # 1. Get Public GICS data
        public_info = self.db.get_public_company(ticker)
        
        # 2. Get User-defined categories
        user_info = None
        if tenant_id:
            user_info = self.db.get_user_company_coverage(tenant_id, ticker)

        # 3. Update Neo4j Node Properties
        # This allows the 'WHERE m.gics_sector = ...' queries to work.
        metadata = {
            "ticker": ticker,
            "gics_sector": public_info.gics_sector if public_info else "Unknown",
            "gics_subsector": public_info.gics_subsector if public_info else "Unknown",
            "user_cat1": user_info.user_category_1 if user_info else None,
            "user_cat2": user_info.user_category_2 if user_info else None
        }
        
        # We use MERGE to update node properties in Neo4j
        self.graph.add_relationship(
            source_id=ticker, 
            target_id=ticker, # Self-loop/Node update pattern
            relationship_type="METADATA_SYNC", 
            metadata=metadata
        )
