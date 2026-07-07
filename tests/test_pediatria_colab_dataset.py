import json
from pathlib import Path

import pytest

from modulo.pediatria.colab_dataset import (
    TrainingSample,
    assign_splits,
    build_colab_dataset,
    load_class_directory,
    load_reviewed_samples,
    normalize_training_label,
)


def test_normalizes_child_candidate_for_training() -> None:
    assert normalize_training_label("child_candidate") == "child"
    with pytest.raises(ValueError):
        normalize_training_label("visitor")


def test_loads_only_human_reviewed_eligible_samples(tmp_path) -> None:
    image = tmp_path / "adult.jpg"
    image.write_bytes(b"x")
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "labels": [
                    {
                        "file": str(image),
                        "human_label": "adult",
                        "training_eligible": True,
                        "source_group": "video_a",
                        "track_id": 7,
                    },
                    {
                        "file": str(image),
                        "human_label": "adult",
                        "training_eligible": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    samples = load_reviewed_samples(review)

    assert len(samples) == 1
    assert samples[0].group == "video_a:track_7"


def test_external_class_directory_preserves_top_level_group(tmp_path) -> None:
    root = tmp_path / "children"
    (root / "source_a").mkdir(parents=True)
    (root / "source_a" / "child.jpg").write_bytes(b"x")

    samples = load_class_directory(f"child={root}")

    assert samples[0].label == "child"
    assert samples[0].group.endswith(":source_a")


def test_same_group_never_crosses_splits(tmp_path) -> None:
    samples = [
        TrainingSample(tmp_path / f"{index}.jpg", "adult", "same_video", {})
        for index in range(3)
    ]

    splits = assign_splits(samples)

    populated = [name for name, values in splits.items() if values]
    assert len(populated) == 1


def test_builds_manifest_and_marks_incomplete_smoke_not_ready(tmp_path) -> None:
    image = tmp_path / "adult.jpg"
    image.write_bytes(b"x")
    output = tmp_path / "package"

    manifest = build_colab_dataset(
        [TrainingSample(image, "adult", "video_a", {"bbox_xyxy": [1, 2, 3, 4]})],
        output,
    )

    assert manifest["training_ready"] is False
    assert (output / "dataset_manifest.json").is_file()
    assert manifest["samples"][0]["metadata"]["bbox_xyxy"] == [1, 2, 3, 4]
