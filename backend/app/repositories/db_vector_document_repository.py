from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from backend.app.repositories.db_repository_utils import as_datetime, json_dumps, json_loads
from backend.app.services.database_connector import DatabaseConnector


class DbVectorDocumentRepository:
    def __init__(self, database_connector: DatabaseConnector) -> None:
        self.database_connector = database_connector

    def find_by_document_ids(self, document_ids: list[str]) -> list[dict]:
        if not document_ids:
            return []
        sql, params = self._build_in_clause(
            base_sql=(
                "SELECT document_id, source_type, source_id, summary, text_content, metadata_json, "
                "content_hash, embedding_provider, embedding_backend, embedding_model, "
                "embedding_dimensions, vector_json, created_at, updated_at "
                "FROM vector_corpus_documents WHERE document_id IN "
            ),
            values=document_ids,
            value_prefix="document_id",
        )
        rows = self.database_connector.fetch_all(sql, params)
        return [self._hydrate_row(row) for row in rows]

    def upsert_documents(self, documents: list[dict]) -> int:
        if not documents:
            return 0

        insert_statement = text(
            """
            INSERT INTO vector_corpus_documents (
                document_id, source_type, source_id, summary, text_content, metadata_json,
                content_hash, embedding_provider, embedding_backend, embedding_model,
                embedding_dimensions, vector_json, created_at, updated_at
            ) VALUES (
                :document_id, :source_type, :source_id, :summary, :text_content, :metadata_json,
                :content_hash, :embedding_provider, :embedding_backend, :embedding_model,
                :embedding_dimensions, :vector_json, :created_at, :updated_at
            )
            """
        )
        update_statement = text(
            """
            UPDATE vector_corpus_documents
            SET source_type = :source_type,
                source_id = :source_id,
                summary = :summary,
                text_content = :text_content,
                metadata_json = :metadata_json,
                content_hash = :content_hash,
                embedding_provider = :embedding_provider,
                embedding_backend = :embedding_backend,
                embedding_model = :embedding_model,
                embedding_dimensions = :embedding_dimensions,
                vector_json = :vector_json,
                updated_at = :updated_at
            WHERE document_id = :document_id
            """
        )
        with self.database_connector.begin() as connection:
            for document in documents:
                params = {
                    "document_id": document["document_id"],
                    "source_type": document["source_type"],
                    "source_id": document["source_id"],
                    "summary": document.get("summary"),
                    "text_content": document.get("text_content", ""),
                    "metadata_json": json_dumps(document.get("metadata", {})),
                    "content_hash": document["content_hash"],
                    "embedding_provider": document["embedding_provider"],
                    "embedding_backend": document["embedding_backend"],
                    "embedding_model": document["embedding_model"],
                    "embedding_dimensions": int(document["embedding_dimensions"]),
                    "vector_json": json_dumps(document.get("vector", [])),
                    "created_at": self._coerce_datetime(document.get("created_at")),
                    "updated_at": self._coerce_datetime(document.get("updated_at")),
                }
                result = connection.execute(update_statement, params)
                if int(result.rowcount or 0) == 0:
                    connection.execute(insert_statement, params)
        return len(documents)

    def delete_missing(self, document_ids: list[str]) -> int:
        if not document_ids:
            return self.database_connector.execute_write("DELETE FROM vector_corpus_documents")
        sql, params = self._build_in_clause(
            base_sql="DELETE FROM vector_corpus_documents WHERE document_id NOT IN ",
            values=document_ids,
            value_prefix="document_id",
        )
        return self.database_connector.execute_write(sql, params)

    def _hydrate_row(self, row: dict) -> dict:
        return {
            "document_id": row["document_id"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "summary": row.get("summary"),
            "text_content": row.get("text_content", ""),
            "metadata": json_loads(row.get("metadata_json"), {}),
            "content_hash": row["content_hash"],
            "embedding_provider": row["embedding_provider"],
            "embedding_backend": row["embedding_backend"],
            "embedding_model": row["embedding_model"],
            "embedding_dimensions": int(row["embedding_dimensions"]),
            "vector": json_loads(row.get("vector_json"), []),
            "created_at": as_datetime(row["created_at"]),
            "updated_at": as_datetime(row["updated_at"]),
        }

    def _build_in_clause(self, base_sql: str, values: list[str], value_prefix: str) -> tuple[str, dict]:
        params: dict[str, str] = {}
        placeholders: list[str] = []
        for index, value in enumerate(values):
            key = f"{value_prefix}_{index}"
            params[key] = value
            placeholders.append(f":{key}")
        return f"{base_sql}({', '.join(placeholders)})", params

    def _coerce_datetime(self, value) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return as_datetime(value)
        return datetime.now(tz=timezone.utc)
