from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re

from backend.app.config import BUSINESS_KNOWLEDGE_PATH, EXAMPLES_TEMPLATE_PATH, TABLES_METADATA_PATH
from backend.app.models.classification import QueryIntent
from backend.app.models.example_library import ExampleRecord
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.vector_retriever import VectorRetriever


class RetrievalService:
    def __init__(
        self,
        domain_config: dict,
        semantic_runtime: SemanticRuntime | None = None,
        examples_path: Path = EXAMPLES_TEMPLATE_PATH,
        tables_metadata_path: Path = TABLES_METADATA_PATH,
        business_knowledge_path: Path = BUSINESS_KNOWLEDGE_PATH,
        vector_retriever: VectorRetriever | None = None,
        vector_top_k: int = 3,
    ) -> None:
        self.domain_config = domain_config
        self.semantic_runtime = semantic_runtime or SemanticRuntime(domain_config)
        self.examples_path = examples_path
        self.tables_metadata_path = tables_metadata_path
        self.business_knowledge_path = business_knowledge_path
        self.vector_retriever = vector_retriever or VectorRetriever(provider="disabled")
        self.vector_top_k = vector_top_k
        self.examples = self._load_examples()
        self.tables_metadata = self._load_tables_metadata()
        self.business_knowledge = self._load_business_knowledge()
        self.corpus_documents: list[dict] = []
        self.document_frequency: Counter[str] = Counter()
        self.average_doc_length = 1.0
        self.document_lookup: dict[tuple[str, str], dict] = {}
        self._refresh_indexes()

    def retrieve(self, query_intent: QueryIntent) -> RetrievalContext:
        domains = [query_intent.subject_domain] if query_intent.subject_domain != "unknown" else []
        retrieval_terms = self._build_retrieval_terms(query_intent)
        query_tokens = self._query_tokens(query_intent, retrieval_terms)
        hits: list[RetrievalHit] = []
        hits.extend(self._retrieve_example_hits(query_intent, query_tokens))
        hits.extend(self._retrieve_metric_hits(query_intent, query_tokens))
        hits.extend(self._retrieve_knowledge_hits(query_intent, query_tokens))
        hits.extend(self._retrieve_vector_hits(query_intent, retrieval_terms))
        hits = self._rerank_hits(hits)
        top_hits = hits[:5]
        return RetrievalContext(
            domains=domains,
            metrics=query_intent.matched_metrics,
            retrieval_terms=retrieval_terms,
            retrieval_channels=self._retrieval_channels(),
            hits=top_hits,
            hit_count_by_source=self._count_hits_by_source(top_hits),
            hit_count_by_channel=self._count_hits_by_channel(top_hits),
        )

    def reload(self) -> None:
        self.examples = self._load_examples()
        self.tables_metadata = self._load_tables_metadata()
        self.business_knowledge = self._load_business_knowledge()
        self._refresh_indexes()

    def summarize_retrieval(self, retrieval: RetrievalContext) -> dict:
        return {
            "channels": retrieval.retrieval_channels,
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
        return {
            "vector_enabled": self.vector_retriever.enabled,
            "vector_provider": self.vector_retriever.provider,
            "document_count": len(self.corpus_documents),
            "document_count_by_source": dict(
                Counter(document["source_type"] for document in self.corpus_documents)
            ),
            "example_count": len(self.examples),
        }

    def validate_example(self, payload: dict | ExampleRecord) -> ExampleRecord:
        if isinstance(payload, ExampleRecord):
            return payload
        return ExampleRecord(**payload)

    def _load_examples(self) -> list[ExampleRecord]:
        with self.examples_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return [self.validate_example(item) for item in payload]

    def _refresh_indexes(self) -> None:
        self.corpus_documents = (
            self._build_example_documents()
            + self._build_metric_documents()
            + self._build_knowledge_documents()
        )
        self.document_frequency = Counter()
        self.document_lookup = {
            (document["source_type"], document["source_id"]): document
            for document in self.corpus_documents
        }
        total_length = 0
        for document in self.corpus_documents:
            total_length += document["length"]
            self.document_frequency.update(set(document["token_counts"].keys()))
        self.average_doc_length = total_length / len(self.corpus_documents) if self.corpus_documents else 1.0
        self.vector_retriever.index_documents(self.corpus_documents)

    def _build_example_documents(self) -> list[dict]:
        documents: list[dict] = []
        for example in self.examples:
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
                        "tables": example.tables,
                    },
                    text_parts=[
                        example.question,
                        example.normalized_question,
                        example.intent,
                        example.scenario or "",
                        " ".join(example.coverage_tags),
                        " ".join(example.metrics),
                        " ".join(example.entities),
                        " ".join(example.dimensions),
                        example.notes or "",
                    ],
                )
            )
        return documents

    def _build_metric_documents(self) -> list[dict]:
        documents: list[dict] = []
        for metric in self.domain_config.get("metrics", []):
            definitions = " ".join(
                f"{item.get('table', '')} {item.get('expression', '')}"
                for item in metric.get("definitions", [])
            )
            documents.append(
                self._build_document(
                    source_type="metric",
                    source_id=metric["name"],
                    summary=f"{metric['name']}: {', '.join(metric.get('aliases', [])[:3])}",
                    metadata={
                        "semantic_column": metric.get("semantic_column"),
                        "definitions": metric.get("definitions", []),
                    },
                    text_parts=[
                        metric["name"],
                        metric.get("semantic_column", ""),
                        " ".join(metric.get("aliases", [])),
                        definitions,
                    ],
                )
            )
        return documents

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
        with self.tables_metadata_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return payload if isinstance(payload, dict) else {}

    def _load_business_knowledge(self) -> list[dict]:
        try:
            with self.business_knowledge_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except Exception:
            return []
        entries = payload.get("entries", [])
        return entries if isinstance(entries, list) else []

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
            documents.append(
                self._build_document(
                    source_type="knowledge",
                    source_id=f"business_knowledge:{entry_id}",
                    summary=notes[0] if notes else entry_id,
                    metadata={
                        "kind": "business_knowledge",
                        "entry_id": entry_id,
                        "domains": domains,
                        "tables": tables,
                        "keywords": keywords,
                    },
                    text_parts=[
                        entry_id,
                        " ".join(domains),
                        " ".join(tables),
                        " ".join(keywords),
                        " ".join(notes),
                    ],
                )
            )
        for table_name, payload in self.tables_metadata.items():
            if not isinstance(payload, dict):
                continue
            columns = payload.get("columns", [])
            relationships = payload.get("relationships", {})
            relationship_text = " ".join(
                f"{field} {target}"
                for field, target in relationships.items()
            )
            documents.append(
                self._build_document(
                    source_type="knowledge",
                    source_id=f"table:{table_name}",
                    summary=payload.get("description", table_name),
                    metadata={
                        "kind": "table_metadata",
                        "table": table_name,
                        "main_key": payload.get("MAIN_KEY"),
                        "date_col": payload.get("date_col"),
                        "month_col": payload.get("month_col"),
                        "version_col": payload.get("version_col"),
                    },
                    text_parts=[
                        table_name,
                        payload.get("description", ""),
                        " ".join(columns),
                        str(payload.get("MAIN_KEY", "")),
                        relationship_text,
                    ],
                )
            )
        return documents

    def _retrieve_example_hits(
        self,
        query_intent: QueryIntent,
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for example in self.examples:
            if (
                query_intent.subject_domain != "unknown"
                and example.subject_domain != query_intent.subject_domain
            ):
                continue
            structured_score, matched_features = self._score_example(example, query_intent)
            lexical_score = self._bm25_score(
                query_tokens,
                self._lookup_document("example", example.id),
            )
            score = structured_score + lexical_score * 0.35
            if score <= 0:
                continue
            if lexical_score > 0:
                matched_features.append(f"keyword:{lexical_score:.3f}")
            hits.append(
                RetrievalHit(
                    source_type="example",
                    source_id=example.id,
                    score=score,
                    summary=example.normalized_question,
                    retrieval_channel="structured",
                    source_score=score,
                    matched_features=matched_features,
                    metadata={
                        "intent": example.intent,
                        "scenario": example.scenario,
                        "coverage_tags": example.coverage_tags,
                        "question_type": example.question_type,
                        "tables": example.tables,
                    },
                )
            )
        return hits

    def _retrieve_metric_hits(
        self,
        query_intent: QueryIntent,
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for metric in self.domain_config.get("metrics", []):
            score = 0.0
            matched_features: list[str] = []
            metric_name = metric["name"]
            if metric_name in query_intent.matched_metrics:
                score += 0.8
                matched_features.append(f"metric:{metric_name}")
            elif query_intent.subject_domain != "unknown":
                metric_tables = set(item.get("table") for item in metric.get("definitions", []))
                domain_tables = set(self.semantic_runtime.domain_tables(query_intent.subject_domain))
                if metric_tables.intersection(domain_tables):
                    score += 0.15
                    matched_features.append("domain_metric_overlap")

            lexical_score = self._bm25_score(query_tokens, self._lookup_document("metric", metric_name))
            score += lexical_score * 0.3
            if lexical_score > 0:
                matched_features.append(f"keyword:{lexical_score:.3f}")
            if score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    source_type="metric",
                    source_id=metric_name,
                    score=score,
                    summary=f"{metric_name}: {', '.join(metric.get('aliases', [])[:3])}",
                    retrieval_channel="structured",
                    source_score=score,
                    matched_features=matched_features,
                    metadata={
                        "semantic_column": metric.get("semantic_column"),
                        "definitions": metric.get("definitions", []),
                    },
                )
            )
        return hits

    def _retrieve_knowledge_hits(
        self,
        query_intent: QueryIntent,
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        metric_tables = {
            table
            for metric in query_intent.matched_metrics
            for table in self.semantic_runtime.metric_tables(metric)
        }
        domain_tables = (
            set(self.semantic_runtime.domain_tables(query_intent.subject_domain))
            if query_intent.subject_domain != "unknown"
            else set()
        )
        for document in self.corpus_documents:
            if document["source_type"] != "knowledge":
                continue
            score = 0.0
            matched_features: list[str] = []
            lexical_score = self._bm25_score(query_tokens, document)
            if lexical_score > 0:
                score += lexical_score * 0.5
                matched_features.append(f"keyword:{lexical_score:.3f}")

            metadata = document.get("metadata", {})
            table_name = metadata.get("table")
            knowledge_domains = {
                str(item).lower()
                for item in metadata.get("domains", [])
                if item
            }
            if (
                query_intent.subject_domain != "unknown"
                and query_intent.subject_domain.lower() in knowledge_domains
            ):
                score += 0.35
                matched_features.append(f"domain:{query_intent.subject_domain}")

            knowledge_tables = {
                str(item)
                for item in metadata.get("tables", [])
                if item
            }
            if table_name and query_intent.subject_domain != "unknown":
                if table_name in domain_tables:
                    score += 0.25
                    matched_features.append(f"domain_table:{table_name}")
            matched_domain_tables = sorted(knowledge_tables.intersection(domain_tables))
            if matched_domain_tables:
                score += 0.2
                matched_features.append("knowledge_domain_tables:" + ",".join(matched_domain_tables[:3]))

            if table_name:
                if table_name in metric_tables:
                    score += 0.25
                    matched_features.append(f"metric_table:{table_name}")
            matched_metric_tables = sorted(knowledge_tables.intersection(metric_tables))
            if matched_metric_tables:
                score += 0.25
                matched_features.append("knowledge_metric_tables:" + ",".join(matched_metric_tables[:3]))

            if score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    source_type=document["source_type"],
                    source_id=document["source_id"],
                    score=score,
                    summary=document["summary"],
                    retrieval_channel="keyword",
                    source_score=score,
                    matched_features=matched_features,
                    metadata=document["metadata"],
                )
            )
        return hits

    def _retrieve_vector_hits(
        self,
        query_intent: QueryIntent,
        retrieval_terms: list[str],
    ) -> list[RetrievalHit]:
        if not self.vector_retriever.enabled:
            return []

        query_text = " ".join(
            [
                query_intent.normalized_question,
                *retrieval_terms,
                *query_intent.matched_metrics,
                *query_intent.matched_entities,
            ]
        ).strip()
        source_types = ["example", "metric", "knowledge"]
        results = self.vector_retriever.search(
            query_text=query_text,
            top_k=self.vector_top_k,
            source_types=source_types,
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

    def _score_example(
        self,
        example: ExampleRecord,
        query_intent: QueryIntent,
    ) -> tuple[float, list[str]]:
        score = 0.0
        matched_features: list[str] = []
        example_metrics = set(example.metrics)
        example_entities = set(example.entities)
        example_filter_fields = {item.field for item in example.filters}
        parse_filter_fields = {item.field for item in query_intent.filters}
        matched_metrics = example_metrics.intersection(query_intent.matched_metrics)
        matched_entities = example_entities.intersection(query_intent.matched_entities)
        matched_filter_fields = example_filter_fields.intersection(parse_filter_fields)
        lexical_overlap = self._lexical_overlap(example, query_intent)

        if matched_metrics:
            score += 0.6
            matched_features.append("metrics:" + ",".join(sorted(matched_metrics)))
        if matched_entities:
            score += 0.2
            matched_features.append("entities:" + ",".join(sorted(matched_entities)))
        if matched_filter_fields:
            score += 0.15
            matched_features.append("filters:" + ",".join(sorted(matched_filter_fields)))
        if query_intent.time_context.grain != "unknown":
            example_time_fields = example_filter_fields.intersection({"biz_date", "biz_month"})
            if query_intent.time_context.grain == "day" and "biz_date" in example_time_fields:
                score += 0.1
                matched_features.append("time_grain:day")
            if query_intent.time_context.grain == "month" and "biz_month" in example_time_fields:
                score += 0.1
                matched_features.append("time_grain:month")
        if query_intent.version_context is not None and "PM_VERSION" in example_filter_fields:
            score += 0.1
            matched_features.append("version:PM_VERSION")
        if example.question_type == "follow_up" and query_intent.has_follow_up_cue:
            score += 0.15
            matched_features.append("question_type:follow_up")
        if example.subject_domain == query_intent.subject_domain:
            score += 0.2
            matched_features.append(f"domain:{example.subject_domain}")
        if lexical_overlap:
            score += min(0.2, 0.05 * len(lexical_overlap))
            matched_features.append("lexical:" + ",".join(lexical_overlap))
        return score, matched_features

    def _rerank_hits(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        deduplicated: dict[tuple[str, str], RetrievalHit] = {}
        for hit in hits:
            key = (hit.source_type, hit.source_id)
            if key not in deduplicated:
                deduplicated[key] = hit
                continue

            existing = deduplicated[key]
            existing.score = round(existing.score + hit.score, 6)
            existing.source_score = max(existing.source_score or 0.0, hit.source_score or 0.0)
            existing.matched_features = self._unique(existing.matched_features + hit.matched_features)
            existing.metadata = {**existing.metadata, **hit.metadata}
            if existing.retrieval_channel != hit.retrieval_channel:
                existing.retrieval_channel = "hybrid"

        ranked = sorted(
            deduplicated.values(),
            key=lambda item: (item.score, self._source_priority(item.source_type)),
            reverse=True,
        )

        quotas = {
            "example": 2,
            "metric": 1,
            "knowledge": 1,
        }
        selected: list[RetrievalHit] = []
        selected_keys: set[tuple[str, str]] = set()
        counts: Counter[str] = Counter()

        for hit in ranked:
            quota = quotas.get(hit.source_type, 1)
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

    def _build_retrieval_terms(self, query_intent: QueryIntent) -> list[str]:
        terms: list[str] = []
        terms.extend(query_intent.matched_metrics)
        terms.extend(query_intent.matched_entities)
        terms.extend(item.field for item in query_intent.filters)
        if query_intent.time_context.grain != "unknown":
            terms.append(f"time_grain:{query_intent.time_context.grain}")
        if query_intent.version_context and query_intent.version_context.field:
            terms.append(f"version_field:{query_intent.version_context.field}")
        if query_intent.has_follow_up_cue:
            terms.append("question_type:follow_up_like")
        return self._unique(terms)

    def _query_tokens(self, query_intent: QueryIntent, retrieval_terms: list[str]) -> list[str]:
        return sorted(
            self._tokenize(
                f"{query_intent.normalized_question} {' '.join(retrieval_terms)}"
            )
        )

    def _lexical_overlap(self, example: ExampleRecord, query_intent: QueryIntent) -> list[str]:
        query_tokens = self._tokenize(query_intent.normalized_question)
        example_tokens = self._tokenize(f"{example.question} {example.normalized_question}")
        overlap = sorted(query_tokens.intersection(example_tokens))
        return overlap[:4]

    def _tokenize(self, text: str) -> set[str]:
        ascii_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", text)
            if len(token) > 1
        }
        chinese_chunks = {
            chunk
            for chunk in re.findall(r"[\u4e00-\u9fa5]{2,}", text)
            if len(chunk) >= 2
        }
        return ascii_tokens.union(chinese_chunks)

    def _lookup_document(self, source_type: str, source_id: str) -> dict | None:
        return self.document_lookup.get((source_type, source_id))

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
            "metric": 1,
            "knowledge": 1,
        }
        return priorities.get(source_type, 0)

    def _retrieval_channels(self) -> list[str]:
        channels = ["structured", "keyword"]
        if self.vector_retriever.enabled:
            channels.append("vector")
        return channels

    def _unique(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
