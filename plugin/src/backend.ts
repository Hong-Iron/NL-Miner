// Backend — the Python↔TS bridge. Spawns ``<python> -m interface.cli ...`` with
// ``cwd=<plugin>/backend`` (configurable to a repo checkout in settings) and
// ``--db <vault>/.nl-miner/nlp_graph.db``. One JSON object on stdout per
// command; the CLI suppresses human text under ``--json`` so stdout is pure
// JSON (we still defensively take the last object-shaped line).

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { Notice, type App } from "obsidian";

import type {
  AuditJson,
  BridgeJson,
  IndexJson,
  QueryBlocksJson,
  QueryBlockJson,
  GraphJson,
  ViewJson,
  VaultJson,
  NLMinerSettings,
} from "./types";
import { defaultDbPath, defaultBackendDir } from "./util";

const execFileP = promisify(execFile);

export interface BackendCtor {
  settings: NLMinerSettings;
  app: App;
  pluginId: string;
}

export class Backend {
  private opts: BackendCtor;

  constructor(opts: BackendCtor) {
    this.opts = opts;
  }

  /** Refresh the live settings reference (cheap; called before each call). */
  setSettings(settings: NLMinerSettings): void {
    this.opts.settings = settings;
  }

  private cwd(): string {
    const { settings } = this.opts;
    return settings.backendPath && settings.backendPath.length > 0
      ? settings.backendPath
      : defaultBackendDir(this.opts.app);
  }

  private dbPath(): string {
    const { settings } = this.opts;
    return settings.dbPath && settings.dbPath.length > 0
      ? settings.dbPath
      : defaultDbPath(this.opts.app);
  }

  private async run(args: string[]): Promise<string> {
    const exe = this.opts.settings.pythonPath;
    const full = ["-m", "interface.cli", ...args];
    try {
      const { stdout } = await execFileP(exe, full, {
        cwd: this.cwd(),
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
        maxBuffer: 64 * 1024 * 1024,
        timeout: 180000,
      });
      return stdout;
    } catch (e) {
      const err = e as NodeJS.ErrnoException & { stderr?: string };
      const stderr = (err.stderr || "").trim();
      const detail = (stderr || err.message || "").split("\n").slice(-4).join("\n");
      // The Python preflight (src/preflight.py) already writes a plain-language
      // diagnosis for the environment failures — wrong Python, missing/old
      // pydantic — so when it spoke, show *its* words rather than a generic
      // "backend error" that hides the one message built to help.
      const diagnosed = /NL-Miner cannot run in this environment/i.test(stderr);
      if (diagnosed) {
        new Notice(`NL-Miner: ${stderr.split("\n").filter((l) => l.startsWith("- "))[0]?.slice(2) ?? stderr}`, 12000);
      } else {
        const missing =
          err.code === "ENOENT" ||
          /not found|no such file|no module named/i.test(detail);
        const hint = missing
          ? ` — is Python at '${exe}' with interface.cli importable? (check NL-Miner settings; backend needs pydantic ≥ 2)`
          : "";
        new Notice(`NL-Miner backend error${hint}`, 10000);
      }
      console.error("[nl-miner] backend failed:", full.join(" "), detail);
      throw new Error(`nlm ${args.join(" ")} failed: ${detail}`);
    }
  }

  /** Pull the last object-looking line out of stdout. */
  private parse<T>(stdout: string): T {
    const lines = stdout.split(/\r?\n/).filter((l) => l.startsWith("{"));
    const last = lines.length > 0 ? lines[lines.length - 1] : stdout.trim();
    return JSON.parse(last) as T;
  }

  index(target: string, opts: { parse: boolean }): Promise<IndexJson> {
    const args = ["--db", this.dbPath(), "index", target, "--json"];
    // Always build the token layer: it is cheap, language-independent, and it
    // is what concept bridges and the vault audit read. `--parse` is the
    // expensive extra (CKY + arc-eager, EN-only, ~2% coverage on real prose),
    // so it stays behind the setting. Sending only `--parse` would mean
    // switching that setting off silently disabled concept queries.
    args.push("--pos");
    if (opts.parse) args.push("--parse");
    return this.run(args).then((o) => this.parse<IndexJson>(o));
  }

  bridge(doc: string): Promise<BridgeJson> {
    return this.run([
      "--db", this.dbPath(), "bridge", doc, "--json",
    ]).then((o) => this.parse<BridgeJson>(o));
  }

  audit(): Promise<AuditJson> {
    return this.run([
      "--db", this.dbPath(), "audit", "--json",
    ]).then((o) => this.parse<AuditJson>(o));
  }

  vault(opts: {
    root?: string;
    folder?: string;
    concept?: string;
  }): Promise<VaultJson> {
    const args = ["--db", this.dbPath(), "vault", "--json"];
    if (opts.root) args.push("--root", opts.root);
    if (opts.folder) args.push("--folder", opts.folder);
    if (opts.concept) args.push("--concept", opts.concept);
    return this.run(args).then((o) => this.parse<VaultJson>(o));
  }

  queryBlocks(doc: string): Promise<QueryBlocksJson> {
    return this.run(["--db", this.dbPath(), "query", doc, "--json"]).then((o) =>
      this.parse<QueryBlocksJson>(o),
    );
  }

  queryBlock(doc: string, blockId: string): Promise<QueryBlockJson> {
    return this.run([
      "--db", this.dbPath(), "query", doc, "--block", blockId, "--json",
    ]).then((o) => this.parse<QueryBlockJson>(o));
  }

  graph(): Promise<GraphJson> {
    return this.run(["--db", this.dbPath(), "graph", "--json"]).then((o) =>
      this.parse<GraphJson>(o),
    );
  }

  view(absPath: string, opts: { parse: boolean }): Promise<ViewJson> {
    const args = [absPath, "--json"];
    if (opts.parse) args.push("--parse");
    return this.run(["--db", this.dbPath(), "view", ...args]).then((o) =>
      this.parse<ViewJson>(o),
    );
  }
}
