from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulo.pediatria.detector_mvp import PediatricsDetectorMvpRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Executa o detector pediatrico v4 em video, somente shadow."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--model",
        default="src/models/pediatria_child_detector_v4.pt",
    )
    parser.add_argument("--output-video", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    report = PediatricsDetectorMvpRunner(args.model).run(
        args.source,
        output_video=args.output_video,
        report_path=args.report,
        confidence=args.conf,
        max_frames=args.max_frames,
        device=args.device,
    )
    summary = {
        key: report[key]
        for key in (
            "frames_processed",
            "average_latency_ms",
            "role_counts",
            "stable_state_counts",
            "publishes_alerts",
        )
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
