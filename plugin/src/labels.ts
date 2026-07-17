// Plain-language names for the grammar vocabulary the backend emits, so the
// UI never shows a bare symbol to a reader who doesn't know it. Three
// vocabularies, all pinned on the Python side:
//   * block kinds   — src/schema.py BlockKind
//   * phrase labels — grammar.cfg non-terminals (CKY constituents)
//   * dep relations — src/transition_parser.py LEFT_RULE / RIGHT_RULE
//
// Each entry gives a plain name and a hint per language. The English names are
// deliberately *not* the technical terms — "naming chunk", not "NP". Falling
// back to the symbol for English readers would defeat the point; the symbol
// belongs in the hint, where it is available to those who want it and invisible
// to those who don't.

import { getLang } from "./i18n";

export interface Term {
  plain: string;
  hint: string;
}
interface Bi {
  en: Term;
  ko: Term;
}

/** Block kinds (``ViewBlock.kind``). */
const BLOCK_TERMS: Record<string, Bi> = {
  heading: {
    en: { plain: "Heading", hint: "heading — the line that titles a section" },
    ko: { plain: "제목", hint: "heading — 문서를 나누는 제목 줄" },
  },
  para: {
    en: { plain: "Paragraph", hint: "paragraph — a block of sentences" },
    ko: { plain: "문단", hint: "paragraph — 여러 문장이 모인 덩어리" },
  },
  sentence: {
    en: { plain: "Sentence", hint: "sentence — one sentence, ended by a full stop" },
    ko: { plain: "문장", hint: "sentence — 마침표로 끊기는 한 문장" },
  },
  list: {
    en: { plain: "List", hint: "list — an itemised line" },
    ko: { plain: "목록", hint: "list — 항목을 나열한 줄" },
  },
  quote: {
    en: { plain: "Quote", hint: "quote — quoted text" },
    ko: { plain: "인용", hint: "quote — 인용한 글" },
  },
  code: {
    en: { plain: "Code", hint: "code — a code block (not grammar-analysed)" },
    ko: { plain: "코드", hint: "code — 코드 블럭 (문법 분석 안 함)" },
  },
  table: {
    en: { plain: "Table", hint: "table" },
    ko: { plain: "표", hint: "table — 표" },
  },
};

/** CKY constituent labels (``ViewPhrase.label``). */
const PHRASE_TERMS: Record<string, Bi> = {
  NP: {
    en: { plain: "naming chunk", hint: "NP (noun phrase) — words naming a person or thing" },
    ko: { plain: "이름 덩어리", hint: "NP (noun phrase) — 사람·사물을 가리키는 말 묶음" },
  },
  VP: {
    en: { plain: "doing chunk", hint: "VP (verb phrase) — words for an action or state" },
    ko: { plain: "움직임 덩어리", hint: "VP (verb phrase) — 동작·상태를 나타내는 말 묶음" },
  },
  PP: {
    en: {
      plain: "where/how chunk",
      hint: "PP (prepositional phrase) — 'in the box', attaching a relation",
    },
    ko: {
      plain: "위치·관계 덩어리",
      hint: "PP (prepositional phrase) — 'in the box' 처럼 관계를 붙이는 묶음",
    },
  },
  ADJP: {
    en: { plain: "describing chunk", hint: "ADJP (adjective phrase) — words describing something" },
    ko: { plain: "꾸밈 덩어리", hint: "ADJP (adjective phrase) — 무언가를 꾸며 주는 말 묶음" },
  },
  ADVP: {
    en: { plain: "manner chunk", hint: "ADVP (adverb phrase) — how much, how, when" },
    ko: { plain: "방식 덩어리", hint: "ADVP (adverb phrase) — 어떻게·얼마나를 나타내는 묶음" },
  },
  S: {
    en: { plain: "whole sentence", hint: "S (sentence) — a subject and a verb" },
    ko: { plain: "문장 전체", hint: "S (sentence) — 주어와 동사를 갖춘 한 문장" },
  },
  SBAR: {
    en: { plain: "embedded sentence", hint: "SBAR — a sentence inside another sentence" },
    ko: { plain: "안긴 문장", hint: "SBAR — 다른 문장 속에 들어간 문장" },
  },
  START: {
    en: { plain: "whole sentence", hint: "START — the root of the parse tree" },
    ko: { plain: "문장 전체", hint: "START — 파스 트리의 뿌리" },
  },
};

/** Dependency relations, keyed without the ``dep:`` prefix. */
const DEP_TERMS: Record<string, Bi> = {
  root: {
    en: { plain: "the main word", hint: "root — the word the sentence hangs on" },
    ko: { plain: "중심 말", hint: "root — 문장의 중심이 되는 말" },
  },
  nsubj: {
    en: { plain: "who/what does it", hint: "nsubj (subject) — who performs the action" },
    ko: { plain: "누가·무엇이", hint: "nsubj (subject) — 동작을 하는 주체" },
  },
  obj: {
    en: { plain: "what it acts on", hint: "obj (object) — what the action affects" },
    ko: { plain: "무엇을", hint: "obj (object) — 동작이 미치는 대상" },
  },
  obl: {
    en: { plain: "where/how", hint: "obl (oblique) — the setting of the action" },
    ko: { plain: "어디서·어떻게", hint: "obl (oblique) — 동작의 배경이 되는 말" },
  },
  det: {
    en: { plain: "which one", hint: "det (determiner) — 'the'/'a', narrowing the noun" },
    ko: { plain: "어느 것", hint: "det (determiner) — 'the/a' 처럼 범위를 정해 주는 말" },
  },
  amod: {
    en: { plain: "what kind", hint: "amod (adjective modifier) — describes the noun" },
    ko: { plain: "어떤", hint: "amod (adjective modifier) — 이름을 꾸미는 말" },
  },
  advmod: {
    en: { plain: "in what way", hint: "advmod (adverb modifier) — describes the action" },
    ko: { plain: "어떻게", hint: "advmod (adverb modifier) — 동작을 꾸미는 말" },
  },
  aux: {
    en: { plain: "helper verb", hint: "aux (auxiliary) — 'will'/'have', supporting the verb" },
    ko: { plain: "도움 동사", hint: "aux (auxiliary) — 'will/have' 처럼 거드는 동사" },
  },
  case: {
    en: { plain: "relation marker", hint: "case — 'of'/'in', marking a relation" },
    ko: { plain: "관계 표시", hint: "case — 'of/in' 처럼 관계를 표시하는 말" },
  },
  compound: {
    en: { plain: "joined name", hint: "compound — two words acting as one name" },
    ko: { plain: "붙은 이름", hint: "compound — 두 낱말이 한 덩어리로 붙은 이름" },
  },
  nummod: {
    en: { plain: "how many", hint: "nummod (numeric modifier) — a quantity" },
    ko: { plain: "몇 개", hint: "nummod (numeric modifier) — 수량을 나타내는 말" },
  },
  nmod: {
    en: { plain: "whose/of what", hint: "nmod (nominal modifier) — a noun describing a noun" },
    ko: { plain: "무엇의", hint: "nmod (nominal modifier) — 이름을 꾸미는 이름" },
  },
  xcomp: {
    en: { plain: "follow-on action", hint: "xcomp — 'want to go', a trailing action" },
    ko: { plain: "이어지는 동작", hint: "xcomp — 'want to go' 처럼 뒤따르는 동작" },
  },
};

/** Phrases fold to (label, head) for display. Phrase nodes are canonical over
 *  (surface, label), but the view JSON exposes only the label and the head
 *  token — so "The octopus" and "octopus" are distinct nodes that render as the
 *  same row. Two identical rows read as a bug, so collapse them; the count the
 *  UI shows is the folded one, to match what's on screen. */
export function displayPhrases<T extends { label: string; head_text: string }>(
  phrases: readonly T[],
): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const p of phrases) {
    const key = `${p.label} ${p.head_text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(p);
  }
  return out;
}

function look(table: Record<string, Bi>, key: string): Term {
  const bi = table[key];
  if (!bi) return { plain: key, hint: key };
  return getLang() === "ko" ? bi.ko : bi.en;
}

export const blockTerm = (kind: string): Term => look(BLOCK_TERMS, kind);
export const phraseTerm = (label: string): Term => look(PHRASE_TERMS, label);
/** Accepts either ``dep:nsubj`` or ``nsubj``. */
export const depTerm = (label: string): Term =>
  look(DEP_TERMS, label.replace(/^dep:/, ""));

/** Heading levels read as "Heading 2"; everything else is just its plain name. */
export function blockTitle(kind: string, level: number | null): string {
  const t = blockTerm(kind);
  return kind === "heading" && level != null ? `${t.plain} ${level}` : t.plain;
}
