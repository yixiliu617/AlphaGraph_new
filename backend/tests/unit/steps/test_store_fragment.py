import pytest

from backend.app.services.extraction_engine.steps.store_fragment import store_fragment


@pytest.mark.unit
class TestStoreFragment:

    def test_saves_fragment_to_relational_db(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)
        mock_db.save_fragment.assert_called_once_with(sample_fragment)

    def test_generates_embedding_from_raw_text(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)
        mock_llm.get_embeddings.assert_called_once_with(sample_fragment.content["raw_text"])

    def test_upserts_to_vector_store(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)
        mock_vector_db.upsert_vectors.assert_called_once()

    def test_embedding_passed_to_vector_store(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        fake_embedding = [[0.9] * 768]
        mock_llm.get_embeddings.return_value = fake_embedding

        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)

        vectors = mock_vector_db.upsert_vectors.call_args.kwargs["vectors"]
        assert vectors == fake_embedding

    def test_vector_metadata_contains_fragment_id(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)

        metadata_list = mock_vector_db.upsert_vectors.call_args.kwargs["metadata"]
        assert len(metadata_list) == 1
        assert str(sample_fragment.fragment_id) in str(metadata_list[0].get("fragment_id", ""))

    def test_vector_metadata_contains_tenant_id(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)

        metadata = mock_vector_db.upsert_vectors.call_args.kwargs["metadata"][0]
        assert metadata["tenant_id"] == sample_fragment.tenant_id

    def test_db_saved_before_vector_upsert(
        self, sample_fragment, mock_db, mock_llm, mock_vector_db
    ):
        """
        Verify call ordering: relational write must succeed before vector write.
        If the order were reversed and the DB write failed, we'd have orphaned vectors.
        """
        call_order = []
        mock_db.save_fragment.side_effect   = lambda *a, **kw: call_order.append("db")
        mock_vector_db.upsert_vectors.side_effect = lambda *a, **kw: call_order.append("vector")

        store_fragment(sample_fragment, mock_db, mock_llm, mock_vector_db)

        assert call_order == ["db", "vector"]
