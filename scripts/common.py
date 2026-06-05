#!/usr/bin/env python3
"""
Shared helpers for the nlm-course-slides skill.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fcntl


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
SECTION_ID_PREFIX_RE = re.compile(r"^\s*([一二三四五六七八九十百千万零两\d]+(?:[.\-、]\d+)*)\s+")

SECTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百千万零两\d]+\s*[章节讲部分单元课]|"
    r"[一二三四五六七八九十百千万零两\d]+(?:[.\-、]\d+)*[.\-、)]?|"
    r"\(\d+(?:\.\d+)*\)|"
    r"（\d+(?:\.\d+)*）"
    r")\s*"
)

FOCUS_PROMPT_TEMPLATE = """文稿题目：{section_title}
目标受众：中国出海企业员工。 用他们能够听懂的语言，用生动又不失专业性的表达方式来深入讲解。
来源文档是给中国出海企业员工的课程内容。请根据来源文档生成演示文稿，演示文稿要和来源文档的逻辑与核心内容严格对应，目的是让学员边看演示文稿，边听来源文档的讲解，支持他们更提纲挈领的领会来源文档的要点，从而根据自身实际情况，采取相应的行动。
开篇尽量简洁，直入主题。
必须遵循来源文档中的专业用语和定义。
页数要求：10-16页，用适当的文字讲解主要内容。
不要浪费页面讲口号，要讲具体的要点和方法。
演示文稿只使用中文，严禁出现除了中文之外的任何其他语言、字母。特殊情况：若出现电话号码，使用阿拉伯数字。
取消结尾页。 
视觉效果要求: 遵循极简的商务视觉原则，以现代企业扁平线性矢量插图为主，构图上合理留白，营造舒适的视觉呼吸感。全文稿任何地方都严禁放任何徽标（Logo）。
底版强制要求：全部演示文稿的底版必须统一为纯白色（#FFFFFF），底版区域禁止出现任何底纹、网格线、辅助线、水印、杂色及各类装饰性背景元素，保证底版干净无杂质。
配图、图标等须使用品牌色，即绿色（RGB 0-176-80），橙色（RGB 255-153-0），蓝色（RGB 51-153-255），可适当加入浅绿色（RGB 97-209-116）、浅橙色（RGB 255-194-102）、浅蓝色（RGB 153-204-255），严禁使用粉色系颜色。"""

FIRA_SKIP_CHAPTER_NAMES = {"训战启程", "训战总结、训战输出", "满意度调查"}
PROMPT_TITLE_RE = re.compile(r"文稿题目：([^\n\r]+)")
NLM_API_DELAY_SECONDS = 15.0
NLM_STATUS_API_DELAY_SECONDS = 60.0
NLM_RATE_LIMIT_MAX_DELAY_SECONDS = 240.0
NLM_RATE_LIMIT_MAX_RETRIES = 4
NLM_RATE_LIMIT_LOCK_PATH = Path(tempfile.gettempdir()) / "nlm-course-slides.rate-limit.lock"
NLM_STATUS_RATE_LIMIT_LOCK_PATH = Path(tempfile.gettempdir()) / "nlm-course-slides.status-rate-limit.lock"


@dataclass
class Section:
    id: str
    order: int
    title: str
    content: str
    slug: str
    output_name: str
    focus: str | None = None
    resource_title: str | None = None


@dataclass
class CourseManifest:
    course_title: str
    sections: list[Section]
    source_path: str


@dataclass
class RecoverySectionState:
    section_id: str
    section_title: str
    resource_title: str
    output_name: str
    status: str
    source_id: str | None
    artifact_id: str | None
    artifact_status: str | None
    local_output_exists: bool
    local_sidecar_exists: bool
    resume_action: str
    notes: list[str]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, *, fallback: str = "section") -> str:
    text = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE).strip("-")
    return text or fallback


def sanitize_filename(value: str, *, fallback: str = "section") -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "-", value, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text, flags=re.UNICODE).strip(" .")
    return text or fallback


def default_output_name(section_id: str, title: str) -> str:
    clean_title = sanitize_filename(title)
    if title_starts_with_section_id(title, section_id):
        return f"{clean_title}.pptx"
    return f"{section_id} {clean_title}.pptx"


def default_resource_title(section_id: str, title: str) -> str:
    if title_starts_with_section_id(title, section_id):
        return title.strip()
    return f"{section_id} {title.strip()}"


def extract_section_id(title: str) -> str | None:
    match = SECTION_ID_PREFIX_RE.match(title or "")
    if not match:
        return None
    return match.group(1).rstrip(".-、)")


def title_starts_with_section_id(title: str, section_id: str) -> bool:
    extracted = extract_section_id(title)
    return bool(extracted and extracted == section_id)


def strip_section_prefix(title: str) -> str:
    cleaned = title.strip()
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = SECTION_PREFIX_RE.sub("", cleaned).strip()
    return cleaned or title.strip()


def build_focus_prompt(title: str) -> str:
    return FOCUS_PROMPT_TEMPLATE.format(section_title=strip_section_prefix(title))


def extract_focus_title(focus: str | None) -> str | None:
    if not focus:
        return None
    match = PROMPT_TITLE_RE.search(focus)
    if not match:
        return None
    return match.group(1).strip() or None


def focus_preview(focus: str | None, *, limit: int = 120) -> str | None:
    if not focus:
        return None
    normalized = re.sub(r"\s+", " ", focus).strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def parse_first_uuid(text: str) -> str:
    match = UUID_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not find UUID in command output:\n{text}")
    return match.group(0)


def log_message(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def configure_nlm_api_delay(delay_seconds: float) -> None:
    global NLM_API_DELAY_SECONDS
    NLM_API_DELAY_SECONDS = max(0.0, delay_seconds)


def _is_nlm_command(args: list[str]) -> bool:
    return bool(args) and Path(args[0]).name == "nlm"


def _read_rate_limit_state(handle: Any, *, base_delay: float) -> dict[str, float]:
    handle.seek(0)
    raw_value = handle.read().strip()
    if not raw_value:
        return {"last_started_at": 0.0, "current_delay": base_delay}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        try:
            return {
                "last_started_at": float(raw_value),
                "current_delay": base_delay,
            }
        except ValueError:
            return {"last_started_at": 0.0, "current_delay": base_delay}
    if isinstance(payload, (int, float)):
        return {
            "last_started_at": float(payload),
            "current_delay": base_delay,
        }
    if not isinstance(payload, dict):
        return {"last_started_at": 0.0, "current_delay": base_delay}
    return {
        "last_started_at": float(payload.get("last_started_at") or 0.0),
        "current_delay": max(
            base_delay,
            float(payload.get("current_delay") or base_delay),
        ),
    }


def _write_rate_limit_state(handle: Any, state: dict[str, float]) -> None:
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(state, ensure_ascii=False))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


def _rate_limit_delay_markers() -> list[str]:
    return [
        "rate limited",
        "rate limit",
        "api error (code 8)",
        "wait a few minutes before retrying",
        "too many requests",
    ]


def _is_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _rate_limit_delay_markers())


def _is_status_poll_command(args: list[str]) -> bool:
    return args[:4] == ["nlm", "studio", "status", "--json"] or args[:4] == [
        "nlm",
        "status",
        "artifacts",
        "--json",
    ] or args[:4] == ["nlm", "list", "artifacts", "--json"]


def _rate_limit_bucket(args: list[str]) -> tuple[Path, float]:
    if _is_status_poll_command(args):
        return NLM_STATUS_RATE_LIMIT_LOCK_PATH, NLM_STATUS_API_DELAY_SECONDS
    return NLM_RATE_LIMIT_LOCK_PATH, NLM_API_DELAY_SECONDS


def _rate_limit_nlm_command(args: list[str]) -> None:
    if not _is_nlm_command(args) or NLM_API_DELAY_SECONDS <= 0:
        return

    lock_path, base_delay = _rate_limit_bucket(args)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = _read_rate_limit_state(handle, base_delay=base_delay)
        last_started_at = state["last_started_at"]
        current_delay = max(base_delay, state["current_delay"])
        now = time.time()
        delay = max(0.0, last_started_at + current_delay - now)
        if delay > 0:
            log_message(
                f"[rate-limit] wait {delay:.1f}s before NotebookLM API call: {shlex.join(args)}"
            )
            time.sleep(delay)
        state["last_started_at"] = time.time()
        _write_rate_limit_state(handle, state)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _update_rate_limit_delay(
    args: list[str],
    *,
    multiplier: float | None = None,
    reset: bool = False,
) -> float:
    lock_path, base_delay = _rate_limit_bucket(args)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = _read_rate_limit_state(handle, base_delay=base_delay)
        if reset:
            state["current_delay"] = base_delay
        elif multiplier is not None:
            state["current_delay"] = min(
                NLM_RATE_LIMIT_MAX_DELAY_SECONDS,
                max(base_delay, state["current_delay"]) * multiplier,
            )
        _write_rate_limit_state(handle, state)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return state["current_delay"]


def run_cmd(
    args: list[str],
    *,
    dry_run: bool = False,
    cwd: str | None = None,
    capture_output: bool = True,
    check: bool = True,
) -> str:
    command = shlex.join(args)
    if dry_run:
        log_message(f"[dry-run] {command}")
        return ""

    attempts = 0
    while True:
        _rate_limit_nlm_command(args)
        completed = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=capture_output,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode == 0:
            if _is_nlm_command(args):
                _update_rate_limit_delay(args, reset=True)
            return stdout if capture_output else ""

        error_text = (
            f"Command failed with exit code {completed.returncode}: {command}\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
        if _is_nlm_command(args) and _is_rate_limit_error(error_text) and attempts < NLM_RATE_LIMIT_MAX_RETRIES:
            attempts += 1
            new_delay = _update_rate_limit_delay(args, multiplier=2.0)
            log_message(
                f"[rate-limit] NotebookLM rate limit on attempt {attempts} for {command}; "
                f"increase shared delay to {new_delay:.1f}s and retry"
            )
            continue

        if check:
            raise RuntimeError(error_text)
        return stdout if capture_output else ""


def ensure_authenticated(*, profile: str | None = None, dry_run: bool = False) -> None:
    if dry_run:
        args = ["nlm", "login", "--check"]
        if profile:
            args.extend(["--profile", profile])
        run_cmd(args, dry_run=True)
        return

    candidate_commands: list[list[str]] = [
        ["nlm", "login", "--check"],
        ["nlm", "auth", "status"],
    ]
    if profile:
        for command in candidate_commands:
            command.extend(["--profile", profile])

    unsupported_markers = [
        "Try 'nlm --help' for help.",
        "No such command",
        "No such option",
        "Usage: nlm [OPTIONS] COMMAND [ARGS]...",
    ]
    last_error: RuntimeError | None = None

    for args in candidate_commands:
        _rate_limit_nlm_command(args)
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode == 0:
            return

        combined = f"{stdout}\n{stderr}"
        if completed.returncode == 2 and any(marker in combined for marker in unsupported_markers):
            continue

        last_error = RuntimeError(
            f"Authentication check failed with exit code {completed.returncode}: {shlex.join(args)}\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
        break

    if last_error:
        raise last_error

    raise RuntimeError(
        "Could not find a supported NotebookLM authentication check command. "
        "This script supports `nlm login --check` and `nlm auth status`."
    )


def create_notebook(
    title: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> str:
    args = ["nlm", "notebook", "create", title]
    if profile:
        args.extend(["--profile", profile])
    output = run_cmd(args, dry_run=dry_run)
    return "dry-run-notebook-id" if dry_run else parse_first_uuid(output)


def add_text_source(
    notebook_id: str,
    title: str,
    text: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    wait_timeout: float = 600.0,
) -> str:
    args = [
        "nlm",
        "add",
        "text",
        "--title",
        title,
        "--wait",
        "--wait-timeout",
        str(wait_timeout),
    ]
    if profile:
        args.extend(["--profile", profile])
    args.extend([notebook_id, text])
    output = run_cmd(args, dry_run=dry_run)
    return "dry-run-source-id" if dry_run else parse_first_uuid(output)


def create_slide_deck(
    notebook_id: str,
    *,
    source_id: str,
    focus: str | None,
    language: str,
    deck_format: str,
    length: str,
    profile: str | None = None,
    dry_run: bool = False,
) -> str:
    args = [
        "nlm",
        "slides",
        "create",
        "--format",
        deck_format,
        "--length",
        length,
        "--language",
        language,
        "--source-ids",
        source_id,
        "--confirm",
    ]
    if focus:
        args.extend(["--focus", focus])
    if profile:
        args.extend(["--profile", profile])
    args.append(notebook_id)
    output = run_cmd(args, dry_run=dry_run)
    return "dry-run-artifact-id" if dry_run else parse_first_uuid(output)


def wait_for_artifact(
    notebook_id: str,
    artifact_id: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    timeout_seconds: float = 1800.0,
    poll_interval: float = 30.0,
) -> dict[str, Any]:
    if dry_run:
        return {"id": artifact_id, "status": "completed", "type": "slide_deck"}

    deadline = time.time() + timeout_seconds
    status_commands = [
        ["nlm", "studio", "status", "--json"],
        ["nlm", "status", "artifacts", "--json"],
        ["nlm", "list", "artifacts", "--json"],
    ]
    if profile:
        for command in status_commands:
            command.extend(["--profile", profile])
    for command in status_commands:
        command.append(notebook_id)

    transient_markers = [
        "could not retrieve studio status.",
        "timed out",
        "timeout",
        "temporarily unavailable",
    ]
    transient_failures = 0

    while time.time() < deadline:
        saw_transient_error = False

        for args in status_commands:
            _rate_limit_nlm_command(args)
            completed = subprocess.run(
                args,
                text=True,
                capture_output=True,
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            combined = f"{stdout}\n{stderr}".lower()

            if completed.returncode != 0:
                if any(marker in combined for marker in transient_markers):
                    saw_transient_error = True
                    continue
                raise RuntimeError(
                    f"Command failed with exit code {completed.returncode}: {shlex.join(args)}\n"
                    f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                )

            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid JSON from {shlex.join(args)}:\n{stdout}"
                ) from exc

            if isinstance(payload, dict) and payload.get("status") == "error":
                message = str(payload.get("error") or payload)
                if any(marker in message.lower() for marker in transient_markers):
                    saw_transient_error = True
                    continue
                raise RuntimeError(
                    f"Status query returned an error for artifact {artifact_id}: {message}"
                )

            if not isinstance(payload, list):
                raise RuntimeError(
                    f"Unexpected status payload from {shlex.join(args)}: {json.dumps(payload, ensure_ascii=False)}"
                )

            for item in payload:
                if item.get("id") != artifact_id:
                    continue
                status = item.get("status")
                if status == "completed":
                    return item
                if status in {"failed", "cancelled", "canceled"}:
                    raise RuntimeError(
                        f"Artifact {artifact_id} ended with status '{status}': {json.dumps(item, ensure_ascii=False)}"
                    )
                break

            # Successfully queried artifact list. If the target artifact is not
            # completed yet, wait for the next poll instead of trying fallback
            # commands in the same cycle.
            break

        if saw_transient_error:
            transient_failures += 1
            log_message(
                f"[artifact {artifact_id}] status query temporarily unavailable "
                f"(consecutive={transient_failures}), retry in {poll_interval} seconds"
            )
        else:
            transient_failures = 0
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Timed out waiting for artifact {artifact_id} after {timeout_seconds} seconds."
    )


def rename_artifact(
    artifact_id: str,
    new_title: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> None:
    args = ["nlm", "rename", "studio"]
    if profile:
        args.extend(["--profile", profile])
    args.extend([artifact_id, new_title])
    run_cmd(args, dry_run=dry_run)


def download_slide_deck(
    notebook_id: str,
    artifact_id: str,
    output_path: Path,
    *,
    file_format: str = "pptx",
    retry_attempts: int = 3,
    retry_delay_seconds: float = 5.0,
    dry_run: bool = False,
) -> None:
    args = [
        "nlm",
        "download",
        "slide-deck",
        "--id",
        artifact_id,
        "--output",
        str(output_path),
        "--format",
        file_format,
        notebook_id,
    ]
    if dry_run:
        run_cmd(args, dry_run=True)
        return

    attempts = 0
    while True:
        try:
            run_cmd(args, dry_run=False)
            return
        except RuntimeError as exc:
            if attempts >= retry_attempts:
                raise
            attempts += 1
            log_message(
                f"[download {artifact_id}] download failed on attempt {attempts}: {exc}. "
                f"Retry in {retry_delay_seconds:.0f}s"
            )
            time.sleep(retry_delay_seconds)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_json_output(output: str, *, command: str) -> Any:
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {command}:\n{output}") from exc


def _first_non_empty(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _coerce_title(item: dict[str, Any]) -> str:
    value = _first_non_empty(
        item,
        [
            "title",
            "name",
            "display_name",
            "displayName",
            "artifact_title",
            "artifactTitle",
            "source_title",
            "sourceTitle",
        ],
    )
    if value in (None, "", [], {}):
        custom_instructions = str(item.get("custom_instructions") or "").strip()
        match = PROMPT_TITLE_RE.search(custom_instructions)
        if match:
            value = match.group(1).strip()
    return str(value or "").strip()


def _coerce_id(item: dict[str, Any]) -> str | None:
    value = _first_non_empty(item, ["id", "artifact_id", "artifactId", "source_id", "sourceId", "uuid"])
    if value is None:
        return None
    return str(value).strip() or None


def _coerce_status(item: dict[str, Any]) -> str | None:
    value = _first_non_empty(item, ["status", "state", "artifact_status", "artifactStatus"])
    if value is None:
        return None
    return str(value).strip().lower() or None


def _coerce_source_ids(item: dict[str, Any]) -> list[str]:
    raw = _first_non_empty(item, ["source_ids", "sourceIds", "sources"])
    if raw is None:
        return []
    if isinstance(raw, list):
        source_ids: list[str] = []
        for entry in raw:
            if isinstance(entry, str):
                source_ids.append(entry)
                continue
            if isinstance(entry, dict):
                source_id = _coerce_id(entry)
                if source_id:
                    source_ids.append(source_id)
        return source_ids
    if isinstance(raw, str):
        return [raw]
    return []


def list_notebook_sources(
    notebook_id: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    args = ["nlm", "list", "sources", "--json"]
    if profile:
        args.extend(["--profile", profile])
    args.append(notebook_id)
    output = run_cmd(args, dry_run=dry_run)
    if dry_run:
        return []
    payload = parse_json_output(output, command=shlex.join(args))
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected source list payload: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def list_notebook_artifacts(
    notebook_id: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    commands = [
        ["nlm", "list", "artifacts", "--json"],
        ["nlm", "studio", "status", "--json"],
        ["nlm", "status", "artifacts", "--json"],
    ]
    if profile:
        for args in commands:
            args.extend(["--profile", profile])
    transient_markers = [
        "could not retrieve studio status.",
        "timed out",
        "timeout",
        "temporarily unavailable",
    ]
    last_error: RuntimeError | None = None
    for args in commands:
        full_args = [*args, notebook_id]
        command = shlex.join(full_args)
        if dry_run:
            run_cmd(full_args, dry_run=True)
            return []
        try:
            output = run_cmd(full_args, dry_run=False)
        except RuntimeError as exc:
            message = str(exc).lower()
            if any(marker in message for marker in transient_markers):
                last_error = exc
                continue
            raise
        payload = parse_json_output(output, command=command)
        if isinstance(payload, dict) and payload.get("status") == "error":
            message = str(payload.get("error") or payload)
            if any(marker in message.lower() for marker in transient_markers):
                last_error = RuntimeError(message)
                continue
            raise RuntimeError(f"Artifact list query returned an error: {message}")
        if isinstance(payload, list):
            return payload
        raise RuntimeError(f"Unexpected artifact list payload: {json.dumps(payload, ensure_ascii=False)}")
    if last_error:
        raise last_error
    return []


def match_sections_to_notebook_state(
    manifest: CourseManifest,
    *,
    notebook_id: str,
    output_dir: Path,
    profile: str | None = None,
    dry_run: bool = False,
) -> list[RecoverySectionState]:
    sources = list_notebook_sources(notebook_id, profile=profile, dry_run=dry_run)
    artifacts = list_notebook_artifacts(notebook_id, profile=profile, dry_run=dry_run)

    source_matches: dict[str, list[dict[str, Any]]] = {}
    for item in sources:
        source_title = _coerce_title(item)
        if source_title:
            source_matches.setdefault(source_title, []).append(item)

    artifact_matches: dict[str, list[dict[str, Any]]] = {}
    for item in artifacts:
        artifact_title = _coerce_title(item)
        if artifact_title:
            artifact_matches.setdefault(artifact_title, []).append(item)

    states: list[RecoverySectionState] = []
    for section in manifest.sections:
        resource_title = section.resource_title or default_resource_title(section.id, section.title)
        output_name = section.output_name
        output_stem = Path(output_name).stem
        output_path = output_dir / output_name
        sidecar_path = output_path.with_suffix(".slide.json")
        notes: list[str] = []

        matched_artifacts = list(artifact_matches.get(output_stem, []))
        if not matched_artifacts:
            stripped_title = strip_section_prefix(section.title)
            matched_artifacts = list(artifact_matches.get(stripped_title, []))
        matched_sources = list(source_matches.get(resource_title, []))
        if not matched_sources and output_stem != resource_title:
            matched_sources = list(source_matches.get(output_stem, []))

        local_output_exists = output_path.exists()
        local_sidecar_exists = sidecar_path.exists()

        if len(matched_artifacts) > 1:
            notes.append(f"Multiple artifacts matched title '{output_stem}'")
        if len(matched_sources) > 1:
            notes.append(f"Multiple sources matched title '{resource_title}'")

        artifact = matched_artifacts[0] if len(matched_artifacts) == 1 else None
        source = matched_sources[0] if len(matched_sources) == 1 else None

        artifact_id = _coerce_id(artifact) if artifact else None
        artifact_status = _coerce_status(artifact) if artifact else None
        source_id = _coerce_id(source) if source else None

        if local_output_exists:
            status = "completed_local"
            resume_action = "skip"
        elif notes:
            status = "ambiguous"
            resume_action = "manual_review"
        elif artifact_id and artifact_status == "completed":
            status = "completed_remote_needs_download"
            resume_action = "download_only"
        elif artifact_id and artifact_status in {"running", "processing", "pending", "queued", "in_progress"}:
            status = "running_remote"
            resume_action = "wait_and_finalize"
        elif artifact_id and artifact_status in {"failed", "cancelled", "canceled"}:
            status = "failed_remote"
            resume_action = "create_from_existing_source" if source_id else "upload_and_create"
        elif source_id:
            status = "source_only"
            resume_action = "create_from_existing_source"
        elif artifact_id:
            status = "unknown"
            resume_action = "manual_review"
            notes.append("Artifact exists but status could not be classified")
        else:
            status = "not_started"
            resume_action = "upload_and_create"

        states.append(
            RecoverySectionState(
                section_id=section.id,
                section_title=section.title,
                resource_title=resource_title,
                output_name=output_name,
                status=status,
                source_id=source_id,
                artifact_id=artifact_id,
                artifact_status=artifact_status,
                local_output_exists=local_output_exists,
                local_sidecar_exists=local_sidecar_exists,
                resume_action=resume_action,
                notes=notes,
            )
        )
    return states


def find_section(manifest: CourseManifest, section_id: str) -> Section:
    for section in manifest.sections:
        if section.id == section_id:
            return section
    raise KeyError(f"Section '{section_id}' was not found in {manifest.source_path}")


def manifest_to_dict(manifest: CourseManifest) -> dict[str, Any]:
    return {
        "course_title": manifest.course_title,
        "source_path": manifest.source_path,
        "sections": [asdict(section) for section in manifest.sections],
    }


def load_course_manifest(path: str | Path) -> CourseManifest:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Manifest does not exist: {source}")

    suffix = source.suffix.lower()
    if suffix == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        return _normalize_manifest(data, source)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "YAML manifest support requires PyYAML. Install it or use JSON/Markdown."
            ) from exc
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
        return _normalize_manifest(data, source)
    if suffix == ".md":
        return _load_markdown_manifest(source)
    raise RuntimeError(
        f"Unsupported manifest extension '{suffix}'. Use .json, .yaml, .yml, or .md."
    )


def _normalize_manifest(data: Any, source: Path) -> CourseManifest:
    if not isinstance(data, dict):
        raise RuntimeError(f"Manifest root must be an object: {source}")

    if isinstance(data.get("chapters"), list):
        return _load_fira_course_manifest(data, source)

    course_title = str(data.get("course_title") or data.get("title") or source.stem).strip()
    raw_sections = data.get("sections") or data.get("chapters")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise RuntimeError(f"Manifest must contain a non-empty sections list: {source}")

    sections: list[Section] = []
    counter = 0

    def visit(items: list[Any], path_parts: list[int]) -> None:
        nonlocal counter
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(f"Each section must be an object: {source}")

            next_path = [*path_parts, index]
            section_id = str(item.get("id") or ".".join(str(part) for part in next_path))
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                raise RuntimeError(f"Section {section_id} is missing a title in {source}")

            content = str(
                item.get("content")
                or item.get("text")
                or item.get("body")
                or item.get("markdown")
                or ""
            ).strip()
            focus = item.get("focus") or item.get("prompt")
            resource_title = item.get("resource_title") or item.get("source_title")
            output_name = item.get("output_name") or item.get("filename")

            if content:
                counter += 1
                slug = str(item.get("slug") or slugify(title, fallback=f"section-{counter}"))
                sections.append(
                    Section(
                        id=section_id,
                        order=counter,
                        title=title,
                        content=content,
                        slug=slug,
                        output_name=str(output_name or default_output_name(section_id, title)),
                        focus=str(focus).strip() if focus else None,
                        resource_title=str(resource_title).strip() if resource_title else None,
                    )
                )

            children = item.get("sections") or item.get("children") or []
            if children:
                if not isinstance(children, list):
                    raise RuntimeError(
                        f"Section {section_id} has non-list children in {source}"
                    )
                visit(children, next_path)

    visit(raw_sections, [])
    if not sections:
        raise RuntimeError(f"No leaf sections with content were found in {source}")
    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )


def _load_fira_course_manifest(data: dict[str, Any], source: Path) -> CourseManifest:
    course_title = str(data.get("name") or data.get("course_title") or source.stem).strip()
    chapters = data.get("chapters") or []
    if not isinstance(chapters, list):
        raise RuntimeError(f"FIRA course chapters must be a list: {source}")

    sections: list[Section] = []
    counter = 0

    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        chapter_name = str(chapter.get("name") or "").strip()
        if chapter_name in FIRA_SKIP_CHAPTER_NAMES:
            continue

        chapter_sections = chapter.get("sections") or []
        if not isinstance(chapter_sections, list):
            continue

        for index, section_data in enumerate(chapter_sections):
            if not isinstance(section_data, dict):
                continue
            if index == 0:
                continue

            title = str(section_data.get("name") or "").strip()
            if not title:
                continue

            content = extract_fira_section_content(section_data)
            if not content:
                continue

            counter += 1
            section_id = extract_section_id(title) or str(counter)
            sections.append(
                Section(
                    id=section_id,
                    order=counter,
                    title=title,
                    content=content,
                    slug=slugify(strip_section_prefix(title), fallback=f"section-{counter}"),
                    output_name=default_output_name(section_id, title),
                    resource_title=default_resource_title(section_id, title),
                )
            )

    if not sections:
        raise RuntimeError(f"No slide-generation sections were found in {source}")

    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )


def extract_fira_section_content(section_data: dict[str, Any]) -> str:
    preferred_texts: list[str] = []
    fallback_texts: list[str] = []

    for vertical in section_data.get("verticals") or []:
        if not isinstance(vertical, dict):
            continue
        vertical_name = str(vertical.get("name") or "").strip()
        texts = []
        for block in vertical.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if block.get("name") != "文字讲解":
                continue
            text = str(block.get("text") or "").strip()
            if text:
                texts.append(text)
        if not texts:
            continue
        fallback_texts.extend(texts)
        if "训战" not in vertical_name:
            preferred_texts.extend(texts)

    texts = preferred_texts or fallback_texts
    return "\n\n".join(texts).strip()


def _load_markdown_manifest(source: Path) -> CourseManifest:
    lines = source.read_text(encoding="utf-8").splitlines()
    course_title = source.stem
    sections: list[Section] = []
    current_title: str | None = None
    current_lines: list[str] = []
    counter = 0

    def flush() -> None:
        nonlocal counter, current_title, current_lines
        if not current_title:
            return
        content = "\n".join(current_lines).strip()
        if content:
            counter += 1
            section_id = str(counter)
            sections.append(
                Section(
                    id=section_id,
                    order=counter,
                    title=current_title,
                    content=content,
                    slug=slugify(current_title, fallback=f"section-{counter}"),
                    output_name=default_output_name(section_id, current_title),
                )
            )
        current_title = None
        current_lines = []

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            if current_title:
                current_lines.append(line)
            continue

        level = len(match.group(1))
        title = match.group(2).strip()
        if level == 1 and title and course_title == source.stem:
            course_title = title
            continue
        if level == 2:
            flush()
            current_title = title
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)

    flush()
    if not sections:
        raise RuntimeError(
            f"Markdown manifest {source} must use '## Section Title' headings with body text."
        )
    return CourseManifest(
        course_title=course_title,
        sections=sections,
        source_path=str(source.resolve()),
    )
