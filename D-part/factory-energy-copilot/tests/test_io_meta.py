"""Tests for bounded source metadata and immutability comparison."""

import hashlib
import json

from src.common.io_meta import compare_file_metadata, get_file_metadata


def test_metadata_and_streaming_hash_are_json_serializable(tmp_path):
    path = tmp_path / "source.csv"
    content = "header\nvalue\n".encode("utf-8")
    path.write_bytes(content)

    metadata = get_file_metadata(path, compute_hash=True, hash_chunk_size=3)
    assert metadata.length == len(content)
    assert metadata.sha256 == hashlib.sha256(content).hexdigest()
    assert json.loads(json.dumps(metadata.to_dict()))["length"] == len(content)


def test_immutability_comparison_detects_change(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("a\n", encoding="utf-8")
    before = get_file_metadata(path, compute_hash=True)
    path.write_text("a\nb\n", encoding="utf-8")
    after = get_file_metadata(path, compute_hash=True)

    comparison = compare_file_metadata(before, after)
    assert comparison["length_equal"] is False
    assert comparison["hash_equal"] is False
    assert comparison["unchanged"] is False
