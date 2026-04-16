from abc import ABC, abstractmethod
from typing import List, Any, Dict, Optional

class QuantRepository(ABC):
    """
    PORT: Abstract Base Class for Structured Layer 1 Quant Data (DuckDB/Parquet).
    """
    
    @abstractmethod
    def execute_query(self, query: str) -> List[Any]:
        """
        Executes an SQL query against the local Parquet files.
        """
        pass

    @abstractmethod
    def get_ticker_metrics(self, ticker: str, metrics: List[str]) -> Dict[str, Any]:
        """
        Specialized method for high-speed metric retrieval.
        """
        pass
