from backend.app.models.domain.data_fragment import DataFragment
from backend.app.models.domain.thesis_ledger import Catalyst
from backend.app.interfaces.db_repository import DBRepository
from backend.app.interfaces.graph_repository import GraphRepository


def fanout_to_graph(fragment: DataFragment, graph_db: GraphRepository) -> None:
    """
    Step 5a: Fan-out extracted relationships into the Neo4j topology graph.
    Adding new relationship types or changing Neo4j only touches this step
    and the GraphRepository adapter — nothing upstream is affected.
    """
    metrics = fragment.content.get("extracted_metrics", {})
    relationships = metrics.get("relationships", [])
    source_entity = metrics.get("entity_name", fragment.source)

    for rel in relationships:
        graph_db.add_relationship(
            source_id=source_entity,
            target_id=rel.get("target_entity"),
            relationship_type=rel.get("relationship_type", "mentioned_with"),
            metadata={
                "context": rel.get("context"),
                "direction": rel.get("direction"),
                "fragment_id": str(fragment.fragment_id),
            },
        )


def fanout_to_ledger(fragment: DataFragment, db: DBRepository) -> None:
    """
    Step 5b: Fan-out extracted catalysts into the active Thesis Ledger.
    Adding new catalyst logic only touches this step — storage and graph
    steps are completely unaffected.
    """
    metrics = fragment.content.get("extracted_metrics", {})
    catalysts_data = metrics.get("catalysts", [])
    if not catalysts_data:
        return

    ledger = db.get_ledger(fragment.tenant_id)
    if not ledger:
        return

    for c_data in catalysts_data:
        Catalyst(
            description=c_data.get("description"),
            impact_weight=5.0,
            supporting_fragment_ids=[fragment.fragment_id],
        )
    db.update_ledger(ledger)
