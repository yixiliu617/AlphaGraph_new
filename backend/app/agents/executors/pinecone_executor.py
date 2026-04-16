from backend.app.agents.executors.base import QueryExecutor
from backend.app.interfaces.graph_repository import VectorRepository
from backend.app.interfaces.llm_provider import LLMProvider
from backend.app.models.api_contracts import AgentResponseBlock


class PineconeExecutor(QueryExecutor):
    """
    Executes semantic vector searches against the Pinecone qualitative index.

    Handles the search_documents tool call from the Engine agent.
    Requires an embedding-capable LLM (GeminiAdapter) -- AnthropicAdapter
    does not support embeddings, so inject GeminiAdapter here separately.

    Adding a new document source (news, website, broker report):
      1. Add an extractor that ingests to Pinecone with the new doc_type tag.
      2. Update the doc_types list in agents/tools.py search_documents description.
      Zero changes here.
    """

    def __init__(self, llm: LLMProvider, vector: VectorRepository):
        self.llm    = llm
        self.vector = vector

    def can_handle(self, tool_call: dict) -> bool:
        return tool_call.get("name") == "search_documents"

    def execute(self, tool_call: dict) -> tuple:
        inp           = tool_call.get("input", {})
        query         = inp.get("query", "")
        ticker_filter = inp.get("ticker_filter", [])
        doc_types     = inp.get("doc_types", [])
        top_k         = min(int(inp.get("top_k", 5)), 10)

        if not query:
            block = AgentResponseBlock(
                block_type="text",
                title="Document Search",
                data={"results": [], "message": "No search query provided."},
            )
            return block, "Search query was empty."

        print(f"[PineconeExecutor] Searching: {query!r} filters={ticker_filter} types={doc_types}")

        try:
            query_vector = self.llm.get_embeddings(query)[0]
            results      = self.vector.query_vectors(query_vector=query_vector, top_k=top_k)
        except NotImplementedError:
            # Embedding not supported by the injected LLM (should not happen if
            # GeminiAdapter is injected, but guard defensively)
            results = []
        except Exception as exc:
            results = []
            print(f"[PineconeExecutor] Search error: {exc}")

        if not results:
            block = AgentResponseBlock(
                block_type="text",
                title=f'Research: "{query[:60]}"',
                data={
                    "results": [],
                    "message": (
                        "No documents found. The research library may not be indexed yet. "
                        "Run the extraction pipeline to populate it."
                    ),
                },
            )
            return block, f"No documents found for query: {query!r}."

        block = AgentResponseBlock(
            block_type="text",
            title=f'Research: "{query[:60]}"',
            data={"results": results},
        )
        n = len(results)
        summary = f"Found {n} document excerpt{'s' if n != 1 else ''} for: {query!r}."
        return block, summary
