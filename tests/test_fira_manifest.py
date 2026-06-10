from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path("/Users/liupengcheng/develop/open_source/test-notebook-course-gen")
SCRIPTS_DIR = REPO_ROOT / "nlm-course-slides" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import extract_fira_section_content, load_course_manifest


def _write_fira_manifest(tmp_path: Path, *, sections: list[dict]) -> Path:
    manifest_path = tmp_path / "fira-course.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "测试 FIRA 课程",
                "course_id": "course-1",
                "chapters": [
                    {
                        "name": "第一章",
                        "sections": [
                            {
                                "name": "1.1 概述",
                                "verticals": [
                                    {
                                        "name": "赋能内容",
                                        "blocks": [
                                            {
                                                "name": "文字讲解",
                                                "category": "html",
                                                "text": "概述内容",
                                            }
                                        ],
                                    }
                                ],
                            },
                            *sections,
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_load_course_manifest_keeps_existing_legacy_fira_behavior():
    manifest = load_course_manifest(
        REPO_ROOT / "fira_course-v1_FIRAx_1039467_20260517.json"
    )

    assert len(manifest.sections) == 13
    assert manifest.sections[0].id == "1.2"
    assert manifest.sections[0].title == "1.2 历史的烙印——民族记忆如何塑造商业信任"
    assert manifest.sections[0].content.startswith(
        "历史的烙印——民族记忆如何塑造商业信任"
    )


def test_extract_fira_section_content_falls_back_to_imagesgallery_text():
    section_data = {
        "name": "1.2 新格式小节",
        "verticals": [
            {
                "name": "赋能内容",
                "blocks": [
                    {
                        "name": "有声幻灯片",
                        "category": "imagesgallery",
                        "text": "imagesgallery 正文",
                    }
                ],
            }
        ],
    }

    assert extract_fira_section_content(section_data) == "imagesgallery 正文"


def test_extract_fira_section_content_prefers_first_vertical_imagesgallery_over_legacy_text():
    section_data = {
        "name": "1.2 双格式小节",
        "verticals": [
            {
                "name": "赋能内容",
                "blocks": [
                    {
                        "name": "有声幻灯片",
                        "category": "imagesgallery",
                        "text": "imagesgallery 正文",
                    },
                    {
                        "name": "文字讲解",
                        "category": "html",
                        "text": "旧格式正文",
                    },
                ],
            }
        ],
    }

    assert extract_fira_section_content(section_data) == "imagesgallery 正文"


def test_extract_fira_section_content_ignores_second_vertical_training_text_when_imagesgallery_exists():
    section_data = {
        "name": "1.2 训战回退小节",
        "verticals": [
            {
                "name": "赋能内容",
                "blocks": [
                    {
                        "name": "有声幻灯片",
                        "category": "imagesgallery",
                        "text": "第一单元 imagesgallery 正文",
                    }
                ],
            },
            {
                "name": "在线训战",
                "blocks": [
                    {
                        "name": "文字讲解",
                        "category": "html",
                        "text": "训战提示文案",
                    }
                ],
            },
        ],
    }

    assert extract_fira_section_content(section_data) == "第一单元 imagesgallery 正文"


def test_extract_fira_section_content_returns_empty_when_imagesgallery_exists_but_not_in_first_vertical():
    section_data = {
        "name": "1.2 首单元缺失图文小节",
        "verticals": [
            {
                "name": "赋能内容",
                "blocks": [
                    {
                        "name": "文字讲解",
                        "category": "html",
                        "text": "旧格式正文",
                    }
                ],
            },
            {
                "name": "在线训战",
                "blocks": [
                    {
                        "name": "有声幻灯片",
                        "category": "imagesgallery",
                        "text": "第二单元 imagesgallery 正文",
                    }
                ],
            },
        ],
    }

    assert extract_fira_section_content(section_data) == ""


def test_load_course_manifest_accepts_fira_section_with_imagesgallery_only(tmp_path: Path):
    manifest_path = _write_fira_manifest(
        tmp_path,
        sections=[
            {
                "name": "1.2 新格式小节",
                "verticals": [
                    {
                        "name": "赋能内容",
                        "blocks": [
                            {
                                "name": "有声幻灯片",
                                "category": "imagesgallery",
                                "text": "imagesgallery 正文",
                            }
                        ],
                    }
                ],
            }
        ],
    )

    manifest = load_course_manifest(manifest_path)

    assert [section.id for section in manifest.sections] == ["1.2"]
    assert manifest.sections[0].content == "imagesgallery 正文"


def test_load_course_manifest_skips_section_when_first_vertical_has_no_imagesgallery_text(tmp_path: Path):
    manifest_path = _write_fira_manifest(
        tmp_path,
        sections=[
            {
                "name": "1.2 首单元缺失图文小节",
                "verticals": [
                    {
                        "name": "赋能内容",
                        "blocks": [
                            {
                                "name": "文字讲解",
                                "category": "html",
                                "text": "旧格式正文",
                            }
                        ],
                    },
                    {
                        "name": "在线训战",
                        "blocks": [
                            {
                                "name": "有声幻灯片",
                                "category": "imagesgallery",
                                "text": "第二单元 imagesgallery 正文",
                            }
                        ],
                    },
                ],
            }
        ],
    )

    try:
        load_course_manifest(manifest_path)
    except RuntimeError as exc:
        assert "No slide-generation sections were found" in str(exc)
    else:
        raise AssertionError("expected manifest loading to skip the section and fail")
