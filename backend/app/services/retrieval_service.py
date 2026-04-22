from __future__ import annotations

import json
from pathlib import Path
import re

from backend.app.config import EXAMPLES_TEMPLATE_PATH
from backend.app.models.classification import SemanticParse
from backend.app.models.example_library import ExampleRecord
from backend.app.models.retrieval import RetrievalContext, RetrievalHit
from backend.app.services.semantic_runtime import SemanticRuntime


class RetrievalService:
    def __init__(
        self,
        semantic_layer: dict,
        semantic_runtime: SemanticRuntime | None = None,
        examples_path: Path = EXAMPLES_TEMPLATE_PATH,
    ) -> None:
        self.semantic_layer = semantic_layer
        self.semantic_runtime = semantic_runtime or SemanticRuntime(semantic_layer)
        self.examples_path = examples_path
        self.examples = self._load_examples()

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
        hits: list[RetrievalHit] = []

        for example in self.examples:
            if (
                semantic_parse.subject_domain != "unknown"
                and example.subject_domain != semantic_parse.subject_domain
            ):
                continue
            score, matched_features = self._score_example(example, semantic_parse)
            if score <= 0:
                continue
            hits.append(
                RetrievalHit(
                    source_type="example",
                    source_id=example.id,
                    score=score,
                    summary=example.normalized_question,
                    matched_features=matched_features,
                    metadata={
                        "intent": example.intent,
                        "question_type": example.question_type,
                        "semantic_views": example.semantic_views,
                    },
                )
            )

        hits.sort(key=lambda item: item.score, reverse=True)
        return RetrievalContext(
            domains=domains,
            semantic_views=semantic_views,
            metrics=semantic_parse.matched_metrics,
            retrieval_terms=retrieval_terms,
            hits=hits[:5],
        )

    def reload(self) -> None:
        self.examples = self._load_examples()

    def validate_example(self, payload: dict | ExampleRecord) -> ExampleRecord:
        if isinstance(payload, ExampleRecord):
            return payload
        return ExampleRecord(**payload)

    def _load_examples(self) -> list[ExampleRecord]:
        with self.examples_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return [self.validate_example(item) for item in payload]

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

    def _unique(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
