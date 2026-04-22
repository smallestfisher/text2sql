from __future__ import annotations

from backend.app.models.admin import (
    ExampleCollectionResponse,
    ExampleMutationResponse,
    MetadataDocument,
    MetadataOverview,
)
from backend.app.models.example_library import ExampleRecord
from backend.app.repositories.metadata_repository import FileMetadataRepository
from backend.app.services.retrieval_service import RetrievalService
from backend.app.services.semantic_loader import SemanticLayerLoader


class MetadataService:
    def __init__(
        self,
        metadata_repository: FileMetadataRepository,
        semantic_loader: SemanticLayerLoader,
        audit_repository,
    ) -> None:
        self.metadata_repository = metadata_repository
        self.semantic_loader = semantic_loader
        self.audit_repository = audit_repository

    def overview(self) -> MetadataOverview:
        summary = self.semantic_loader.summary()
        examples = self.metadata_repository.read("examples_template")
        return MetadataOverview(
            semantic_version=summary.get("version"),
            semantic_domains=summary.get("domains", []),
            semantic_views=summary.get("semantic_views", []),
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
        payload: dict | ExampleRecord,
        retrieval_service: RetrievalService,
    ) -> ExampleMutationResponse:
        example = retrieval_service.validate_example(payload)
        examples = [retrieval_service.validate_example(item) for item in self.metadata_repository.read("examples_template")]
        if any(item.id == example.id for item in examples):
            raise ValueError(f"example id already exists: {example.id}")
        examples.append(example)
        self.metadata_repository.write(
            "examples_template",
            [item.model_dump() for item in examples],
        )
        retrieval_service.reload()
        return ExampleMutationResponse(
            created=True,
            example=example,
            count=len(examples),
        )

    def update_example(
        self,
        example_id: str,
        payload: dict | ExampleRecord,
        retrieval_service: RetrievalService,
    ) -> ExampleMutationResponse:
        example = retrieval_service.validate_example(payload)
        examples = [retrieval_service.validate_example(item) for item in self.metadata_repository.read("examples_template")]
        updated = False
        for index, item in enumerate(examples):
            if item.id != example_id:
                continue
            examples[index] = example.model_copy(update={"id": example_id})
            updated = True
            break
        if not updated:
            raise KeyError(example_id)
        self.metadata_repository.write(
            "examples_template",
            [item.model_dump() for item in examples],
        )
        retrieval_service.reload()
        return ExampleMutationResponse(
            updated=True,
            example=example.model_copy(update={"id": example_id}),
            count=len(examples),
        )

    def get_document(self, name: str) -> MetadataDocument:
        path = self.metadata_repository._resolve(name)
        content = self.metadata_repository.read(name)
        return MetadataDocument(name=name, path=str(path), content=content)

    def update_document(self, name: str, content) -> MetadataDocument:
        path = self.metadata_repository.write(name, content)
        return MetadataDocument(name=name, path=str(path), content=content)

    def reload_runtime(self, retrieval_service=None) -> dict:
        self.semantic_loader.load.cache_clear()
        summary = self.semantic_loader.summary()
        if retrieval_service is not None:
            retrieval_service.reload()
        return {
            "semantic_version": summary.get("version"),
            "reloaded": True,
        }
