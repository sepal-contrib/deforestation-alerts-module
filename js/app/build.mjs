// js/app/build.mjs
// node build.mjs [outdir]: the committed bundle gui/widget/static/multiview.{js,css}
// (default), or a scratch build into ``outdir`` (tests/test_bundle_drift.py
// builds one and compares it with the committed files).
import {build} from "esbuild";
import path from "node:path";
import {fileURLToPath} from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const outdir = path.resolve(process.argv[2] || path.join(here, "..", "..", "gui", "widget", "static"));

await build({
  entryPoints: {multiview: path.join(here, "index.js")},
  outdir,
  bundle: true,
  format: "esm",
  minify: true,
  target: "es2020",
  // Every free ``L`` in the bundle -- Geoman's IIFE reads one -- becomes the
  // shim's private, mutable copy of our Leaflet (leaflet_shim.js).
  inject: [path.join(here, "leaflet_shim.js")],
  loader: {".png": "dataurl", ".svg": "dataurl"},
  legalComments: "none",
  logLevel: "warning",
});
