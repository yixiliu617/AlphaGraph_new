from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict
import uuid

class GraphRepository(ABC):
    """
    PORT: Abstract Base Class for Topology and Relationship Mapping (Neo4j).
    """
    
    @abstractmethod
    def get_neighbors(self, node_id: str, relationship_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves direct graph relationships (e.g., Apple -> iPhone -> Semiconductor).
        """
        pass

    @abstractmethod
    def add_relationship(self, source_id: str, target_id: str, relationship_type: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Creates a node or relationship in Neo4j (e.g., ExtractionRecipe -> Created -> DataFragment).
        """
        pass

    @abstractmethod
    def find_paths(self, start_node_id: str, end_node_id: str, max_depth: int = 3) -> List[Any]:
        """
        For Topology Tab discovery.
        """
        pass

class VectorRepository(ABC):
    """
    PORT: Abstract Base Class for Semantic Search and RAG (Pinecone).
    """
    
    @abstractmethod
    def upsert_vectors(self, vectors: List[List[float]], metadata: List[Dict[str, Any]]) -> bool:
        """
        Stores semantic embeddings of DataFragments.
        """
        pass

    @abstractmethod
    def query_vectors(self, query_vector: List[float], top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Retrieves contextually relevant fragments for the LangChain agent.
        """
        pass

    @abstractmethod
    def delete_vectors(self, filter: Dict[str, Any]) -> bool:
        pass
