/**
 * Copies the vendored webview runtime assets (markdown renderer + vis.js
 * graph library) from node_modules into media/vendor/, so webviews never
 * touch a CDN and the extension works fully offline.
 *
 * Run automatically as part of `npm run package`.
 */
"use strict";

const fs = require("fs");
const path = require("path");

/** candidate paths inside each package, first existing one wins */
const ASSETS = [
  {
    name: "marked",
    pkg: "marked",
    candidates: ["marked.min.js", "lib/marked.umd.js", "lib/marked.cjs"],
    out: "marked.min.js",
  },
  {
    // Standalone browser build: bundles its own DataSet, exposing the
    // combined `vis` global (vis.DataSet + vis.Network).
    name: "vis-network",
    pkg: "vis-network",
    candidates: ["dist/vis-network.min.js", "dist/vis-network.umd.js"],
    out: "vis-network.min.js",
  },
];

const extensionRoot = path.resolve(__dirname, "..");
const vendorDir = path.join(extensionRoot, "media", "vendor");
fs.mkdirSync(vendorDir, { recursive: true });

let failed = false;
for (const asset of ASSETS) {
  const pkgRoot = path.join(extensionRoot, "node_modules", asset.pkg);
  const source = asset.candidates
    .map((candidate) => path.join(pkgRoot, candidate))
    .find((candidate) => fs.existsSync(candidate));

  if (!source) {
    console.error(
      `error: could not find ${asset.name} asset (tried: ${asset.candidates.join(", ")})`
    );
    failed = true;
    continue;
  }

  const target = path.join(vendorDir, asset.out);
  fs.copyFileSync(source, target);
  console.log(`vendored ${asset.name}: ${path.relative(extensionRoot, source)} -> media/vendor/${asset.out}`);
}

process.exit(failed ? 1 : 0);
