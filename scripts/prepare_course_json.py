#!/usr/bin/env python3
"""
Stage a course JSON file into a local course directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
from pathlib import Path

from common import log_message, sanitize_filename, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", help="Existing local course JSON file")
    parser.add_argument("--signed-url", help="Signed download URL returned by get_course_content_json")
    parser.add_argument("--course-name", required=True, help="Course name used to create the local course directory")
    parser.add_argument("--output-root", default=".", help="Root directory where the course directory should be created")
    parser.add_argument("--filename", help="Target JSON filename inside the course directory")
    parser.add_argument("--report-path", help="Optional course JSON report output path")
    parser.add_argument("--force", action="store_true", help="Overwrite the target JSON file if it already exists")
    return parser


def copy_local_file(source_file: Path, target_file: Path) -> None:
    if source_file.resolve() == target_file.resolve():
        return
    shutil.copy2(source_file, target_file)


def download_file(signed_url: str, target_file: Path) -> None:
    with urllib.request.urlopen(signed_url) as response:
        target_file.write_bytes(response.read())


def main() -> int:
    args = build_parser().parse_args()
    if bool(args.source_file) == bool(args.signed_url):
        raise SystemExit("Provide exactly one of --source-file or --signed-url")

    course_dir = Path(args.output_root).resolve() / sanitize_filename(args.course_name, fallback="course")
    course_dir.mkdir(parents=True, exist_ok=True)

    default_name = f"{sanitize_filename(args.course_name, fallback='course')}.json"
    target_file = course_dir / (args.filename or default_name)

    if target_file.exists() and not args.force:
        raise SystemExit(f"Target file already exists: {target_file}. Use --force to overwrite.")

    if args.source_file:
        source_file = Path(args.source_file).resolve()
        if not source_file.exists():
            raise SystemExit(f"Source file does not exist: {source_file}")
        log_message(f"[course-json] copy local file to {target_file}")
        copy_local_file(source_file, target_file)
    else:
        log_message(f"[course-json] download signed URL to {target_file}")
        download_file(args.signed_url, target_file)

    report_path = Path(args.report_path).resolve() if args.report_path else course_dir / "course-json-report.json"
    payload = {
        "status": "ready",
        "source_kind": "local_file" if args.source_file else "signed_url",
        "course_name": args.course_name,
        "course_dir": str(course_dir),
        "json_path": str(target_file),
        "report_path": str(report_path),
    }
    write_json(report_path, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
