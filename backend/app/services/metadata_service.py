from __future__ import annotations

import threading

from backend.app.models.admin import (
    ExampleCollectionResponse,
    ExampleMutationResponse,
    MetadataDocument,
    MetadataOverview,
)
from backend.app.models.example_library import ExampleTemplateRecord
from backend.app.repositories.metadata_repository import FileMetadataRepository
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.domain_config_loader import DomainConfigLoader


class MetadataService:
    def __init__(
        self,
        metadata_repository: FileMetadataRepository,
        domain_config_loader: DomainConfigLoader,
        audit_repository,
    ) -> None:
        self.metadata_repository = metadata_repository
        self.domain_config_loader = domain_config_loader
        self.audit_repository = audit_repository
        self._lock = threading.RLock()

    def overview(self) -> MetadataOverview:
        summary = self.domain_config_loader.summary()
        examples = self.metadata_repository.read("examples_template")
        return MetadataOverview(
            semantic_version=summary.get("version"),
            semantic_domains=summary.get("domains", []),
            table_count=len(summary.get("tables", [])),
            example_count=len(examples),
            trace_count=len(self.audit_repository.list_records()),
        )

    def list_documents(self) -> list[str]:
        return self.metadata_repository.list_names()

    def list_examples(self, retrieval_service: RetrievalService) -> ExampleCollectionResponse:
        examples = [retrieval_service.validate_example(item) for item in self.metadata_repository.read("examples_template")]
        return ExampleCollectionResponse(examples=examples, count=len(examples))

    def create_example(
        self,
        payload: dict | ExampleTemplateRecord,
        retrieval_service: RetrievalService,
    ) -> ExampleMutationResponse:
        with self._lock:
            template = retrieval_service.dump_example_template(payload)
            example = retrieval_service.validate_example(payload)
            examples = [retrieval_service.validate_example(item) for item in self.metadata_repository.read("examples_template")]
            if any(item.id == example.id for item in examples):
                raise ValueError(f"example id already exists: {example.id}")
            templates = self.metadata_repository.read("examples_template")
            templates.append(template)
            self.metadata_repository.write(
                "examples_template",
                templates,
            )
        retrieval_service.reload()
        return ExampleMutationResponse(
            created=True,
            example=example,
            template=ExampleTemplateRecord(**template),
            count=len(templates),
        )

    def materialize_example(
        self,
        example: dict | ExampleTemplateRecord,
        retrieval_service: RetrievalService,
    ) -> ExampleMutationResponse:
        return self.create_example(example, retrieval_service=retrieval_service)

    def bulk_upsert_examples(
        self,
        payloads: list[dict | ExampleTemplateRecord],
        retrieval_service: RetrievalService,
        replace_existing: bool = False,
    ) -> ExampleCollectionResponse:
        with self._lock:
            incoming = [retrieval_service.validate_example(item) for item in payloads]
            incoming_templates = [retrieval_service.dump_example_template(item) for item in payloads]
            incoming_ids = [item.id for item in incoming]
            if len(set(incoming_ids)) != len(incoming_ids):
                raise ValueError("example ids must be unique within the bulk payload")

            if replace_existing:
                merged = incoming
                merged_templates = dict(zip(incoming_ids, incoming_templates, strict=True))
            else:
                existing_templates = self.metadata_repository.read("examples_template")
                existing_records = [retrieval_service.validate_example(item) for item in existing_templates]
                merged_templates = {
                    item.id: template
                    for item, template in zip(existing_records, existing_templates, strict=True)
                }
                for item, template in zip(incoming, incoming_templates, strict=True):
                    merged_templates[item.id] = template
                merged = [retrieval_service.validate_example(item) for item in merged_templates.values()]

            merged.sort(key=lambda item: item.id)
            sorted_templates = [merged_templates[item.id] for item in merged]
            self.metadata_repository.write(
                "examples_template",
                sorted_templates,
            )
        retrieval_service.reload()
        return ExampleCollectionResponse(examples=merged, count=len(merged))

    def update_example(
        self,
        example_id: str,
        payload: dict | ExampleTemplateRecord,
        retrieval_service: RetrievalService,
    ) -> ExampleMutationResponse:
        with self._lock:
            example = retrieval_service.validate_example(payload)
            templates = self.metadata_repository.read("examples_template")
            examples = [retrieval_service.validate_example(item) for item in templates]
            replacement = None
            updated = False
            for index, item in enumerate(examples):
                if item.id != example_id:
                    continue
                replacement = retrieval_service.dump_example_template(payload)
                replacement["id"] = example_id
                templates[index] = replacement
                updated = True
                break
            if not updated:
                raise KeyError(example_id)
            self.metadata_repository.write(
                "examples_template",
                templates,
            )
        retrieval_service.reload()
        updated_example = retrieval_service.validate_example(replacement)
        return ExampleMutationResponse(
            updated=True,
            example=updated_example,
            template=ExampleTemplateRecord(**replacement),
            count=len(templates),
        )

    def get_document(self, name: str) -> MetadataDocument:
        path = self.metadata_repository._resolve(name)
        content = self.metadata_repository.read(name)
        return MetadataDocument(name=name, path=str(path), content=content)

    def update_document(self, name: str, content) -> MetadataDocument:
        with self._lock:
            path = self.metadata_repository.write(name, content)
        return MetadataDocument(name=name, path=str(path), content=content)
