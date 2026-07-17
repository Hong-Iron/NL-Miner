// sync-backend.mjs — keep plugin/backend/ in sync with the repo's Python core.
// Copies src/, interface/, observer/, payload.py, grammar.cfg, requirements.txt
// into plugin/backend/ so the shipped plugin is self-contained. Run before
// esbuild (`npm run build`) or alone (`npm run sync`). Excludes __pycache__/.venv.
import { cpSync, copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));     // plugin/scripts
const repoRoot = resolve(here, "..", "..");
const backend = resolve(here, "..", "backend");

mkdirSync(backend, { recursive: true });

function copyDir(name) {
  const src = join(repoRoot, name);
  const dst = join(backend, name);
  if (!existsSync(src)) return;
  rmSync(dst, { recursive: true, force: true });
  cpSync(src, dst, {
    recursive: true,
    filter: (s) => !s.includes("__pycache__") && !s.includes(".venv"),
  });
}

for (const d of ["src", "interface", "observer"]) copyDir(d);

for (const f of ["payload.py", "grammar.cfg", "requirements.txt"]) {
  const src = join(repoRoot, f);
  if (existsSync(src)) copyFileSync(src, join(backend, f));
}

// cky.GRAMMAR_PATH resolves to backend/grammar.cfg (src's parent.parent).
console.log("backend synced ->", backend);
