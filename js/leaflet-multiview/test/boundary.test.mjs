// The package boundary: every import specifier under src/ is relative and
// resolves to a file inside js/leaflet-multiview/ (no bare specifiers at all),
// and only core/multiview.js may fall back to the page's global L.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "src");

function sources(dir) {
  return fs.readdirSync(dir, {withFileTypes: true}).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sources(full);
    return entry.name.endsWith(".js") ? [full] : [];
  });
}

const PATTERNS = [
  /\bfrom\s*["']([^"']+)["']/g, // import x from "…", export {x} from "…"
  /\bimport\s*["']([^"']+)["']/g, // import "…"
  /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g, // import("…")
];

function specifiers(file) {
  const text = fs.readFileSync(file, "utf8");
  return PATTERNS.flatMap(re => [...text.matchAll(re)].map(m => m[1]));
}

test("every import under src/ is relative and stays inside the package", () => {
  const files = sources(SRC);
  assert.ok(files.length > 0, "no sources found");
  let count = 0;
  for (const file of files) {
    for (const spec of specifiers(file)) {
      count++;
      const where = `${path.relative(ROOT, file)} imports "${spec}"`;
      assert.ok(spec.startsWith("./") || spec.startsWith("../"), `${where}: bare specifiers are not allowed`);
      const target = path.resolve(path.dirname(file), spec);
      assert.ok(target.startsWith(ROOT + path.sep), `${where}: it leaves js/leaflet-multiview/`);
      assert.ok(fs.existsSync(target), `${where}: no such file`);
    }
  }
  assert.ok(count > 0, "no import specifiers found: the pattern is broken");
});

test("only core/multiview.js reads the page's global L", () => {
  for (const file of sources(SRC)) {
    const rel = path.relative(SRC, file).split(path.sep).join("/");
    if (rel === "core/multiview.js") continue;
    const text = fs.readFileSync(file, "utf8");
    assert.doesNotMatch(text, /\b(globalThis|window)\.L\b/, `${rel} reads the global L: use mv.L`);
  }
});
