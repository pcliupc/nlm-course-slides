#!/usr/bin/env python3
"""
Monitor created slide artifacts, rename them, and optionally download them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import (
    download_slide_deck,
    ensure_authenticated,
    find_section,
    load_course_manifest,
    read_json,
    rename_artifact,
    sanitize_filename,
    utc_timestamp,
    wait_for_artifact,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Course manifest (.json/.yaml/.md)")
    parser.add_argument("--create-report", required=True, help="Stage-D create-report.json")
    parser.add_argument("--retry-failed-from-report", help="Retry only unfinished or failed items from a previous finalize report")
    parser.add_argument("--section-id", action="append", dest="section_ids", help="Only finalize the specified section ID")
    parser.add_argument("--notebook-id", help="Existing notebook ID or alias")
    parser.add_argument("--output-dir", help="Course output directory")
    parser.add_argument("--report-path", help="Optional finalize report output path")
    parser.add_argument("--profile", help="NotebookLM profile")
    parser.add_argument("--poll-interval", type=float, default=60.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--download", action="store_true", help="Download completed slide decks to local PPTX files")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_retry_filter(path: str | None) -> set[str]:
    if not path:
        return set()
    payload = read_json(Path(path).resolve())
    selected = set()
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        section_id = item.get("section_id")
        if not isinstance(section_id, str):
            continue
        if item.get("artifact_status") in {"running", "pending", "queued"} or item.get("error"):
            selected.add(section_id)
    return selected


def main() -> int:
    args = build_parser().parse_args()
    manifest = load_course_manifest(args.manifest)
    output_dir = Path(args.output_dir or sanitize_filename(manifest.course_title)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    create_report = read_json(Path(args.create_report).resolve())
    notebook_id = args.notebook_id or str(create_report.get("notebook_id") or "").strip()
    if not notebook_id:
        raise SystemExit("Notebook ID is required via --notebook-id or create-report.json")

    requested_ids = set(args.section_ids or [])
    requested_ids.update(_load_retry_filter(args.retry_failed_from_report))
    ensure_authenticated(profile=args.profile, dry_run=args.dry_run)

    results = []
    failures = 0
    for item in create_report.get("results", []):
        if not isinstance(item, dict):
            continue
        section_id = item.get("section_id")
        if not isinstance(section_id, str):
            continue
        if requested_ids and section_id not in requested_ids:
            continue

        section = find_section(manifest, section_id)
        artifact_id = item.get("artifact_id")
        if item.get("status") != "create_requested" or not isinstance(artifact_id, str) or not artifact_id.strip():
            results.append(
                {
                    "section_id": section.id,
                    "artifact_id": artifact_id if isinstance(artifact_id, str) else None,
                    "artifact_status": "skipped",
                    "renamed": False,
                    "downloaded": False,
                    "output_path": None,
                    "error": item.get("error"),
                }
            )
            continue

        output_path = output_dir / section.output_name
        renamed = False
        downloaded = False
        artifact_status = "running"
        error = None
        try:
            artifact = wait_for_artifact(
                notebook_id,
                artifact_id,
                profile=args.profile,
                dry_run=args.dry_run,
                timeout_seconds=args.timeout_seconds,
                poll_interval=args.poll_interval,
            )
            artifact_status = str(artifact.get("status") or "completed").lower()
            rename_artifact(artifact_id, output_path.stem, profile=args.profile, dry_run=args.dry_run)
            renamed = True
            if args.download:
                download_slide_deck(notebook_id, artifact_id, output_path, dry_run=args.dry_run)
                downloaded = True
                artifact_status = "downloaded_local"
            else:
                artifact_status = "completed"
        except TimeoutError:
            artifact_status = "running"
        except Exception as exc:
            failures += 1
            error = str(exc)
            if renamed and not downloaded:
                artifact_status = "failed_download"
            elif "download" in str(exc).lower():
                artifact_status = "failed_download"
            else:
                artifact_status = "failed"

        results.append(
            {
                "section_id": section.id,
                "artifact_id": artifact_id,
                "artifact_status": artifact_status,
                "renamed": renamed,
                "downloaded": downloaded,
                "output_path": str(output_path) if downloaded else None,
                "error": error,
            }
        )

    payload = {
        "manifest": manifest.source_path,
        "course_title": manifest.course_title,
        "output_dir": str(output_dir),
        "notebook_id": notebook_id,
        "generated_at": utc_timestamp(),
        "poll_interval": args.poll_interval,
        "timeout_seconds": args.timeout_seconds,
        "download": args.download,
        "failures": failures,
        "results": results,
    }
    report_path = Path(args.report_path).resolve() if args.report_path else output_dir / "finalize-report.json"
    write_json(report_path, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
