#!/usr/bin/env python3
"""Run V2.0 regression tests without third-party test runners."""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("test_stock_key10_v2", HERE/"test_stock_key10_v2.py")
if spec is None or spec.loader is None:
    raise SystemExit("无法加载V2.0测试")
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)

tests.test_available_weight_rescale_and_70_percent_floor()
tests.test_sector_has_no_minimum_sample_gate_and_stock_score_bounds()
tests.test_risk_only_marks_and_external_activation()


class Environment:
    @staticmethod
    def setenv(key: str, value: str) -> None:
        os.environ[key] = value


with tempfile.TemporaryDirectory() as directory:
    tests.test_single_png_contains_both_sections(Path(directory), Environment())

print("V2.0 regression tests passed: 4")
