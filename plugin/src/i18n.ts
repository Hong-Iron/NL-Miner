// UI strings, English and Korean.
//
// English is the fallback everywhere: a missing Korean entry shows English
// rather than a raw key, so a half-finished translation degrades to something
// readable instead of `panel.blocks`.
//
// The language is module state rather than threaded through every call. Views
// are re-rendered wholesale on a settings change, so there is no window in
// which half the pane is in one language — and `t()` stays callable from the
// plain functions (labels, strengthLabel) that aren't view methods.

export type Lang = "en" | "ko";
/** ``auto`` follows Obsidian's own UI language. */
export type LangSetting = "auto" | Lang;

type Entry = { en: string; ko: string };

const STRINGS = {
  // -- shared / chrome
  "app.name": { en: "NL-Miner", ko: "NL-Miner" },
  "tab.note": { en: "Note", ko: "노트" },
  "tab.graph": { en: "Graph", ko: "그래프" },
  "tab.vault": { en: "Vault", ko: "볼트" },
  "tab.audit": { en: "Audit", ko: "점검" },
  "btn.reindex": { en: "Reindex", ko: "다시 분석" },
  "btn.indexVault": { en: "Index vault", ko: "전체 분석" },
  "btn.refresh": { en: "Refresh", ko: "새로고침" },
  "btn.fit": { en: "Fit", ko: "화면 맞춤" },
  "btn.insertLink": { en: "Insert link", ko: "링크 넣기" },

  // -- empty / error states
  "empty.openNote": { en: "Open a note.", ko: "노트를 열어 주세요." },
  "empty.openNoteGraph": {
    en: "Open a note to see its structure.",
    ko: "노트를 열면 그 노트의 구조를 보여줍니다.",
  },
  "empty.notText": {
    en: "NL-Miner only handles text notes (.md, .txt).\n{name} is not analysable.",
    ko: "NL-Miner는 텍스트 노트(.md, .txt)만 다룹니다.\n{name} 은(는) 분석 대상이 아닙니다.",
  },
  "empty.notIndexed": {
    en: "Nothing analysed yet — run Index vault first.",
    ko: "아직 분석된 내용이 없습니다 — 먼저 인덱싱하세요.",
  },
  "empty.loading": { en: "Analysing…", ko: "분석 중…" },
  "empty.noNotes": {
    en: "No notes — run Index vault first.",
    ko: "노트가 없습니다 — 먼저 '전체 분석'을 실행하세요.",
  },
  "empty.noConcept": {
    en: "No notes discuss '{concept}'.",
    ko: "'{concept}' 를 다루는 노트가 없습니다.",
  },
  "err.vaultGraph": {
    en: "Couldn't load the vault graph. Run Index vault first.",
    ko: "볼트 그래프를 불러오지 못했습니다. 먼저 '전체 분석'을 실행하세요.",
  },
  "err.audit": {
    en: "Couldn't run the audit. Run Index vault first.",
    ko: "점검을 실행하지 못했습니다. 먼저 '전체 분석'으로 볼트를 인덱싱하세요.",
  },
  "err.noteNotFound": { en: "Note not found: {name}", ko: "노트를 찾지 못했습니다: {name}" },
  "err.needEditor": {
    en: "Open a note in the editor to insert a link",
    ko: "링크를 넣으려면 편집 중인 노트가 필요합니다",
  },
  "err.notTextNotice": {
    en: "NL-Miner: {name} is not a text note, so it can't be analysed",
    ko: "NL-Miner: {name} 은(는) 텍스트 노트가 아니라 분석할 수 없습니다",
  },

  // -- note panel
  "note.meta": {
    en: "{blocks} blocks · {phrases} phrase chunks · {deps} word relations",
    ko: "블럭 {blocks}개 · 뜻 덩어리 {phrases}개 · 말의 관계 {deps}개",
  },
  "note.parseOff": { en: " · grammar analysis off", ko: " · 문법 분석 꺼짐" },
  "note.limit.nonEnglish": {
    en: "This note isn't English, so grammar analysis was skipped ({n} sentences). Blocks and concepts still work.",
    ko: "이 노트는 영어가 아니라 문법 분석을 건너뛰었습니다 (문장 {n}개). 블럭과 낱말은 그대로 쓸 수 있습니다.",
  },
  "note.limit.noPhrases": {
    en: "No phrase chunks found here — one word outside the grammar's vocabulary fails the whole sentence.",
    ko: "이 노트에서는 뜻 덩어리를 찾지 못했습니다 — 문법 사전에 없는 낱말이 있으면 구절 분석이 통째로 실패합니다.",
  },
  "note.empty": { en: "(empty)", ko: "(빈 줄)" },
  "note.badge": { en: "{p} chunks {d} relations", ko: "{p}덩어리 {d}관계" },
  "group.phrases": { en: "Phrase chunks ({n})", ko: "뜻 덩어리 ({n})" },
  "group.phrases.hint": {
    en: "the sentence grouped into units of meaning (constituent phrases)",
    ko: "문장을 의미 단위로 묶은 것 (constituent phrases)",
  },
  "group.deps": { en: "Word relations ({n})", ko: "말과 말의 관계 ({n})" },
  "group.deps.hint": {
    en: "which word attaches to which (dependency arcs)",
    ko: "어떤 낱말이 어떤 낱말에 걸리는지 (dependency arcs)",
  },
  "group.links": { en: "Linked from here", ko: "이어진 곳" },
  "group.links.loading": { en: "loading…", ko: "불러오는 중…" },
  "group.links.error": { en: "couldn't load", ko: "불러오지 못했습니다" },
  "group.links.none": { en: "none", ko: "없음" },
  "group.out": { en: "Points to ({n})", ko: "여기서 나가는 연결 ({n})" },
  "group.out.hint": { en: "what this block refers to", ko: "이 블럭이 가리키는 대상" },
  "group.in": { en: "Referred to by ({n})", ko: "여기로 들어오는 연결 ({n})" },
  "group.in.hint": { en: "other blocks pointing here", ko: "이 블럭을 가리키는 다른 블럭" },
  "group.more": { en: "{n} more", ko: "그 밖에 {n}개 더" },
  "phrase.head": { en: "head word", ko: "중심 낱말" },

  // -- bridges
  "bridge.title": { en: "Notes worth linking ({n})", ko: "이어질 만한 노트 ({n})" },
  "bridge.subtitle": {
    en: "Notes that share uncommon concepts with this one but aren't linked yet.",
    ko: "드물게 쓰이는 개념을 함께 쓰는데 아직 링크가 없는 노트입니다.",
  },
  "bridge.tooltip": {
    en: "Shared concepts (rarest first): {shared}\nRelatedness {score}\nClick to open the note",
    ko: "함께 쓰는 개념 (드문 것부터): {shared}\n연관 점수 {score}\n클릭하면 노트를 엽니다",
  },
  "bridge.insertTip": { en: "Insert [[{name}]] at the cursor", ko: "커서 위치에 [[{name}]] 를 넣습니다" },
  "bridge.inserted": { en: "[[{name}]] inserted", ko: "[[{name}]] 삽입됨" },
  "bridge.chipTip": {
    en: "Graph the notes that discuss '{word}'",
    ko: "'{word}' 를 다루는 노트들을 그래프로 보기",
  },
  "strength.strong": { en: "strong", ko: "강함" },
  "strength.medium": { en: "medium", ko: "보통" },
  "strength.weak": { en: "weak", ko: "약함" },

  // -- graph
  "graph.meta": { en: "{n} blocks · click one for detail", ko: "{n}개 블럭 · 블럭을 누르면 자세히 보입니다" },
  "graph.intro": { en: "(opening)", ko: "(도입부)" },
  "graph.prose": { en: "{n} paragraphs", ko: "문단 {n}개" },
  "graph.prose1": { en: "1 paragraph", ko: "문단 1개" },
  "graph.sectionTip": {
    en: "{title}\n{n} blocks in this section\n\nClick to open it in the Note tab",
    ko: "{title}\n이 구역의 블럭 {n}개\n\n클릭하면 노트 탭에서 봅니다",
  },
  "graph.proseTip": {
    en: "{n} paragraphs of prose\n\nClick for detail",
    ko: "본문 문단 {n}개\n\n클릭하면 자세히 보입니다",
  },
  "graph.metaPanel": {
    en: "{n} blocks · click one to see detail in the Note tab",
    ko: "{n}개 블럭 · 블럭을 누르면 노트 탭에 자세히 보입니다",
  },
  "graph.section": { en: "{n} blocks", ko: "블럭 {n}개" },

  // -- vault graph
  "vault.scopeAll": { en: "Whole vault", ko: "전체 볼트" },
  "vault.scopeClear": { en: "Clear concept scope", ko: "개념 범위 해제" },
  "vault.meta": {
    en: "{notes} notes · {links} links · {bridges} suggested",
    ko: "노트 {notes} · 링크 {links} · 제안 {bridges}",
  },
  "vault.noteTip": {
    en: "{name}\n{folder}\n{blocks} blocks{concepts}\n\nClick: highlight links · Double-click: open",
    ko: "{name}\n{folder}\n블럭 {blocks}{concepts}\n\n클릭: 연결 강조 · 더블클릭: 노트 열기",
  },
  "vault.conceptsTip": { en: "\nConcepts: {list}", ko: "\n개념: {list}" },
  "vault.linkTip": { en: "Link: {a} → {b}", ko: "링크: {a} → {b}" },
  "vault.bridgeTip": {
    en: "Suggested (not linked): {a} ↔ {b}\n{n} shared concepts: {shared}",
    ko: "제안 (링크 없음): {a} ↔ {b}\n공유 개념 {n}: {shared}",
  },
  "vault.root": { en: "(root)", ko: "(최상위)" },

  // -- audit
  "audit.meta": { en: "{files} notes · {links} links", ko: "노트 {files}개 · 링크 {links}개" },
  "audit.orphans": { en: "Orphan notes ({n})", ko: "외딴 노트 ({n})" },
  "audit.orphans.sub": {
    en: "Notes with no link in or out.",
    ko: "오가는 링크가 하나도 없는 노트입니다.",
  },
  "audit.orphans.none": { en: "None — everything is connected.", ko: "없습니다 — 전부 이어져 있습니다." },
  "audit.pairs": { en: "Notes worth linking ({n})", ko: "이어질 만한 노트 쌍 ({n})" },
  "audit.pairs.sub": {
    en: "Notes sharing uncommon concepts with no link between them, strongest first.",
    ko: "드문 개념을 함께 쓰는데 링크가 없는 노트들입니다. 연관이 강한 순.",
  },
  "audit.pairs.none": { en: "None.", ko: "없습니다." },
  "audit.pairTip": {
    en: "Relatedness {score} · {n} shared concepts",
    ko: "연관 점수 {score} · 함께 쓰는 개념 {n}개",
  },

  // -- index notice
  "notice.indexing": { en: "NL-Miner: indexing…", ko: "NL-Miner: 분석 중…" },
  "notice.indexed": {
    en: "{files} file(s), {edges} edge(s)",
    ko: "{files}개 파일, {edges}개 연결",
  },
  "notice.dedup": { en: "{pct}% duplicate words saved", ko: "중복 낱말 {pct}% 절약" },
  "notice.cky": { en: "grammar {pct}%", ko: "문법 분석 {pct}%" },
  "notice.skippedLang": { en: "{n} non-English sentences skipped", ko: "비영어 문장 {n}개 제외" },
  "notice.reindexed": { en: "NL-Miner: reindexed {name}", ko: "NL-Miner: {name} 다시 분석함" },
  "notice.needLocalVault": {
    en: "NL-Miner needs a local (filesystem) vault",
    ko: "NL-Miner는 로컬(파일시스템) 볼트가 필요합니다",
  },
} as const satisfies Record<string, Entry>;

export type StringKey = keyof typeof STRINGS;

let LANG: Lang = "en";

export function setLang(lang: Lang): void {
  LANG = lang;
}

export function getLang(): Lang {
  return LANG;
}

/** Resolve the setting to a concrete language.
 *
 *  Obsidian has no public API for its UI language, but it persists the choice
 *  under the ``language`` key in localStorage — the established way for a plugin
 *  to follow along. Anything we don't translate falls back to English. */
export function resolveLang(setting: LangSetting): Lang {
  if (setting === "en" || setting === "ko") return setting;
  try {
    const ob = window.localStorage.getItem("language");
    return ob === "ko" ? "ko" : "en";
  } catch {
    return "en";
  }
}

/** Look up a string, filling ``{name}`` placeholders. */
export function t(
  key: StringKey,
  params?: Record<string, string | number>,
): string {
  const entry = STRINGS[key] as Entry;
  let s = (LANG === "ko" ? entry.ko : entry.en) || entry.en;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.split(`{${k}}`).join(String(v));
    }
  }
  return s;
}
