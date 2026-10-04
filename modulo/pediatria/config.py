from __future__ import annotations

from dataclasses import dataclass, field


def _weights() -> dict[str, float]:
    return {
        "visual_child_prob": 1.0,
        "perspective_height": 1.0,
        "head_shoulder": 1.0,
        "head_bbox": 0.8,
        "leg_body": 0.8,
        "torso_body": 0.6,
    }


def _sigmoids() -> dict[str, dict[str, float]]:
    return {
        "head_shoulder": {"center": 0.56, "k": 12.0, "direction": 1.0},
        "head_bbox": {"center": 0.34, "k": 15.0, "direction": 1.0},
        "leg_body": {"center": 0.46, "k": 14.0, "direction": -1.0},
        "torso_body": {"center": 0.32, "k": 14.0, "direction": 1.0},
        "perspective_height": {"center": 0.78, "k": 8.0, "direction": -1.0},
    }


def _visual_family_weights() -> dict[str, float]:
    return {
        "visual_appearance": 0.60,
        "head_proportion": 0.25,
        "body_proportion": 0.25,
        "scene_scale": 0.15,
    }


@dataclass(frozen=True)
class ChildScoreConfig:
    conf_det: float = 0.40
    iou: float = 0.45
    tracker: str = "bytetrack.yaml"
    kp_conf_min: float = 0.50
    weights: dict[str, float] = field(default_factory=_weights)
    sigmoids: dict[str, dict[str, float]] = field(default_factory=_sigmoids)
    min_valid_families: int = 2
    perspective_bands: int = 6
    band_min_samples: int = 12
    band_min_distinct_tracks: int = 1
    buffer_frames: int = 30
    hysteresis_frames: int = 15
    child_threshold: float = 0.65
    adult_threshold: float = 0.30
    diagnostic_child_candidate_threshold: float = 0.50
    diagnostic_min_track_frames: int = 30
    diagnostic_partial_child_min_frames: int = 15
    diagnostic_partial_child_min_ratio: float = 0.10
    enable_visual_classifier: bool = False
    visual_classifier_model_path: str | None = None
    visual_classifier_imgsz: int = 224
    visual_classifier_min_crop_size: int = 64
    visual_classifier_device: str = "cuda:0"
    visual_classifier_half: bool = True
    visual_classifier_min_quality: float = 0.45
    visual_family_weights: dict[str, float] = field(default_factory=_visual_family_weights)

    def snapshot(self) -> dict[str, object]:
        return {
            "weights": self.weights,
            "sigmoids": self.sigmoids,
            "child_threshold": self.child_threshold,
            "adult_threshold": self.adult_threshold,
            "min_valid_families": self.min_valid_families,
            "diagnostic_child_candidate_threshold": (
                self.diagnostic_child_candidate_threshold
            ),
            "enable_visual_classifier": self.enable_visual_classifier,
            "visual_classifier_model_path": self.visual_classifier_model_path,
            "visual_classifier_imgsz": self.visual_classifier_imgsz,
            "visual_classifier_min_crop_size": self.visual_classifier_min_crop_size,
            "visual_classifier_device": self.visual_classifier_device,
            "visual_classifier_half": self.visual_classifier_half,
            "visual_classifier_min_quality": self.visual_classifier_min_quality,
            "visual_family_weights": self.visual_family_weights,
        }


@dataclass(frozen=True)
class CompanionshipTiming:
    """Tempos do analisador de companhia em SEGUNDOS, independentes da taxa de analise.

    Os defaults de `CompanionshipConfig` estao em frames (referencia ~15 fps: 1 s, 1 s, 1 s, 3 s). Com a
    analise a 3-5 fps, frames fixos esticariam o tempo real de 3 a 5 vezes; por isso o runtime ao vivo
    converte estes segundos pela taxa de analise efetiva (`CompanionshipConfig.from_seconds`).
    """

    relationship_confirmation_seconds: float = 1.0
    state_persistence_seconds: float = 1.0
    adult_occlusion_grace_seconds: float = 1.0
    unbound_child_alone_grace_seconds: float = 3.0


@dataclass(frozen=True)
class CompanionshipConfig:
    """Parametros conservadores do analisador de companhia em shadow."""

    adult_near_distance_ratio: float = 1.75
    adult_separated_distance_ratio: float = 3.25
    relationship_confirmation_frames: int = 15
    min_role_confidence: float = 0.60
    state_persistence_frames: int = 15
    adult_occlusion_grace_frames: int = 15
    unbound_child_alone_grace_frames: int = 45

    @classmethod
    def from_seconds(
        cls,
        analysis_fps: float,
        timing: CompanionshipTiming | None = None,
    ) -> "CompanionshipConfig":
        """Converte tempos em segundos para frames na taxa de analise informada (minimo 1 frame)."""
        t = timing or CompanionshipTiming()
        fps = max(0.1, float(analysis_fps))

        def frames(seconds: float) -> int:
            return max(1, round(seconds * fps))

        return cls(
            relationship_confirmation_frames=frames(t.relationship_confirmation_seconds),
            state_persistence_frames=frames(t.state_persistence_seconds),
            adult_occlusion_grace_frames=frames(t.adult_occlusion_grace_seconds),
            unbound_child_alone_grace_frames=frames(t.unbound_child_alone_grace_seconds),
        )
