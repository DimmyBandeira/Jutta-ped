import csv
import json
from pathlib import Path

from tools.review_pediatria_dataset import (
    load_review_items,
    write_review_outputs,
)


def test_loads_review_priority_csv_and_exports_colab_json(tmp_path: Path) -> None:
    image = tmp_path / "crop.jpg"
    image.write_bytes(b"jpg")
    csv_path = tmp_path / "review_priority.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "index",
                "predicted_role",
                "confidence",
                "source",
                "frame",
                "track",
                "crop",
                "review_copy",
                "human_label",
                "training_eligible",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "index": "1",
                "predicted_role": "child",
                "confidence": "0.88",
                "source": "video_a",
                "frame": "10",
                "track": "7",
                "crop": str(image),
                "review_copy": str(image),
                "human_label": "",
                "training_eligible": "",
                "notes": "",
            }
        )

    items = load_review_items(csv_path)
    items[0].human_label = "adult"
    items[0].training_eligible = True
    output_json = tmp_path / "human_review_labels.json"
    output_csv = tmp_path / "human_review_labels.csv"

    write_review_outputs(items, output_json, output_csv)

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["labels"][0]["human_label"] == "adult"
    assert payload["labels"][0]["training_eligible"] is True
    assert payload["labels"][0]["source_group"] == "video_a"
    assert output_csv.is_file()
