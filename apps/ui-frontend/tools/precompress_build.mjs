import fs from "fs";
import path from "path";
import zlib from "zlib";
import { promisify } from "util";
import { fileURLToPath } from "url";

const gzip = promisify(zlib.gzip);

function findRepoRoot(startDir) {
  let cur = startDir;
  while (true) {
    const candidate = path.join(cur, "pyproject.toml");
    if (fs.existsSync(candidate)) return cur;
    const parent = path.dirname(cur);
    if (parent === cur) return startDir;
    cur = parent;
  }
}

async function walk(dir, onFile) {
  const entries = await fs.promises.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      await walk(full, onFile);
      continue;
    }
    if (entry.isFile()) {
      await onFile(full);
    }
  }
}

function shouldGzip(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return (
    ext === ".html" ||
    ext === ".js" ||
    ext === ".css" ||
    ext === ".json" ||
    ext === ".svg" ||
    ext === ".txt" ||
    ext === ".ico"
  );
}

async function main() {
  const selfDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = findRepoRoot(selfDir);
  const buildDir = path.join(repoRoot, "apps", "ui-frontend", "build");

  const indexPath = path.join(buildDir, "index.html");
  if (!fs.existsSync(indexPath)) {
    console.error(`[precompress_build] missing build output: ${indexPath}`);
    console.error(`[precompress_build] run: (cd apps/ui-frontend && npm run build:acer)`);
    process.exitCode = 2;
    return;
  }

  let written = 0;
  let totalBytes = 0;

  await walk(buildDir, async (filePath) => {
    if (filePath.endsWith(".gz")) return;
    if (!shouldGzip(filePath)) return;

    const raw = await fs.promises.readFile(filePath);
    const gz = await gzip(raw, { level: 9 });
    await fs.promises.writeFile(`${filePath}.gz`, gz);
    written += 1;
    totalBytes += gz.length;
  });

  console.log(`[precompress_build] wrote ${written} .gz files (${Math.round(totalBytes / 1024)} KiB)`);
}

main().catch((err) => {
  console.error(`[precompress_build] fatal: ${err?.stack || err}`);
  process.exitCode = 1;
});

