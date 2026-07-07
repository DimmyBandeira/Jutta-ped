from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_CLASSES = {"child", "adult", "uncertain"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TrainingSample:
    file: Path
    label: str
    group: str
    metadata: dict[str, Any]


def _repair_mojibake_path_text(value: str) -> str:
    text = str(value)
    try:
        repaired = text.encode("latin1").decode("utf-8")
        if repaired != text and ("Ã" in text or "Â" in text):
            return repaired
    except UnicodeError:
        pass
    return text.replace("WebGuardiÃ£o", "WebGuardião")


def _review_path_candidates(value: Any, review_path: Path) -> list[Path]:
    raw_values = [str(value or "")]
    repaired = _repair_mojibake_path_text(raw_values[0])
    if repaired not in raw_values:
        raw_values.append(repaired)

    candidates: list[Path] = []
    for raw in raw_values:
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(ROOT / path)
            candidates.append(review_path.parent / path)
            candidates.append(path)
    return candidates


def _resolve_review_image(item: dict[str, Any], review_path: Path) -> Path:
    for key in ("file", "review_copy"):
        for candidate in _review_path_candidates(item.get(key), review_path):
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
    raw = item.get("file") or item.get("review_copy") or ""
    return _review_path_candidates(raw, review_path)[0].resolve()


def normalize_training_label(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized == "child_candidate":
        return "child"
    if normalized not in VALID_CLASSES:
        raise ValueError(f"Classe de treino invalida: {value}")
    return normalized


def load_reviewed_samples(path: Path) -> list[TrainingSample]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    samples: list[TrainingSample] = []
    for item in payload.get("labels", []):
        if not item.get("training_eligible", False):
            continue
        file = _resolve_review_image(item, path)
        label = normalize_training_label(item["human_label"])
        group = str(item.get("source_group") or item.get("source") or file.parent.name)
        track_id = item.get("track_id")
        if track_id not in (None, ""):
            group = f"{group}:track_{track_id}"
        samples.append(TrainingSample(file, label, group, dict(item)))
    return samples


def load_class_directory(spec: str) -> list[TrainingSample]:
    if "=" not in spec:
        raise ValueError("--class-dir deve usar classe=caminho.")
    raw_label, raw_path = spec.split("=", 1)
    label = normalize_training_label(raw_label)
    root = Path(raw_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Diretorio de classe nao encontrado: {root}")
    samples: list[TrainingSample] = []
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        relative = file.relative_to(root)
        group = relative.parts[0] if len(relative.parts) > 1 else file.stem
        samples.append(
            TrainingSample(
                file=file,
                label=label,
                group=f"external:{root.name}:{group}",
                metadata={"source_type": "external_class_dir"},
            )
        )
    return samples


def assign_splits(samples: list[TrainingSample]) -> dict[str, list[TrainingSample]]:
    by_group: dict[str, list[TrainingSample]] = defaultdict(list)
    for sample in samples:
        by_group[sample.group].append(sample)
    splits: dict[str, list[TrainingSample]] = {
        "train": [],
        "val": [],
        "test": [],
    }
    for group, group_samples in sorted(by_group.items()):
        bucket = int(hashlib.sha256(group.encode("utf-8")).hexdigest()[:8], 16) % 10
        split = "test" if bucket == 0 else "val" if bucket == 1 else "train"
        splits[split].extend(group_samples)
    return splits


def build_colab_dataset(
    samples: list[TrainingSample],
    output_dir: Path,
) -> dict[str, Any]:
    if not samples:
        raise ValueError("Nenhuma amostra elegivel para empacotar.")
    if output_dir.exists():
        raise FileExistsError(f"Diretorio ja existe: {output_dir}")
    output_dir.mkdir(parents=True)
    splits = assign_splits(samples)
    records: list[dict[str, Any]] = []
    for split, split_samples in splits.items():
        for index, sample in enumerate(split_samples, start=1):
            destination_dir = output_dir / split / sample.label
            destination_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(str(sample.file).encode("utf-8")).hexdigest()[:10]
            destination = destination_dir / f"{digest}_{sample.file.name}"
            shutil.copy2(sample.file, destination)
            records.append(
                {
                    "source_file": str(sample.file),
                    "packaged_file": str(destination.relative_to(output_dir)),
                    "label": sample.label,
                    "split": split,
                    "group": sample.group,
                    "metadata": sample.metadata,
                }
            )
    counts = {
        split: dict(Counter(item.label for item in split_samples))
        for split, split_samples in splits.items()
    }
    training_ready = all(
        all(counts[split].get(label, 0) > 0 for label in ("child", "adult"))
        for split in ("train", "val", "test")
    )
    manifest = {
        "mode": "classification_crops_with_audit_metadata",
        "classes": sorted(VALID_CLASSES),
        "split_strategy": "deterministic_by_source_group",
        "training_ready": training_ready,
        "counts": counts,
        "samples": records,
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest

