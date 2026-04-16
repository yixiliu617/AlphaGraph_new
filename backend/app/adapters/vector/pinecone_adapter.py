from pinecone import Pinecone
from typing import List, Optional, Any, Dict
from backend.app.interfaces.graph_repository import VectorRepository

class PineconeAdapter(VectorRepository):
    """
    ADAPTER: Concrete implementation for Pinecone VectorDB.
    Fulfills the VectorRepository port contract.
    """
    def __init__(self, api_key: str, index_name: str, dimension: int = 768):
        self.pc = Pinecone(api_key=api_key)
        self.index_name = index_name
        
        # Check if index exists, else create it in a separate process
        # For simplicity, we assume index is already created or we'll handle creation elsewhere
        self.index = self.pc.Index(self.index_name)
        print(f"Pinecone Adapter initialized. Index: {self.index_name}")

    def upsert_vectors(self, vectors: List[List[float]], metadata: List[Dict[str, Any]]) -> bool:
        """
        Stores semantic embeddings of DataFragments with metadata for filtering.
        """
        try:
            # Prepare data for batch upsert
            to_upsert = []
            for i, vec in enumerate(vectors):
                # Using metadata's fragment_id or a unique ID if not present
                vector_id = metadata[i].get("fragment_id", f"vec_{i}")
                to_upsert.append((str(vector_id), vec, metadata[i]))
            
            self.index.upsert(vectors=to_upsert)
            return True
        except Exception as e:
            print(f"Pinecone Upsert Error: {e}")
            return False

    def query_vectors(self, query_vector: List[float], top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Retrieves contextually relevant fragments for the LangChain agent.
        Supports tenant_id and metadata filtering.
        """
        try:
            results = self.index.query(
                vector=query_vector,
                top_k=top_k,
                include_metadata=True,
                filter=filters
            )
            # Extract relevant info from results
            fragments = []
            for match in results['matches']:
                fragments.append({
                    "id": match['id'],
                    "score": match['score'],
                    "metadata": match['metadata']
                })
            return fragments
        except Exception as e:
            print(f"Pinecone Query Error: {e}")
            return []

    def delete_vectors(self, filter: Dict[str, Any]) -> bool:
        """
        Supports deleting vectors based on metadata (e.g., when a tenant is removed).
        """
        try:
            self.index.delete(filter=filter)
            return True
        except Exception as e:
            print(f"Pinecone Delete Error: {e}")
            return False
