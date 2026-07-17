// esbuild config for the NL-Miner Obsidian plugin.
// Bundles src/main.ts -> main.js, vendoring d3-force, with `obsidian`/`electron`
// left external (Obsidian provides them at runtime). platform=node so Node
// builtins (child_process, fs, path) the backend spawns via are left external —
// they resolve to Electron's Node at runtime, not bundled. esbuild transpiles
// TS without type-checking, so no obsidian.d.ts is required to build (though it
// helps the IDE); modelled on the official Obsidian sample plugin build.
import esbuild from "esbuild";
import process from "node:process";

const prod = process.argv[2] === "production";

const ctx = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  platform: "node",
  external: ["obsidian", "electron"],
  format: "cjs",
  target: "es2018",
  logLevel: "info",
  sourcemap: prod ? false : "inline",
  treeShaking: true,
  outfile: "main.js",
});

if (prod) {
  await ctx.rebuild();
  process.exit(0);
} else {
  await ctx.watch();
}
