from neo4j import GraphDatabase
from typing import List, Optional, Any, Dict
import uuid
from backend.app.interfaces.graph_repository import GraphRepository

class Neo4jAdapter(GraphRepository):
    """
    ADAPTER: Concrete implementation for Neo4j Graph Database.
    Fulfills the GraphRepository port contract.
    """
    def __init__(self, uri: str, user: str = "neo4j", password: str = "password"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        print(f"Neo4j Adapter initialized. URI: {uri}")

    def close(self):
        self.driver.close()

    def get_neighbors(self, node_id: str, relationship_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves direct graph relationships.
        """
        with self.driver.session() as session:
            query = (
                "MATCH (n {id: $id})"
                + (f"-[r:{relationship_type}]->" if relationship_type else "-[r]->")
                + "(m) RETURN m, type(r) as rel"
            )
            result = session.run(query, id=node_id)
            return self._parse_neo4j_result(result)

    def get_filtered_neighbors(self, node_id: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Advanced focused view: Filters neighbors by Sector, Subsector, or User Category.
        """
        with self.driver.session() as session:
            # Dynamically build WHERE clause based on filters
            where_clauses = []
            if filters.get("sectors"):
                where_clauses.append("m.gics_sector IN $sectors")
            if filters.get("subsectors"):
                where_clauses.append("m.gics_subsector IN $subsectors")
            if filters.get("user_categories"):
                where_clauses.append("(m.user_cat1 IN $user_cats OR m.user_cat2 IN $user_cats)")

            where_str = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            query = f"MATCH (n {{id: $id}})-[r]->(m){where_str} RETURN m, type(r) as rel"
            
            result = session.run(
                query, 
                id=node_id, 
                sectors=filters.get("sectors"),
                subsectors=filters.get("subsectors"),
                user_cats=filters.get("user_categories")
            )
            return self._parse_neo4j_result(result)

    def _parse_neo4j_result(self, result) -> List[Dict[str, Any]]:
        nodes = []
        for record in result:
            nodes.append({
                "id": record["m"]["id"],
                "labels": list(record["m"].labels),
                "properties": dict(record["m"]),
                "relationship": record["rel"]
            })
        return nodes

    def add_relationship(self, source_id: str, target_id: str, relationship_type: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Creates nodes and relationships in Neo4j.
        This enables topological discovery (ExtractionRecipe -> Created -> DataFragment).
        """
        with self.driver.session() as session:
            query = (
                "MERGE (s {id: $source_id}) "
                "MERGE (t {id: $target_id}) "
                f"MERGE (s)-[r:{relationship_type}]->(t) "
                "SET r += $metadata "
                "RETURN count(r) as count"
            )
            result = session.run(query, source_id=source_id, target_id=target_id, metadata=metadata or {})
            record = result.single()
            return record["count"] > 0 if record else False

    def find_paths(self, start_node_id: str, end_node_id: str, max_depth: int = 3) -> List[Any]:
        """
        Finds topological paths between entities for discovery views.
        Example Cypher: MATCH p = shortestPath((s {id: $s_id})-[*..3]->(e {id: $e_id})) RETURN p
        """
        with self.driver.session() as session:
            query = (
                f"MATCH p = shortestPath((s {{id: $s_id}})-[*..{max_depth}]->(e {{id: $e_id}})) "
                "RETURN p"
            )
            result = session.run(query, s_id=start_node_id, e_id=end_node_id)
            paths = []
            for record in result:
                paths.append(record["p"])
            return paths
