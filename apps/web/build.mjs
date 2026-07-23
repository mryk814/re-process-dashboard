import { build } from "esbuild";
import { copyFile, mkdir, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const outdir = resolve("dist");
await rm(outdir, { recursive: true, force: true });
await mkdir(resolve(outdir, "assets"), { recursive: true });

await build({
  entryPoints: [resolve("src/main.tsx")],
  bundle: true,
  format: "esm",
  minify: true,
  sourcemap: false,
  target: ["chrome120"],
  outdir: resolve(outdir, "assets"),
  entryNames: "app",
  assetNames: "[name]-[hash]",
  loader: { ".png": "file", ".svg": "file" },
});

await copyFile(resolve("public/app-icon.png"), resolve(outdir, "app-icon.png"));

await writeFile(resolve(outdir, "index.html"), `<!doctype html>
<html lang="ja">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="theme-color" content="#0F1B2D" />
    <link rel="icon" type="image/png" href="./app-icon.png" />
    <link rel="apple-touch-icon" href="./app-icon.png" />
    <title>Material Decision Workbench</title>
    <link rel="stylesheet" href="./assets/app.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="./assets/app.js"></script>
  </body>
</html>
`, "utf8");
