"""Phase 3 — frontmatter parsing, normalisation, language detection.

Per phase3_ingest.md §2.4 this stays a *thin* layer: block splitting is the
Phase 1 blocker's job. preprocess only refines (BOM/line-ending) and enriches
``FileMeta`` (creator from frontmatter, ``mtime`` if missing, ``lang`` via
script-ratio). No external deps — regex frontmatter + stdlib only.

Span invariant (phase0_schema.md §2.3): offsets are char offsets into the text
we *return* here, so callers (blocker) treat this as "the original". We keep
the transform non-destructive enough that ``span`` stays meaningful: BOM strip
and ``\\r\\n``→``\\n`` are 1:1 in codepoints, and the frontmatter block (which
would shift offsets) is removed *before* blocking, so the body offsets are
internally consistent.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .schema import FileMeta

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
KV_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.+?)\s*$", re.MULTILINE)

# CJK script ranges (codepoint spans suffice for ratio detection)
HANGUL = re.compile(r"[가-힯]")
HAN = re.compile(r"[㐀-鿿豈-﫿]")
KANA = re.compile(r"[ぁ-ヿ]")
LATIN = re.compile(r"[A-Za-z]")

# Frontmatter keys that map to FileMeta.creator (first hit wins).
CREATOR_KEYS = ("authors", "author", "creator")


def strip_bom(text: str) -> str:
    return text.lstrip("﻿")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (meta_kvs, body). Body is everything after the ``---…---`` block."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    block = m.group(1)
    body = text[m.end() :]
    meta: dict[str, str] = {}
    for km in KV_RE.finditer(block):
        meta[km.group(1).lower()] = km.group(2).strip()
    return meta, body


def detect_lang(text: str) -> str:
    """Crude script-ratio detector (phase3 §2.4). Korean wins ties over Han because
    Hangul is unambiguous; pure Han → zh; kana → ja; else en (brave fallback)."""
    c = Counter()
    for name, rx in (("hangul", HANGUL), ("han", HAN), ("kana", KANA), ("latin", LATIN)):
        c[name] = len(rx.findall(text))
    if c["hangul"]:
        return "ko"
    if c["kana"]:
        return "ja"
    if c["han"] and c["han"] >= c["latin"]:
        return "zh"
    return "en"


def preprocess(file: FileMeta) -> tuple[str, FileMeta]:
    """Read + normalise + enrich ``file``. Returns (body_text, updated_file).

    Robustness: a stray non-UTF-8 byte (latin-1 export, truncated multibyte)
    must not crash the whole pipeline. We decode strictly first — so valid
    UTF-8 is byte-identical and offsets are exact — then fall back to
    ``errors="replace"`` (U+FFFD) so a bad byte turns into a replacement char
    instead of raising. Lossy only for genuinely invalid input.
    """
    raw_bytes = file.file_path.read_bytes()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw = raw_bytes.decode("utf-8", errors="replace")
    raw = strip_bom(raw)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    meta, body = split_frontmatter(raw)

    updates: dict = {}
    if file.creator is None:
        for key in CREATOR_KEYS:
            if key in meta:
                updates["creator"] = meta[key]
                break
    if file.mtime is None:
        try:
            updates["mtime"] = file.file_path.stat().st_mtime
        except OSError:
            pass
    if file.lang is None:
        updates["lang"] = detect_lang(body)

    if updates:
        file = file.model_copy(update=updates)
    return body, file
