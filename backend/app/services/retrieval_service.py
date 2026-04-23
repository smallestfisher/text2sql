from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re

from backend.app.config import EXAMPLES_TEMPLATE_PATH, README_TEXT_PATH, TABLES_METADATA_PATH
from backend.app.models.classification import SemanticParse
from backend.app.models.example_library import ExampleRecord
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.services.semantic_runtime import SemanticRuntime
from backend.app.services.vector_retriever import VectorRetriever


class RetrievalService:
    def __init__(
        self,
        semantic_layer: dict,
        semantic_runtime: SemanticRuntime | None = None,
        examples_path: Path = EXAMPLES_TEMPLATE_PATH,
        tables_metadata_path: Path = TABLES_METADATA_PATH,
        readme_path: Path = README_TEXT_PATH,
        vector_retriever: VectorRetriever | None = None,
        vector_top_k: int = 3,
    ) -> None:
        self.semantic_layer = semantic_layer
        self.semantic_runtime = semantic_runtime or SemanticRuntime(semantic_layer)
        self.examples_path = examples_path
        self.tables_metadata_path = tables_metadata_path
        self.readme_path = readme_path
        self.vector_retriever = vector_retriever or VectorRetriever(provider="disabled")
        self.vector_top_k = vector_top_k
        self.examples = self._load_examples()
        self.tables_metadata = self._load_tables_metadata()
        self.readme_text = self._load_readme_text()
        self.corpus_documents: list[dict] = []
        self.document_frequency: Counter[str] = Counter()
        self.average_doc_length = 1.0
        self.document_lookup: dict[tuple[str, str], dict] = {}
        self._refresh_indexes()

    def retrieve(self, semantic_parse: SemanticParse) -> RetrievalContext:
        domains = [semantic_parse.subject_domain] if semantic_parse.subject_domain != "unknown" else []
        semantic_views = self.semantic_runtime.rank_semantic_views(
            domain_name=semantic_parse.subject_domain,
            metrics=semantic_parse.matched_metrics,
            dimensions=[],
            filters=semantic_parse.filters,
            version_field=semantic_parse.version_context.field if semantic_parse.version_context else None,
        )
        retrieval_terms = self._build_retrieval_terms(semantic_parse)
        query_tokens = self._query_tokens(semantic_parse, retrieval_terms)
        hits: list[RetrievalHit] = []
        hits.extend(self._retrieve_example_hits(semantic_parse, query_tokens))
        hits.extend(self._retrieve_semantic_view_hits(semantic_parse, semantic_views, query_tokens))
        hits.extend(self._retrieve_metric_hits(semantic_parse, query_tokens))
        hits.extend(self._retrieve_knowledge_hits(semantic_parse, query_tokens))
        hits.extend(self._retrieve_vector_hits(semantic_parse, retrieval_terms))
        hits = self._rerank_hits(hits)
        return RetrievalContext(
            domains=domains,
            semantic_views=semantic_views,
            metrics=semantic_parse.matched_metrics,
            retrieval_terms=retrieval_terms,
            retrieval_channels=self._retrieval_channels(),
            hits=hits[:5],
            hit_count_by_source=self._count_hits_by_source(hits[:5]),
        )

    def reload(self) -> None:
        self.examples = self._load_examples()
        self.tables_metadata = self._load_tables_metadata()
        self.readme_text = self._load_readme_text()
        self._refresh_indexes()

    def summarize_retrieval(self, retrieval: RetrievalContext) -> dict:
        return {
            "channels": retrieval.retrieval_channels,
            "hit_count_by_source": retrieval.hit_count_by_source,
            "top_hits": [
                {
                    "source_type": hit.source_type,
                    "source_id": hit.source_id,
                    "score": hit.score,
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
            + self._build_semantic_view_documents()
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
                        "semantic_views": example.semantic_views,
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
                        " ".join(example.semantic_views),
                        example.notes or "",
                    ],
                )
            )
        return documents

    def _build_semantic_view_documents(self) -> list[dict]:
        documents: list[dict] = []
        for view in self.semantic_layer.get("semantic_views", []):
            documents.append(
                self._build_document(
                    source_type="semantic_view",
                    source_id=view["name"],
                    summary=view.get("purpose", view["name"]),
                    metadata={
                        "source_tables": view.get("source_tables", []),
                        "output_fields": view.get("output_fields", []),
                    },
                    text_parts=[
                        view["name"],
                        view.get("purpose", ""),
                        " ".join(view.get("source_tables", [])),
                        " ".join(view.get("output_fields", [])),
                    ],
                )
            )
        return documents

    def _build_metric_documents(self) -> list[dict]:
        documents: list[dict] = []
        for metric in self.semantic_layer.get("metrics", []):
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

    def _load_readme_text(self) -> str:
        return self.readme_path.read_text(encoding="utf-8")

    def _build_knowledge_documents(self) -> list[dict]:
        documents: list[dict] = []
        if self.readme_text.strip():
            documents.append(
                self._build_document(
                    source_type="knowledge",
                    source_id="readme_txt",
                    summary="业务关系补充说明",
                    metadata={"kind": "business_notes"},
                    text_parts=[self.readme_text],
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
        semantic_parse: SemanticParse,
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for example in self.examples:
            if (
                semantic_parse.subject_domain != "unknown"
                and example.subject_domain != semantic_parse.subject_domain
            ):
                continue
            structured_score, matched_features = self._score_example(example, semantic_parse)
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
                    matched_features=matched_features,
                    metadata={
                        "intent": example.intent,
                        "scenario": example.scenario,
                        "coverage_tags": example.coverage_tags,
                        "question_type": example.question_type,
                        "semantic_views": example.semantic_views,
                        "tables": example.tables,
                    },
                )
            )
        return hits

    def _retrieve_semantic_view_hits(
        self,
        semantic_parse: SemanticParse,
        ranked_semantic_views: list[str],
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        ranked_index = {view_name: index for index, view_name in enumerate(ranked_semantic_views)}
        for view in self.semantic_layer.get("semantic_views", []):
            view_name = view["name"]
            if (
                semantic_parse.subject_domain != "unknown"
                and view_name not in ranked_semantic_views
            ):
                continue
            score = 0.0
            matched_features: list[str] = []
            output_fields = set(view.get("output_fields", []))
            source_tables = set(view.get("source_tables", []))
            domain_tables = set(self.semantic_runtime.domain_tables(semantic_parse.subject_domain))
            metric_columns = {
                self.semantic_runtime.metric_column(metric)
                for metric in semantic_parse.matched_metrics
            }
            parse_filter_fields = {item.field for item in semantic_parse.filters}

            matched_metric_fields = sorted(output_fields.intersection(metric_columns))
            matched_filter_fields = sorted(output_fields.intersection(parse_filter_fields))
            matched_domain_tables = sorted(source_tables.intersection(domain_tables))

            if view_name in ranked_index:
                rank_score = max(0, 0.45 - ranked_index[view_name] * 0.1)
                score += rank_score
                matched_features.append(f"view_rank:{ranked_index[view_name] + 1}")
            if matched_metric_fields:
                score += 0.35
                matched_features.append("metric_fields:" + ",".join(matched_metric_fields))
            if matched_filter_fields:
                score += 0.2
                matched_features.append("filter_fields:" + ",".join(matched_filter_fields))
            if matched_domain_tables:
                score += 0.15
                matched_features.append("domain_tables:" + ",".join(matched_domain_tables))

            lexical_score = self._bm25_score(query_tokens, self._lookup_document("semantic_view", view_name))
            score += lexical_score * 0.4
            if lexical_score > 0:
                matched_features.append(f"keyword:{lexical_score:.3f}")
            if score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    source_type="semantic_view",
                    source_id=view_name,
                    score=score,
                    summary=view.get("purpose", view_name),
                    matched_features=matched_features,
                    metadata={
                        "source_tables": view.get("source_tables", []),
                        "output_fields": view.get("output_fields", []),
                    },
                )
            )
        return hits

    def _retrieve_metric_hits(
        self,
        semantic_parse: SemanticParse,
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for metric in self.semantic_layer.get("metrics", []):
            score = 0.0
            matched_features: list[str] = []
            metric_name = metric["name"]
            if metric_name in semantic_parse.matched_metrics:
                score += 0.8
                matched_features.append(f"metric:{metric_name}")
            elif semantic_parse.subject_domain != "unknown":
                metric_tables = set(item.get("table") for item in metric.get("definitions", []))
                domain_tables = set(self.semantic_runtime.domain_tables(semantic_parse.subject_domain))
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
        semantic_parse: SemanticParse,
        query_tokens: list[str],
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
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
            if table_name and semantic_parse.subject_domain != "unknown":
                domain_tables = set(self.semantic_runtime.domain_tables(semantic_parse.subject_domain))
                if table_name in domain_tables:
                    score += 0.25
                    matched_features.append(f"domain_table:{table_name}")

            if table_name:
                metric_tables = {
                    table
                    for metric in semantic_parse.matched_metrics
                    for table in self.semantic_runtime.metric_tables(metric)
                }
                if table_name in metric_tables:
                    score += 0.25
                    matched_features.append(f"metric_table:{table_name}")

            if score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    source_type=document["source_type"],
                    source_id=document["source_id"],
                    score=score,
                    summary=document["summary"],
                    matched_features=matched_features,
                    metadata=document["metadata"],
                )
            )
        return hits

    def _retrieve_vector_hits(
        self,
        semantic_parse: SemanticParse,
        retrieval_terms: list[str],
    ) -> list[RetrievalHit]:
        if not self.vector_retriever.enabled:
            return []

        query_text = " ".join(
            [
                semantic_parse.normalized_question,
                *retrieval_terms,
                *semantic_parse.matched_metrics,
                *semantic_parse.matched_entities,
            ]
        ).strip()
        source_types = ["example", "semantic_view", "metric", "knowledge"]
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
                    matched_features=[f"vector:{float(item['score']):.3f}"],
                    metadata=metadata,
                )
            )
        return hits

    def _score_example(
        self,
        example: ExampleRecord,
        semantic_parse: SemanticParse,
    ) -> tuple[float, list[str]]:
        score = 0.0
        matched_features: list[str] = []
        example_metrics = set(example.metrics)
        example_entities = set(example.entities)
        example_filter_fields = {item.field for item in example.filters}
        parse_filter_fields = {item.field for item in semantic_parse.filters}
        matched_metrics = example_metrics.intersection(semantic_parse.matched_metrics)
        matched_entities = example_entities.intersection(semantic_parse.matched_entities)
        matched_filter_fields = example_filter_fields.intersection(parse_filter_fields)
        lexical_overlap = self._lexical_overlap(example, semantic_parse)

        if matched_metrics:
            score += 0.6
            matched_features.append("metrics:" + ",".join(sorted(matched_metrics)))
        if matched_entities:
            score += 0.2
            matched_features.append("entities:" + ",".join(sorted(matched_entities)))
        if matched_filter_fields:
            score += 0.15
            matched_features.append("filters:" + ",".join(sorted(matched_filter_fields)))
        if semantic_parse.time_context.grain != "unknown":
            example_time_fields = example_filter_fields.intersection({"biz_date", "biz_month"})
            if semantic_parse.time_context.grain == "day" and "biz_date" in example_time_fields:
                score += 0.1
                matched_features.append("time_grain:day")
            if semantic_parse.time_context.grain == "month" and "biz_month" in example_time_fields:
                score += 0.1
                matched_features.append("time_grain:month")
        if semantic_parse.version_context is not None and "PM_VERSION" in example_filter_fields:
            score += 0.1
            matched_features.append("version:PM_VERSION")
        if example.question_type == "follow_up" and semantic_parse.has_follow_up_cue:
            score += 0.15
            matched_features.append("question_type:follow_up")
        if example.subject_domain == semantic_parse.subject_domain:
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
            existing.matched_features = self._unique(existing.matched_features + hit.matched_features)
            existing.metadata = {**existing.metadata, **hit.metadata}
        ranked = sorted(
            deduplicated.values(),
            key=lambda item: (item.score, self._source_priority(item.source_type)),
            reverse=True,
        )
        return ranked

    def _build_retrieval_terms(self, semantic_parse: SemanticParse) -> list[str]:
        terms: list[str] = []
        terms.extend(semantic_parse.matched_metrics)
        terms.extend(semantic_parse.matched_entities)
        terms.extend(item.field for item in semantic_parse.filters)
        if semantic_parse.time_context.grain != "unknown":
            terms.append(f"time_grain:{semantic_parse.time_context.grain}")
        if semantic_parse.version_context and semantic_parse.version_context.field:
            terms.append(f"version_field:{semantic_parse.version_context.field}")
        if semantic_parse.has_follow_up_cue:
            terms.append("question_type:follow_up_like")
        return self._unique(terms)

    def _query_tokens(self, semantic_parse: SemanticParse, retrieval_terms: list[str]) -> list[str]:
        return sorted(
            self._tokenize(
                f"{semantic_parse.normalized_question} {' '.join(retrieval_terms)}"
            )
        )

    def _lexical_overlap(self, example: ExampleRecord, semantic_parse: SemanticParse) -> list[str]:
        query_tokens = self._tokenize(semantic_parse.normalized_question)
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

    def _source_priority(self, source_type: str) -> int:
        priorities = {
            "example": 3,
            "semantic_view": 2,
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
