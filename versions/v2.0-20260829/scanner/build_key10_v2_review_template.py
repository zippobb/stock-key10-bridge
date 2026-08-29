#!/usr/bin/env python3
"""Create the exact 22:00 human/AI review input skeleton."""

from __future__ import annotations

import argparse
from pathlib import Path

from stock_key10_v2_common import atomic_write, json_bytes, load_json


def review_item(candidate: dict[str, object]) -> dict[str, object]:
    return {
        "ts_code": candidate["ts_code"],
        "origin": candidate["origin"],
        "logic_purity": {"score": None, "reason": ""},
        "elasticity": {
            "score": None,
            "factors": {
                "rebound_efficiency": None,
                "thematic_elasticity": None,
                "capital_support": None,
                "expectation_gap": None,
                "chip_pushing_efficiency": None,
            },
            "reason": "",
        },
        "fundamentals": {"score": None, "reason": ""},
        "major_positive": {"score": 0, "reason": "未发现可确认的重大利好时保持0分"},
        "risk_annotations": [],
        "selection_reason": "",
        "main_risk": "",
        "evidence_as_of": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("quant")
    parser.add_argument("output")
    args = parser.parse_args()
    quant = load_json(Path(args.quant))
    candidates = list(quant.get("official_review_candidates", []))+list(quant.get("external_review_candidates", []))
    value = {
        "schema_version": "2.0.0",
        "trade_date": quant["trade_date"],
        "review_as_of": "",
        "future_information_used": False,
        "candidates": [review_item(item) for item in candidates],
    }
    output = Path(args.output)
    atomic_write(output, json_bytes(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
