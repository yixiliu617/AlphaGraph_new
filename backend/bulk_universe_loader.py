import csv
import os
import sys

# Ensure the project root is in the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.db.session import SessionLocal, init_db
from backend.app.adapters.db.postgres_adapter import PostgresAdapter
from backend.app.models.domain.universe import PublicCompany
from backend.app.services.universe_service import UniverseService
from backend.app.core.config import settings

# Conditional import for Neo4j
try:
    from backend.app.adapters.graph.neo4j_adapter import Neo4jAdapter
    HAS_NEO4J_LIB = True
except ImportError:
    HAS_NEO4J_LIB = False

CSV_PATH = "backend/data/public_universe.csv"

def load_universe():
    # 0. Ensure tables exist
    try:
        init_db()
    except Exception as e:
        print(f"Database Initialization Error: {e}")
    
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found.")
        return

    db_session = SessionLocal()
    postgres_repo = PostgresAdapter(db_session)
    
    # Initialize Neo4j for syncing
    has_graph = False
    if HAS_NEO4J_LIB:
        try:
            graph_repo = Neo4jAdapter(
                uri=settings.NEO4J_URI,
                user=settings.NEO4J_USER,
                password=settings.NEO4J_PASSWORD
            )
            universe_service = UniverseService(db=postgres_repo, graph=graph_repo)
            has_graph = True
        except Exception as e:
            print(f"Warning: Could not connect to Neo4j. Graph syncing will be skipped. Error: {e}")
    else:
        print("Warning: neo4j library not installed. Graph syncing will be skipped.")

    print(f"--- Loading Public Universe from {CSV_PATH} ---")
    
    with open(CSV_PATH, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = PublicCompany(
                ticker=row['ticker'],
                name=row['name'],
                gics_sector=row['gics_sector'],
                gics_subsector=row['gics_subsector'],
                gics_subindustry=row['gics_subindustry']
            )
            
            # 1. Save to Postgres
            success = postgres_repo.save_public_company(company)
            if success:
                print(f"Saved {company.ticker} to Postgres.")
                
                # 2. Sync to Neo4j
                if has_graph:
                    try:
                        universe_service.sync_company_metadata_to_graph(company.ticker)
                        print(f"Synced {company.ticker} to Neo4j.")
                    except Exception as e:
                        print(f"Failed to sync {company.ticker} to Neo4j: {e}")
            else:
                print(f"Failed to save {company.ticker} to Postgres.")

    db_session.close()
    if has_graph:
        graph_repo.close()
    print("--- Bulk Universe Loading Complete ---")

if __name__ == "__main__":
    load_universe()
