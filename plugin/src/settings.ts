// NL-Miner settings tab. Python interpreter (with detect), db path, parse
// toggle, auto-reindex + debounce, and an advanced backend-path override.

import { execFile } from "node:child_process";
import { App, Notice, PluginSettingTab, Setting } from "obsidian";

import type NLMinerPlugin from "./main";
import type { NLMinerSettings } from "./types";
import { defaultDbPath, defaultBackendDir } from "./util";

function probe(exe: string): Promise<boolean> {
  return new Promise((resolve) => {
    execFile(exe, ["--version"], (e) => resolve(!e));
  });
}

export class NLMinerSettingTab extends PluginSettingTab {
  private plugin: NLMinerPlugin;

  constructor(app: App, plugin: NLMinerPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    const s = this.plugin.settings;
    containerEl.empty();

    new Setting(containerEl)
      .setName("Language")
      .setDesc(
        "Language for NL-Miner's own panels. Automatic follows Obsidian's UI language.",
      )
      .addDropdown((d) =>
        d
          .addOptions({ auto: "Automatic", en: "English", ko: "한국어" })
          .setValue(s.language)
          .onChange(async (v) => {
            s.language = v as NLMinerSettings["language"];
            await this.plugin.saveSettings();
            // repaint every open view in the new language, and this tab too
            this.plugin.applyLanguage();
            this.display();
          }),
      );

    new Setting(containerEl)
      .setName("Python interpreter")
      .setDesc("Path to python/python3 (needs pydantic). Default: python3.")
      .addText((t) =>
        t
          .setPlaceholder("python3")
          .setValue(s.pythonPath)
          .onChange(async (v) => {
            s.pythonPath = v.trim();
            await this.plugin.saveSettings();
          }),
      )
      .addButton((b) =>
        b.setButtonText("Detect").onClick(async () => {
          for (const cand of [s.pythonPath, "python3", "python"]) {
            if (cand && (await probe(cand))) {
              s.pythonPath = cand;
              await this.saveSettingsAndRefresh();
              new Notice(`NL-Miner: found '${cand}'`);
              this.display();
              return;
            }
          }
          new Notice("NL-Miner: no Python found on PATH", 8000);
        }),
      );

    new Setting(containerEl)
      .setName("Graph database")
      .setDesc("SQLite path. Default: <vault>/.nl-miner/nlp_graph.db")
      .addText((t) =>
        t
          .setPlaceholder(defaultDbPath(this.app))
          .setValue(s.dbPath)
          .onChange(async (v) => {
            s.dbPath = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Parse (CKY + arc-eager)")
      .setDesc("Attach phrase + dependency structure. Off = block + POS only.")
      .addToggle((t) =>
        t.setValue(s.parse).onChange(async (v) => {
          s.parse = v;
          await this.saveSettingsAndRefresh();
        }),
      );

    new Setting(containerEl)
      .setName("Auto-reindex on save")
      .setDesc("Debounced reindex of the saved note (off by default).")
      .addToggle((t) =>
        t.setValue(s.autoReindex).onChange(async (v) => {
          s.autoReindex = v;
          await this.saveSettingsAndRefresh();
          this.plugin.applyAutoReindex();
        }),
      );

    new Setting(containerEl)
      .setName("Reindex debounce (ms)")
      .addText((t) =>
        t
          .setValue(String(s.reindexDebounceMs))
          .onChange(async (v) => {
            const n = Number(v);
            if (!Number.isNaN(n) && n >= 100) {
              s.reindexDebounceMs = n;
              await this.saveSettingsAndRefresh();
              this.plugin.applyAutoReindex();
            }
          }),
      );

    new Setting(containerEl)
      .setName("Advanced: backend path")
      .setDesc(
        `Leave blank to use the bundled backend. Default: ${defaultBackendDir(this.app)}`,
      )
      .addText((t) =>
        t
          .setPlaceholder("bundled")
          .setValue(s.backendPath)
          .onChange(async (v) => {
            s.backendPath = v.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl).addButton((b) =>
      b
        .setButtonText("Index vault")
        .setCta()
        .onClick(() => this.plugin.indexVault()),
    );
  }

  /** Keep the backend's settings reference live after each change. */
  private async saveSettingsAndRefresh(): Promise<void> {
    await this.plugin.saveSettings();
    this.plugin.backend.setSettings(this.plugin.settings);
  }
}
