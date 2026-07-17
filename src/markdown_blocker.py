"""Phase 1 — markdown-aware blocking → Block tree.

Regex-based CommonMark *subset* (phase1_blocks_links.md §2.1). Captures the
block *structure* (heading/para/code/list/quote/table); inline markup is
left in ``Block.text`` and normalised later by the linker. ``span`` is an
original-text char offset ``[start, end)`` so offsets survive re-tokenisation.

Honours §3.2/§3.3: ``#`` inside a fenced block is not a heading; heading
level stack fills skipped levels onto the nearest higher parent.
"""
from __future__ import annotations

import re
from uuid import UUID

from .schema import Block, BlockKind

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?\S)\s*#*\s*$")
FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")
QUOTE_RE = re.compile(r"^\s*>\s?\S")
TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def blockify(text: str, file_uuid: UUID, *, md: bool = True) -> list[Block]:
    """Split ``text`` into a parent-linked ``Block`` tree.

    ``md=True`` (default, ``.md``) does heading + body blocking. ``md=False``
    (``.txt``) does paragraph → ``SENTENCE`` splitting (phase1 §1 step 3).
    """
    if md:
        return _blockify_markdown(text, file_uuid)
    return _blockify_plain(text, file_uuid)


# --------------------------------------------------------------------------- md


def _classify_line(raw: str) -> BlockKind:
    if LIST_RE.match(raw):
        return BlockKind.LIST
    if QUOTE_RE.match(raw):
        return BlockKind.QUOTE
    if TABLE_RE.match(raw):
        return BlockKind.TABLE
    return BlockKind.PARA


def _fence_close(raw: str, fence_char: str, fence_len: int) -> bool:
    return bool(re.match(rf"^\s*{re.escape(fence_char)}{{{fence_len},}}\s*$", raw))


def _blockify_markdown(text: str, file_uuid: UUID) -> list[Block]:
    blocks: list[Block] = []
    heading_stack: list[Block] = []      # top = innermost enclosing heading
    pending: dict | None = None          # {kind, start, last_end, lines}
    in_fence = False
    fence_char = fence_len = ""
    fence_lines: list[str] = []
    fence_start = 0

    def parent() -> UUID | None:
        return heading_stack[-1].block_id if heading_stack else None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        body = "\n".join(pending["lines"]).strip()
        if body and pending["last_end"] >= pending["start"]:
            blocks.append(
                Block(
                    file_uuid=file_uuid,
                    kind=pending["kind"],
                    text=body,
                    span=(pending["start"], pending["last_end"]),
                    parent_block_id=parent(),
                )
            )
        pending = None

    def start_block(kind: BlockKind, start: int, line: str) -> None:
        nonlocal pending
        pending = {"kind": kind, "start": start, "last_end": start + len(line), "lines": [line]}

    pos = 0
    for raw in text.split("\n"):
        start = pos
        end = pos + len(raw)
        pos = end + 1  # the consumed newline

        # Inside a fenced code block: collect verbatim, ignore all other syntax.
        if in_fence:
            fence_lines.append(raw)
            if _fence_close(raw, fence_char, fence_len):
                blocks.append(
                    Block(
                        file_uuid=file_uuid,
                        kind=BlockKind.CODE,
                        text="\n".join(fence_lines),
                        span=(fence_start, end),
                        parent_block_id=parent(),
                    )
                )
                in_fence = False
                fence_char = fence_len = ""
                fence_lines = []
            continue

        mo = FENCE_OPEN_RE.match(raw)
        if mo:
            flush()
            tok = mo.group(1)
            in_fence = True
            fence_char = tok[0]
            fence_len = len(tok)
            fence_lines = [raw]
            fence_start = start
            continue

        h = HEADING_RE.match(raw)
        if h:
            flush()
            level = len(h.group(1))
            htext = h.group(2)
            while heading_stack and heading_stack[-1].level is not None and heading_stack[-1].level >= level:
                heading_stack.pop()  # strict parent = strictly lower level (§3.1)
            blk = Block(
                file_uuid=file_uuid,
                kind=BlockKind.HEADING,
                level=level,
                text=htext,
                span=(start, end),
                parent_block_id=parent(),
            )
            blocks.append(blk)
            heading_stack.append(blk)
            continue

        if raw.strip() == "":
            flush()
            continue

        kind = _classify_line(raw)
        if pending is None:
            start_block(kind, start, raw)
        elif pending["kind"] == kind:
            pending["lines"].append(raw)
            pending["last_end"] = end
        else:
            flush()
            start_block(kind, start, raw)

    if in_fence:  # unterminated fence → close at EOF
        blocks.append(
            Block(
                file_uuid=file_uuid,
                kind=BlockKind.CODE,
                text="\n".join(fence_lines),
                span=(fence_start, end),
                parent_block_id=parent(),
            )
        )
    flush()
    return blocks


# -------------------------------------------------------------------------- txt


def _emit_sentences(blocks: list[Block], file_uuid: UUID, start: int, last_end: int, body_raw: str) -> None:
    body = body_raw.strip()
    if not body:
        return
    cursor = 0
    for s in SENT_SPLIT_RE.split(body):
        stext = s.strip()
        if not stext:
            cursor += len(s)
            continue
        idx = body.find(stext, max(cursor - 1, 0))
        if idx < 0:
            idx = cursor
        blocks.append(
            Block(
                file_uuid=file_uuid,
                kind=BlockKind.SENTENCE,
                text=stext,
                span=(start + idx, start + idx + len(stext)),
                parent_block_id=None,
            )
        )
        cursor = idx + len(s)


def _blockify_plain(text: str, file_uuid: UUID) -> list[Block]:
    blocks: list[Block] = []
    acc: list[str] = []
    acc_start: int | None = None
    acc_last_end = 0
    pos = 0
    for raw in text.split("\n"):
        end = pos + len(raw)
        if raw.strip() == "":
            if acc and acc_start is not None:
                _emit_sentences(blocks, file_uuid, acc_start, acc_last_end, "\n".join(acc))
                acc = []
                acc_start = None
            pos = end + 1
            continue
        if acc_start is None:
            acc_start = pos
        acc.append(raw)
        acc_last_end = end
        pos = end + 1
    if acc and acc_start is not None:
        _emit_sentences(blocks, file_uuid, acc_start, acc_last_end, "\n".join(acc))
    return blocks
