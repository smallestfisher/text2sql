from __future__ import annotations

from collections import Counter
import logging
import math
import re
import threading

from backend.app.models.example_library import ExampleRecord, ExampleTemplateRecord
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.services.example_factory import ExampleFactory
from backend.app.services.metadata_registry import MetadataRegistry
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.sql_ast_validator import SqlAstValidator
from backend.app.services.vector_corpus_store_service import VectorCorpusStoreService
from backend.app.services.vector_retriever import VectorRetriever


logger = logging.getLogger(__name__)


_TOKENIZER_LOCK = threading.Lock()
_CHINESE_TOKENIZER = None
_CHINESE_TOKENIZER_READY = False


def _get_chinese_tokenizer():
    """Lazily build a dedicated jieba tokenizer.

    Returns ``None`` when jieba is unavailable so callers can fall back to
    bigram-only Chinese tokenization without crashing.
    """

    global _CHINESE_TOKENIZER, _CHINESE_TOKENIZER_READY
    if _CHINESE_TOKENIZER_READY:
        return _CHINESE_TOKENIZER
    with _TOKENIZER_LOCK:
        if _CHINESE_TOKENIZER_READY:
            return _CHINESE_TOKENIZER
        try:
            import jieba

            tokenizer = jieba.Tokenizer()
            tokenizer.initialize()
            _CHINESE_TOKENIZER = tokenizer
        except Exception:  # pragma: no cover - depends on optional dependency
            logger.warning(
                "jieba is unavailable; falling back to bigram Chinese tokenization"
            )
            _CHINESE_TOKENIZER = None
        _CHINESE_TOKENIZER_READY = True
    return _CHINESE_TOKENIZER


class RetrievalService:
    HIT_SOURCE_TYPE_QUOTAS = {
        "example": 2,
        "table_schema": 2,
        "knowledge": 2,
        "join_pattern": 1,
    }

    def __init__(
        self,
        domain_config: dict,
        semantic_runtime: SemanticRuntime | None = None,
        metadata_registry: MetadataRegistry | None = None,
        vector_retriever: VectorRetriever | None = None,
        vector_corpus_store_service: VectorCorpusStoreService | None = None,
        vector_top_k: int = 8,
        prewarm_vector_index: bool = False,
    ) -> None:
        self.domain_config = domain_config
        self.metadata_registry = metadata_registry or (
            semantic_runtime.metadata_registry if semantic_runtime is not None else MetadataRegistry()
        )
        self.semantic_runtime = semantic_runtime or SemanticRuntime(
            domain_config,
            metadata_registry=self.metadata_registry,
        )
        self.example_factory = ExampleFactory(domain_config, self.semantic_runtime)
        self.sql_inspector = SqlAstValidator()
        self.vector_retriever = vector_retriever or VectorRetriever(provider="disabled")
        self.vector_corpus_store_service = vector_corpus_store_service
        self.vector_top_k = vector_top_k
        self.table_domains: dict[str, list[str]] = {}
        if self.vector_retriever.provider != "disabled" and not self.vector_retriever.enabled:
            raise RuntimeError("vector retrieval is enabled but vector embedding client is not configured")
        self.prewarm_vector_index_on_reload = prewarm_vector_index
        self.examples = self._load_examples()
        self.tables_metadata = self._load_tables_metadata()
        self.business_knowledge = self._load_business_knowledge()
        self.join_patterns = self._load_join_patterns()
        self.table_domains = self._build_table_domains()
        self._seed_chinese_tokenizer()
        self.corpus_documents: list[dict] = []
        self.document_frequency: Counter[str] = Counter()
        self.average_doc_length = 1.0
        self.last_vector_sync_summary: dict = {
            "persisted_document_count": 0,
            "reused_document_count": 0,
            "rebuilt_document_count": 0,
            "deleted_document_count": 0,
            "upserted_document_count": 0,
            "vector_sync_last_updated_at": None,
            "embedding_signature": None,
            "error": None,
            "pending_rebuild": bool(self.vector_retriever.enabled),
        }
        self._refresh_indexes()
        if prewarm_vector_index:
            # Best-effort: a failing embedding backend must not block container
            # construction (which would brick startup / reset_container). The
            # error is recorded in last_vector_sync_summary and retrieval that
            # needs vectors surfaces it at use time; the admin prewarm button
            # can retry once the config is fixed.
            try:
                self.prewarm_vector_index()
            except Exception:
                logger.warning("vector index prewarm failed at construction", exc_info=True)

    def retrieve_text(
        self,
        *,
        question: str,
        semantic_brief: str | None = None,
        conversation_summary: str | None = None,
    ) -> RetrievalContext:
        retrieval_terms = self._unique(
            [
                question,
                semantic_brief or "",
                conversation_summary or "",
            ]
        )
        query_tokens = sorted(self._tokenize(" ".join(retrieval_terms)))
        hits: list[RetrievalHit] = []
        hits.extend(self._retrieve_text_document_hits(query_tokens))
        retrieval_warnings: list[str] = []
        vector_used = False
        if self.vector_retriever.enabled:
            try:
                self._ensure_vector_ready()
                hits.extend(self._retrieve_text_vector_hits(" ".join(retrieval_terms)))
                vector_used = True
            except Exception as exc:
                warning = f"vector retrieval unavailable; keyword fallback used: {exc}"
                retrieval_warnings.append(warning)
                logger.warning(warning)
        self._apply_fusion_scores(hits)
        hits = self._rerank_hits(hits)
        top_hits = self._select_top_hits(hits, limit=self._top_hit_limit())
        return RetrievalContext(
            domains=self._domains_from_hits(top_hits),
            metrics=self._metrics_from_hits(top_hits),
            retrieval_terms=retrieval_terms,
            retrieval_channels=self._retrieval_channels(vector_used=vector_used),
            hits=top_hits,
            hit_count_by_source=self._count_hits_by_source(top_hits),
            hit_count_by_channel=self._count_hits_by_channel(top_hits),
            warnings=retrieval_warnings,
        )

    def reload(self, *, prewarm_vectors: bool | None = None) -> None:
        self.metadata_registry.reload()
        self.semantic_runtime.reload()
        self.examples = self._load_examples()
        self.tables_metadata = self._load_tables_metadata()
        self.business_knowledge = self._load_business_knowledge()
        self.join_patterns = self._load_join_patterns()
        self.table_domains = self._build_table_domains()
        self._seed_chinese_tokenizer()
        should_prewarm = self.prewarm_vector_index_on_reload if prewarm_vectors is None else prewarm_vectors
        self._refresh_indexes()
        if should_prewarm:
            self.prewarm_vector_index()

    def prewarm_vector_index(self) -> dict:
        self._sync_vector_index()
        return {
            "accepted": True,
            "vector_enabled": self.vector_retriever.enabled,
            "pending_rebuild": bool(self.last_vector_sync_summary.get("pending_rebuild")),
        }

    def summarize_retrieval(self, retrieval: RetrievalContext) -> dict:
        return {
            "channels": retrieval.retrieval_channels,
            "warnings": retrieval.warnings,
            "hit_count_by_source": retrieval.hit_count_by_source,
            "hit_count_by_channel": retrieval.hit_count_by_channel,
            "top_hits": [
                {
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "score": hit.score,
                    "retrieval_channel": hit.retrieval_channel,
                    "source_score": hit.source_score,
                    "matched_features": hit.matched_features,
                }
                for hit in retrieval.hits[:3]
            ],
        }

    def health(self) -> dict:
        vector_health = self.vector_retriever.health()
        return {
            "vector_enabled": self.vector_retriever.enabled,
            "vector_provider": self.vector_retriever.provider,
            "vector_ready": vector_health.get("ready", False),
            "document_count": len(self.corpus_documents),
            "document_count_by_source": dict(
                Counter(document["source_type"] for document in self.corpus_documents)
            ),
            "example_count": len(self.examples),
            "join_pattern_count": len(self.join_patterns),
            "vector_sync": self.last_vector_sync_summary,
        }

    def validate_example(self, payload: dict | ExampleTemplateRecord) -> ExampleRecord:
        return self.example_factory.normalize(payload)

    def dump_example_template(self, payload: dict | ExampleTemplateRecord) -> dict:
        return self.example_factory.dump_template(payload)

    def _load_examples(self) -> list[ExampleRecord]:
        payload = self.metadata_registry.examples_template
        return [self.validate_example(item) for item in payload]

    def _refresh_indexes(self) -> None:
        self.corpus_documents = (
            self._build_example_documents()
            + self._build_knowledge_documents()
            + self._build_table_schema_documents()
            + self._build_join_pattern_documents()
        )
        self.document_frequency = Counter()
        total_length = 0
        for document in self.corpus_documents:
            total_length += document["length"]
            self.document_frequency.update(set(document["token_counts"].keys()))
        self.average_doc_length = total_length / len(self.corpus_documents) if self.corpus_documents else 1.0
        self._mark_vector_index_pending()

    def _mark_vector_index_pending(self) -> None:
        if not self.vector_retriever.enabled:
            self.last_vector_sync_summary = {
                "persisted_document_count": 0,
                "reused_document_count": 0,
                "rebuilt_document_count": 0,
                "deleted_document_count": 0,
                "upserted_document_count": 0,
                "vector_sync_last_updated_at": None,
                "embedding_signature": None,
                "error": None,
                "pending_rebuild": False,
            }
            self.vector_retriever.load_documents([])
            return
        self.last_vector_sync_summary = {
            "persisted_document_count": 0,
            "reused_document_count": 0,
            "rebuilt_document_count": 0,
            "deleted_document_count": 0,
            "upserted_document_count": 0,
            "vector_sync_last_updated_at": None,
            "embedding_signature": self.vector_retriever.embedding_signature(),
            "error": None,
            "pending_rebuild": True,
        }
        self.vector_retriever.load_documents([])

    def _sync_vector_index(self) -> None:
        if not self.vector_retriever.enabled:
            self._mark_vector_index_pending()
            return
        if self.vector_corpus_store_service is None:
            raise RuntimeError("vector_corpus_store_service is required when vector retrieval is enabled")
        try:
            sync_result = self.vector_corpus_store_service.sync(self.corpus_documents)
        except Exception as exc:
            logger.exception("vector corpus sync failed")
            self.vector_retriever.load_documents([])
            self.last_vector_sync_summary = {
                "persisted_document_count": 0,
                "reused_document_count": 0,
                "rebuilt_document_count": 0,
                "deleted_document_count": 0,
                "upserted_document_count": 0,
                "vector_sync_last_updated_at": None,
                "embedding_signature": None,
                "error": str(exc),
                "pending_rebuild": True,
            }
            raise RuntimeError(f"vector corpus sync failed: {exc}") from exc
        self.last_vector_sync_summary = sync_result.summary()
        self.last_vector_sync_summary["pending_rebuild"] = False
        self.vector_retriever.load_documents(sync_result.documents)

    def _ensure_vector_ready(self) -> None:
        if not self.vector_retriever.enabled:
            return
        if self.last_vector_sync_summary.get("pending_rebuild"):
            raise RuntimeError("vector retrieval is enabled but vector index is pending rebuild")
        error = self.last_vector_sync_summary.get("error")
        if error:
            raise RuntimeError(f"vector retrieval is unavailable: {error}")
        if not self.vector_retriever.ready:
            raise RuntimeError("vector retrieval is enabled but vector index is not ready")

    def _build_example_documents(self) -> list[dict]:
        documents: list[dict] = []
        for example in self.examples:
            filter_terms = [
                f"{item.field} {item.op} {self._stringify_filter_value(item.value)}"
                for item in example.filters
            ]
            sql_features = self._example_sql_features(example)
            documents.append(
                self._build_document(
                    source_type="example",
                    source_id=example.id,
                    summary=example.normalized_question,
                    metadata={
                        "intent": example.intent,
                        "scenario": example.scenario,
                        "coverage_tags": example.coverage_tags,
                        "question_type": example.question_type,
                        "subject_domain": example.subject_domain,
                        "domains": [example.subject_domain] if example.subject_domain != "unknown" else [],
                        "tables": example.tables,
                        "metrics": example.metrics,
                        "entities": example.entities,
                        "dimensions": example.dimensions,
                        "filter_fields": [item.field for item in example.filters],
                        "join_path": example.join_path,
                        "result_shape": example.result_shape,
                        "sql_features": sql_features,
                    },
                    text_parts=[
                        example.question,
                        example.normalized_question,
                        example.intent,
                        example.subject_domain,
                        example.scenario or "",
                        " ".join(example.coverage_tags),
                        " ".join(example.tables),
                        " ".join(example.metrics),
                        " ".join(example.entities),
                        " ".join(example.dimensions),
                        " ".join(filter_terms),
                        " ".join(example.join_path),
                        example.result_shape or "",
                        example.notes or "",
                        self._format_sql_features(sql_features),
                        self._sql_retrieval_outline(example.sql),
                    ],
                )
            )
        return documents

    def _example_sql_features(self, example: ExampleRecord) -> dict:
        inspection = self.sql_inspector.inspect(example.sql)
        physical_sources = [
            source
            for source in inspection.sources
            if source not in set(inspection.cte_names)
        ]
        return {
            "sources": self._unique(physical_sources),
            "cte_names": inspection.cte_names,
            "functions": inspection.functions,
            "referenced_fields": inspection.referenced_fields,
            "select_fields": inspection.select_fields,
            "group_by_fields": inspection.group_by_fields,
            "order_by_fields": inspection.order_by_fields,
            "has_distinct": inspection.has_distinct,
            "has_having": inspection.has_having,
            "has_subquery": inspection.has_subquery,
            "sql_patterns": self._sql_patterns(example.sql),
            "where_terms": self._compact_sql_clause(inspection.all_where_clause),
        }

    def _format_sql_features(self, sql_features: dict) -> str:
        parts: list[str] = []
        for label, key in (
            ("sql_sources", "sources"),
            ("sql_cte", "cte_names"),
            ("sql_functions", "functions"),
            ("sql_fields", "referenced_fields"),
            ("sql_select", "select_fields"),
            ("sql_group_by", "group_by_fields"),
            ("sql_order_by", "order_by_fields"),
            ("sql_patterns", "sql_patterns"),
        ):
            values = sql_features.get(key, [])
            if isinstance(values, list) and values:
                parts.append(f"{label}: " + " ".join(str(item) for item in values[:80] if item))
        where_terms = sql_features.get("where_terms")
        if isinstance(where_terms, str) and where_terms:
            parts.append(f"sql_where: {where_terms}")
        flags = [
            flag
            for flag in ("has_distinct", "has_having", "has_subquery")
            if sql_features.get(flag)
        ]
        if flags:
            parts.append("sql_flags: " + " ".join(flags))
        return " ".join(parts)

    def _sql_patterns(self, sql: str) -> list[str]:
        pattern_map = {
            "with_cte": r"\bWITH\b",
            "union_all_unpivot": r"\bUNION\s+ALL\b",
            "row_number_version_pick": r"\bROW_NUMBER\s*\(",
            "dense_rank": r"\bDENSE_RANK\s*\(",
            "regexp_version_parse": r"\bREGEXP_SUBSTR\s*\(",
            "substr_time_parse": r"\bSUBSTR\s*\(",
            "add_months_time_shift": r"\bADD_MONTHS\s*\(",
            "to_date_time_parse": r"\bTO_DATE\s*\(",
            "nvl_null_handling": r"\bNVL\s*\(",
            "nullif_zero_guard": r"\bNULLIF\s*\(",
            "case_when": r"\bCASE\b.+\bWHEN\b",
            "left_join": r"\bLEFT\s+JOIN\b",
            "cross_join": r"\bCROSS\s+JOIN\b",
            "full_outer_join": r"\bFULL\s+OUTER\s+JOIN\b",
            "fetch_first_limit": r"\bFETCH\s+FIRST\b",
        }
        return [
            label
            for label, pattern in pattern_map.items()
            if re.search(pattern, sql, re.IGNORECASE | re.DOTALL)
        ]

    def _compact_sql_clause(self, clause: str, *, max_length: int = 1200) -> str:
        compacted = re.sub(r"\s+", " ", clause or "").strip()
        if len(compacted) <= max_length:
            return compacted
        return compacted[:max_length].rsplit(" ", 1)[0]

    def _sql_retrieval_outline(self, sql: str, *, max_length: int = 2200) -> str:
        compacted = re.sub(r"/\*.*?\*/", " ", sql or "", flags=re.DOTALL)
        compacted = re.sub(r"--.*?(?:\n|$)", " ", compacted)
        compacted = re.sub(r"\s+", " ", compacted).strip()
        if len(compacted) <= max_length:
            return "sql_outline: " + compacted
        return "sql_outline: " + compacted[:max_length].rsplit(" ", 1)[0]

    def _build_document(
        self,
        source_type: str,
        source_id: str,
        text_parts: list[str],
        summary: str,
        metadata: dict,
    ) -> dict:
        text = " ".join(part for part in text_parts if part)
        tokens = self._tokenize(text)
        token_counts = Counter(tokens)
        return {
            "source_type": source_type,
            "source_id": source_id,
            "summary": summary,
            "text": text,
            "metadata": metadata,
            "tokens": tokens,
            "token_counts": token_counts,
            "length": max(1, sum(token_counts.values())),
        }

    def _load_tables_metadata(self) -> dict:
        payload = self.metadata_registry.tables_metadata
        return payload if isinstance(payload, dict) else {}

    def _load_business_knowledge(self) -> list[dict]:
        return self.metadata_registry.business_knowledge_entries

    def _load_join_patterns(self) -> list[dict]:
        return self.metadata_registry.join_patterns

    def _build_knowledge_documents(self) -> list[dict]:
        documents: list[dict] = []
        for entry in self.business_knowledge:
            if not isinstance(entry, dict):
                continue
            domains = [str(item) for item in entry.get("domains", []) if item]
            tables = [str(item) for item in entry.get("tables", []) if item]
            keywords = [str(item) for item in entry.get("keywords", []) if item]
            notes = [str(item) for item in entry.get("notes", []) if item]
            if not (domains or tables or keywords or notes):
                continue
            entry_id = str(entry.get("id", "business_knowledge"))
            base_metadata = {
                "kind": "business_knowledge",
                "entry_id": entry_id,
                "domains": domains,
                "tables": tables,
                "keywords": keywords,
            }
            documents.append(
                self._build_document(
                    source_type="knowledge",
                    source_id=f"business_knowledge:{entry_id}",
                    summary=notes[0] if notes else entry_id,
                    metadata={**base_metadata, "chunk_type": "entry"},
                    text_parts=[
                        entry_id,
                        " ".join(domains),
                        " ".join(tables),
                        " ".join(keywords),
                        " ".join(notes),
                    ],
                )
            )
            for note_index, note in enumerate(notes):
                documents.append(
                    self._build_document(
                        source_type="knowledge",
                        source_id=f"business_knowledge:{entry_id}:note:{note_index}",
                        summary=note,
                        metadata={
                            **base_metadata,
                            "chunk_type": "note",
                            "chunk_index": note_index,
                        },
                        text_parts=[
                            entry_id,
                            " ".join(domains),
                            " ".join(tables),
                            " ".join(keywords),
                            note,
                        ],
                    )
                )
        return documents

    def _build_table_schema_documents(self) -> list[dict]:
        documents: list[dict] = []
        for table_name, payload in self.tables_metadata.items():
            if not isinstance(payload, dict):
                continue
            columns = payload.get("columns", [])
            relationships = payload.get("relationships", {})
            time_fields = payload.get("time_fields", {})
            relationship_text = " ".join(
                f"{field} {target}"
                for field, target in relationships.items()
            )
            time_field_text = " ".join(
                f"{field_name} grain={metadata.get('grain')} format={metadata.get('format')}"
                for field_name, metadata in time_fields.items()
                if isinstance(metadata, dict)
            )
            documents.append(
                self._build_document(
                    source_type="table_schema",
                    source_id=table_name,
                    summary=payload.get("description", table_name),
                    metadata={
                        "kind": "table_metadata",
                        "table": table_name,
                        "domains": self._domains_for_tables([table_name]),
                        "main_key": payload.get("MAIN_KEY"),
                        "time_fields": time_fields,
                    },
                    text_parts=[
                        table_name,
                        payload.get("description", ""),
                        " ".join(columns),
                        str(payload.get("MAIN_KEY", "")),
                        time_field_text,
                        relationship_text,
                    ],
                )
            )
        return documents

    def _build_join_pattern_documents(self) -> list[dict]:
        documents: list[dict] = []
        for pattern in self.join_patterns:
            if not isinstance(pattern, dict):
                continue
            pattern_id = str(pattern.get("id", "join_pattern"))
            domains = [str(item) for item in pattern.get("domains", []) if item]
            tables = [str(item) for item in pattern.get("tables", []) if item]
            keywords = [str(item) for item in pattern.get("keywords", []) if item]
            join_path = [str(item) for item in pattern.get("join_path", []) if item]
            notes = [str(item) for item in pattern.get("notes", []) if item]
            documents.append(
                self._build_document(
                    source_type="join_pattern",
                    source_id=pattern_id,
                    summary=notes[0] if notes else pattern_id,
                    metadata={
                        "domains": domains,
                        "tables": tables,
                        "keywords": keywords,
                        "join_path": join_path,
                        "notes": notes,
                    },
                    text_parts=[
                        pattern_id,
                        " ".join(domains),
                        " ".join(tables),
                        " ".join(keywords),
                        " ".join(join_path),
                        " ".join(notes),
                    ],
                )
            )
        return documents

    def _retrieve_text_document_hits(self, query_tokens: list[str]) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for document in self.corpus_documents:
            lexical_score = self._bm25_score(query_tokens, document)
            if lexical_score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    source_type=document["source_type"],
                    source_id=document["source_id"],
                    score=lexical_score,
                    summary=document["summary"],
                    retrieval_channel="keyword",
                    source_score=lexical_score,
                    matched_features=[f"keyword:{lexical_score:.3f}"],
                    metadata=document["metadata"],
                )
            )
        return hits

    def _retrieve_text_vector_hits(self, query_text: str) -> list[RetrievalHit]:
        if not self.vector_retriever.enabled:
            return []
        results = self.vector_retriever.search(
            query_text=query_text,
            top_k=self.vector_top_k,
            source_types=["example", "knowledge", "table_schema", "join_pattern"],
        )
        hits: list[RetrievalHit] = []
        for item in results:
            metadata = dict(item.get("metadata", {}))
            metadata["retrieval_channel"] = "vector"
            hits.append(
                RetrievalHit(
                    source_type=item["source_type"],
                    source_id=item["source_id"],
                    score=float(item["score"]) * 0.45,
                    summary=item.get("summary", item["source_id"]),
                    retrieval_channel="vector",
                    source_score=float(item["score"]),
                    matched_features=[f"vector:{float(item['score']):.3f}"],
                    metadata=metadata,
                )
            )
        return hits

    def _apply_fusion_scores(self, hits: list[RetrievalHit]) -> None:
        """Assign a channel-normalized fusion_score used only for ranking.

        BM25 (keyword) scores span roughly 0-48 while vector cosine scores span
        roughly 0.3-0.9, so adding them directly lets keyword dominate and makes
        the paid vector channel almost irrelevant to ordering. We min-max
        normalize independently inside each channel/source-type bucket using the
        raw source_score, then store the result in fusion_score. The source-type
        bucket matters because _rerank_hits applies quotas by source_type; a
        highly relevant vector join pattern should not be down-ranked only
        because examples or knowledge chunks are stronger in the same channel.
        hit.score keeps its original BM25 magnitude so downstream weighting and
        thresholds that assume that scale stay correct.

        Normalization is a monotonic transform within the bucket that competes
        for quota, so when only one channel is present the relative ordering for
        each source type is unchanged.
        """

        by_bucket: dict[tuple[str, str], list[RetrievalHit]] = {}
        for hit in hits:
            by_bucket.setdefault((hit.retrieval_channel, hit.source_type), []).append(hit)
        for bucket_hits in by_bucket.values():
            raw_scores = [
                hit.source_score if hit.source_score is not None else hit.score
                for hit in bucket_hits
            ]
            lowest = min(raw_scores)
            highest = max(raw_scores)
            span = highest - lowest
            for hit, raw in zip(bucket_hits, raw_scores):
                if span <= 0:
                    hit.fusion_score = 1.0
                else:
                    hit.fusion_score = (raw - lowest) / span

    def _rerank_hits(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        deduplicated: dict[tuple[str, str], RetrievalHit] = {}
        for hit in hits:
            key = self._hit_dedup_key(hit)
            if key not in deduplicated:
                deduplicated[key] = hit
                continue

            existing = deduplicated[key]
            if existing.source_id != hit.source_id:
                merged_features = self._unique(existing.matched_features + hit.matched_features)
                merged_source_score = max(existing.source_score or 0.0, hit.source_score or 0.0)
                # Keep the stronger fusion_score so the surviving variant ranks
                # on the best evidence either channel produced for this entry.
                merged_fusion_score = max(existing.fusion_score or 0.0, hit.fusion_score or 0.0)
                merged_channel = existing.retrieval_channel
                if existing.retrieval_channel != hit.retrieval_channel:
                    merged_channel = "hybrid"
                if hit.score > existing.score:
                    replacement = hit.model_copy(deep=True)
                    replacement.source_score = merged_source_score
                    replacement.fusion_score = merged_fusion_score
                    replacement.matched_features = merged_features
                    replacement.retrieval_channel = merged_channel
                    replacement.metadata = {**existing.metadata, **hit.metadata}
                    deduplicated[key] = replacement
                    continue
                existing.source_score = merged_source_score
                existing.fusion_score = merged_fusion_score
                existing.matched_features = merged_features
                existing.retrieval_channel = merged_channel
                existing.metadata = {**hit.metadata, **existing.metadata}
                continue

            existing.score = round(existing.score + hit.score, 6)
            # Same document hit by both channels: add normalized contributions so
            # hybrid evidence outranks single-channel hits of comparable strength.
            existing.fusion_score = round((existing.fusion_score or 0.0) + (hit.fusion_score or 0.0), 6)
            existing.source_score = max(existing.source_score or 0.0, hit.source_score or 0.0)
            existing.matched_features = self._unique(existing.matched_features + hit.matched_features)
            existing.metadata = {**existing.metadata, **hit.metadata}
            if existing.retrieval_channel != hit.retrieval_channel:
                existing.retrieval_channel = "hybrid"

        ranked = sorted(
            deduplicated.values(),
            key=lambda item: (item.fusion_score or 0.0, self._source_priority(item.source_type)),
            reverse=True,
        )

        selected: list[RetrievalHit] = []
        selected_keys: set[tuple[str, str]] = set()
        counts: Counter[str] = Counter()

        for hit in ranked:
            quota = self.HIT_SOURCE_TYPE_QUOTAS.get(hit.source_type, 1)
            if counts[hit.source_type] >= quota:
                continue
            key = (hit.source_type, hit.source_id)
            selected.append(hit)
            selected_keys.add(key)
            counts[hit.source_type] += 1

        for hit in ranked:
            key = (hit.source_type, hit.source_id)
            if key in selected_keys:
                continue
            selected.append(hit)

        return selected

    def _top_hit_limit(self) -> int:
        return sum(self.HIT_SOURCE_TYPE_QUOTAS.values())

    def _hit_dedup_key(self, hit: RetrievalHit) -> tuple[str, str]:
        if hit.source_type == "knowledge":
            entry_id = hit.metadata.get("entry_id")
            if isinstance(entry_id, str) and entry_id:
                return hit.source_type, f"entry:{entry_id}"
        return hit.source_type, hit.source_id

    def _select_top_hits(self, hits: list[RetrievalHit], *, limit: int) -> list[RetrievalHit]:
        if limit <= 0 or len(hits) <= limit:
            return hits[:limit]

        top_hits = hits[:limit]
        available_types = self._unique([hit.source_type for hit in hits])
        covered_types = {hit.source_type for hit in top_hits}
        missing_types = [source_type for source_type in available_types if source_type not in covered_types]
        if not missing_types:
            return top_hits

        selected_keys: set[tuple[str, str]] = set()
        selected: list[RetrievalHit] = []
        for source_type in available_types[:limit]:
            best_hit = next((hit for hit in hits if hit.source_type == source_type), None)
            if best_hit is None:
                continue
            key = (best_hit.source_type, best_hit.source_id)
            selected.append(best_hit)
            selected_keys.add(key)

        for hit in hits:
            if len(selected) >= limit:
                break
            key = (hit.source_type, hit.source_id)
            if key in selected_keys:
                continue
            selected.append(hit)
            selected_keys.add(key)

        original_rank = {
            (hit.source_type, hit.source_id): index
            for index, hit in enumerate(hits)
        }
        return sorted(
            selected[:limit],
            key=lambda hit: original_rank.get((hit.source_type, hit.source_id), len(hits)),
        )

    def _seed_chinese_tokenizer(self) -> None:
        """Feed domain keywords into jieba so compound business terms survive.

        Without seeding, jieba can split multi-character business terms into
        smaller generic words, weakening lexical matching against the corpus.
        Seeding is best-effort: when jieba is unavailable the bigram fallback
        still applies, so failures here must never break index construction.
        """

        tokenizer = _get_chinese_tokenizer()
        if tokenizer is None:
            return
        seen: set[str] = set()
        for term in self._domain_lexicon_terms():
            normalized = term.strip()
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            try:
                tokenizer.add_word(normalized)
            except Exception:  # pragma: no cover - defensive, jieba is forgiving
                continue

    def _domain_lexicon_terms(self) -> list[str]:
        terms: list[str] = []

        def collect(values: object) -> None:
            if isinstance(values, str):
                if any("一" <= char <= "龥" for char in values):
                    terms.append(values)
                return
            if isinstance(values, list):
                for item in values:
                    collect(item)

        for entry in self.business_knowledge:
            if isinstance(entry, dict):
                collect(entry.get("keywords"))
        for pattern in self.join_patterns:
            if isinstance(pattern, dict):
                collect(pattern.get("keywords"))
        for example in self.examples:
            collect(getattr(example, "coverage_tags", None))
        return terms

    def _tokenize(self, text: str) -> set[str]:
        ascii_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", text)
            if len(token) > 1
        }
        chinese_tokens: set[str] = set()
        for chunk in re.findall(r"[\u4e00-\u9fa5]+", text):
            if len(chunk) < 2:
                continue
            chinese_tokens.update(self._tokenize_chinese_chunk(chunk))
        return ascii_tokens.union(chinese_tokens)

    def _tokenize_chinese_chunk(self, chunk: str) -> set[str]:
        """Tokenize a run of Chinese characters into searchable terms.

        The original implementation kept each whole run as a single token, which
        made BM25 keyword matching effectively whole-phrase-only for Chinese and
        collapsed recall onto the vector channel. We now segment with jieba when
        available, and always add character bigrams plus the whole chunk so that
        both word-level and substring matches contribute to lexical scoring.
        """

        tokens: set[str] = {chunk}
        tokenizer = _get_chinese_tokenizer()
        if tokenizer is not None:
            tokens.update(
                word
                for word in tokenizer.cut(chunk, HMM=True)
                if len(word) >= 2 and not word.isspace()
            )
        for index in range(len(chunk) - 1):
            tokens.add(chunk[index : index + 2])
        return tokens

    def _bm25_score(self, query_tokens: list[str], document: dict | None) -> float:
        if document is None or not query_tokens:
            return 0.0

        score = 0.0
        doc_length = max(1, document["length"])
        average_length = max(1.0, self.average_doc_length)
        total_docs = max(1, len(self.corpus_documents))
        k1 = 1.5
        b = 0.75

        for token in query_tokens:
            tf = document["token_counts"].get(token, 0)
            if tf <= 0:
                continue
            df = self.document_frequency.get(token, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_length / average_length)
            score += idf * numerator / denominator
        return round(score, 6)

    def _count_hits_by_source(self, hits: list[RetrievalHit]) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for hit in hits:
            counter[hit.source_type] += 1
        return dict(counter)

    def _count_hits_by_channel(self, hits: list[RetrievalHit]) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for hit in hits:
            counter[hit.retrieval_channel] += 1
        return dict(counter)

    def _source_priority(self, source_type: str) -> int:
        priorities = {
            "example": 3,
            "table_schema": 3,
            "join_pattern": 2,
            "knowledge": 1,
        }
        return priorities.get(source_type, 0)

    def _retrieval_channels(self, *, vector_used: bool | None = None) -> list[str]:
        channels = ["keyword"]
        if vector_used is True or (vector_used is None and self.vector_retriever.enabled):
            channels.append("vector")
        return channels

    def _build_table_domains(self) -> dict[str, list[str]]:
        table_domains: dict[str, list[str]] = {}
        for example in self.examples:
            self._add_table_domains(
                table_domains,
                tables=example.tables,
                domains=[example.subject_domain],
            )
        for entry in self.business_knowledge:
            if not isinstance(entry, dict):
                continue
            self._add_table_domains(
                table_domains,
                tables=[str(item) for item in entry.get("tables", []) if item],
                domains=[str(item) for item in entry.get("domains", []) if item],
            )
        for pattern in self.join_patterns:
            if not isinstance(pattern, dict):
                continue
            self._add_table_domains(
                table_domains,
                tables=[str(item) for item in pattern.get("tables", []) if item],
                domains=[str(item) for item in pattern.get("domains", []) if item],
            )
        return table_domains

    def _add_table_domains(
        self,
        table_domains: dict[str, list[str]],
        *,
        tables: list[str],
        domains: list[str],
    ) -> None:
        normalized_domains = [
            domain_name
            for domain_name in domains
            if isinstance(domain_name, str) and domain_name.strip() and domain_name != "unknown"
        ]
        for table_name in tables:
            if not isinstance(table_name, str) or not table_name.strip():
                continue
            if table_name not in self.tables_metadata:
                continue
            for domain_name in normalized_domains:
                table_domains.setdefault(table_name, [])
                if domain_name not in table_domains[table_name]:
                    table_domains[table_name].append(domain_name)

    def _domains_for_tables(self, tables: list[str]) -> list[str]:
        domains: list[str] = []
        for table_name in tables:
            for domain_name in self.table_domains.get(table_name, []):
                if domain_name not in domains:
                    domains.append(domain_name)
        return domains

    def _domains_from_hits(self, hits: list[RetrievalHit]) -> list[str]:
        domains: list[str] = []
        for hit in hits:
            metadata_domains = hit.metadata.get("domains", [])
            if isinstance(metadata_domains, list):
                for domain_name in metadata_domains:
                    if isinstance(domain_name, str) and domain_name and domain_name != "unknown" and domain_name not in domains:
                        domains.append(domain_name)
            subject_domain = hit.metadata.get("subject_domain")
            if isinstance(subject_domain, str) and subject_domain and subject_domain != "unknown" and subject_domain not in domains:
                domains.append(subject_domain)
            tables = hit.metadata.get("tables", [])
            if isinstance(tables, list):
                for domain_name in self._domains_for_tables([table for table in tables if isinstance(table, str)]):
                    if domain_name not in domains:
                        domains.append(domain_name)
            table_name = hit.metadata.get("table")
            if isinstance(table_name, str):
                for domain_name in self._domains_for_tables([table_name]):
                    if domain_name not in domains:
                        domains.append(domain_name)
        return domains

    def _metrics_from_hits(self, hits: list[RetrievalHit]) -> list[str]:
        metrics: list[str] = []
        for hit in hits:
            metadata_metrics = hit.metadata.get("metrics", [])
            if not isinstance(metadata_metrics, list):
                continue
            for metric in metadata_metrics:
                if isinstance(metric, str) and metric and metric not in metrics:
                    metrics.append(metric)
        return metrics

    def _unique(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not item:
                continue
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _stringify_filter_value(self, value) -> str:
        if isinstance(value, list):
            return " ".join(str(item) for item in value if item is not None)
        if isinstance(value, dict):
            return " ".join(str(item) for item in value.values() if item is not None)
        if value is None:
            return ""
        return str(value)
