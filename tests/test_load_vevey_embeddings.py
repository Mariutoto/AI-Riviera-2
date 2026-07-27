import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "audit-vevey" / "load_embeddings_to_aiven.py"
SPEC = importlib.util.spec_from_file_location("load_vevey_embeddings", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class VeveyEmbeddingLoaderTests(unittest.TestCase):
    def test_rejects_non_vevey_ids(self):
        inputs = [{"chunk_id": "chunk-1", "document_id": "other_doc"}]
        vectors = [{"chunk_id": "chunk-1", "dimension": 1024}]

        with self.assertRaisesRegex(ValueError, "non-Vevey"):
            MODULE.validate_inputs(inputs, vectors)

    def test_requires_matching_1024_dimension_vectors(self):
        inputs = [{"chunk_id": "chunk-1", "document_id": "vevey_doc"}]

        with self.assertRaisesRegex(ValueError, "dimension"):
            MODULE.validate_inputs(
                inputs,
                [{"chunk_id": "chunk-1", "dimension": 1536}],
            )

    def test_preserves_homogeneous_metadata_and_additional_fields(self):
        row = {
            "document_id": "vevey_doc",
            "embedding_recipe": "political_object",
            "source_metadata_file": "metadata.json",
        }
        record = {
            "document_metadata": {
                "document_id": "vevey_doc",
                "commune": "Vevey",
                "category": "interpellation",
            },
            "interpellation_metadata": {"authors": []},
            "relationships": {"responses": []},
            "processing": {"status": "validated"},
        }

        metadata = MODULE.document_metadata(row, record)

        self.assertEqual(metadata["commune"], "Vevey")
        self.assertEqual(metadata["embedding_recipe"], "political_object")
        self.assertEqual(
            set(metadata["additional_metadata"]),
            {"interpellation_metadata", "relationships"},
        )


if __name__ == "__main__":
    unittest.main()
