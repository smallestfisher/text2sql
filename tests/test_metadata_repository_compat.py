from __future__ import annotations

import unittest

from backend.app.repositories.metadata_repository import FileMetadataRepository


class MetadataRepositoryCompatTests(unittest.TestCase):
    def test_documents_alias_matches_paths(self) -> None:
        repository = FileMetadataRepository()

        self.assertIs(repository.documents, repository.paths)
        self.assertIn("examples_template", repository.documents)
        self.assertIn("tables_metadata", repository.documents)
        self.assertIn("join_patterns", repository.documents)


if __name__ == "__main__":
    unittest.main()
