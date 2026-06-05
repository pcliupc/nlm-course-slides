#!/usr/bin/env python3
"""
Verify that expected slide decks exist for every section in a course manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import build_focus_prompt, load_course_manifest, sanitize_filename, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Course manifest (.json/.yaml/.md)")
    parser.add_argument("--output-dir", help="Directory containing PPTX files")
    parser.add_argument("--report-path", help="Optional JSON report path")
    parser.add_argument("--require-sidecar", action="store_true")
    parser.add_argument("--require-focus", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = load_course_manifest(args.manifest)
    output_dir = Path(args.output_dir or sanitize_filename(manifest.course_title)).resolve()

    expected = {}
    duplicate_names = []
    for section in manifest.sections:
        if section.output_name in expected:
            duplicate_names.append(section.output_name)
        expected[section.output_name] = section

    missing = []
    empty = []
    sidecar_missing = []
    metadata_mismatch = []
    missing_focus_metadata = []
    focus_mismatch = []

    for output_name, section in expected.items():
        pptx_path = output_dir / output_name
        if not pptx_path.exists():
            missing.append({"section_id": section.id, "expected_path": str(pptx_path)})
            continue
        if pptx_path.stat().st_size == 0:
            empty.append({"section_id": section.id, "path": str(pptx_path)})

        sidecar_path = pptx_path.with_suffix(".slide.json")
        if not sidecar_path.exists():
            if args.require_sidecar:
                sidecar_missing.append(str(sidecar_path))
            if args.require_focus:
                missing_focus_metadata.append(
                    {
                        "section_id": section.id,
                        "path": str(sidecar_path),
                        "reason": "missing-sidecar",
                    }
                )
            continue

        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if data.get("section_id") != section.id or data.get("output_name") != output_name:
            metadata_mismatch.append(
                {
                    "path": str(sidecar_path),
                    "expected_section_id": section.id,
                    "actual_section_id": data.get("section_id"),
                    "expected_output_name": output_name,
                    "actual_output_name": data.get("output_name"),
                }
            )
        if args.require_focus:
            expected_focus = section.focus or build_focus_prompt(section.title)
            actual_focus = data.get("focus")
            if not isinstance(actual_focus, str) or not actual_focus.strip():
                missing_focus_metadata.append(
                    {
                        "section_id": section.id,
                        "path": str(sidecar_path),
                        "reason": "missing-focus",
                    }
                )
            elif actual_focus.strip() != expected_focus:
                focus_mismatch.append(
                    {
                        "section_id": section.id,
                        "path": str(sidecar_path),
                        "expected_focus": expected_focus,
                        "actual_focus": actual_focus.strip(),
                    }
                )

    actual_pptx = {path.name for path in output_dir.glob("*.pptx")}
    extra_files = sorted(actual_pptx - set(expected))

    report = {
        "manifest": manifest.source_path,
        "course_title": manifest.course_title,
        "output_dir": str(output_dir),
        "expected_count": len(expected),
        "present_count": len(actual_pptx & set(expected)),
        "missing": missing,
        "empty_files": empty,
        "duplicate_expected_names": sorted(set(duplicate_names)),
        "missing_sidecars": sidecar_missing,
        "metadata_mismatch": metadata_mismatch,
        "missing_focus_metadata": missing_focus_metadata,
        "focus_mismatch": focus_mismatch,
        "extra_files": extra_files,
    }

    report_path = Path(args.report_path).resolve() if args.report_path else output_dir / "slide-verification-report.json"
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False))

    has_errors = any(
        [
            missing,
            empty,
            duplicate_names,
            sidecar_missing,
            metadata_mismatch,
            missing_focus_metadata,
            focus_mismatch,
        ]
    )
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
