from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict, Union
from pydantic import BaseModel

class LLMProvider(ABC):
    """
    PORT: Abstract Base Class for LLM providers (OpenAI, Gemini, Anthropic).
    Provides absolute modularity for our agentic routing.
    """
    
    @abstractmethod
    def generate_response(self, prompt: str, system_message: str = "You are a senior financial analyst.") -> str:
        """
        Generic textual synthesis.
        """
        pass

    @abstractmethod
    def generate_structured_output(self, prompt: str, output_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enforces a JSON Schema (passed from ExtractionRecipe) for structured extraction.
        """
        pass

    @abstractmethod
    def get_embeddings(self, text: Union[str, List[str]]) -> List[List[float]]:
        """
        Vector embedding generation for Pinecone RAG.
        """
        pass

    @abstractmethod
    def stream_response(self, prompt: str):
        """
        For real-time thought process streaming in the Unified Data Engine.
        """
        pass
        
    @abstractmethod
    def classify_intent(self, user_query: str) -> str:
        """
        Specific routing classification: 'quant', 'qual', or 'synthesis'.
        """
        pass

    def generate_with_tools(
        self,
        system: str,
        messages: List[Dict],
        tools: List[Dict],
    ) -> tuple:
        """
        Tool-use / function-calling pass.
        Returns (text: str, tool_calls: list[dict]) where each tool_call is
        {"name": str, "input": dict}.

        Not abstract -- only adapters that support tool-use need to implement this.
        Default raises NotImplementedError so callers get a clear error message
        rather than a silent no-op.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support tool-use. "
            "Use AnthropicAdapter for the Engine agent."
        )
