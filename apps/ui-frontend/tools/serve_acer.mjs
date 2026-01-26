import fs from "fs";
import http from "http";
import path from "path";
import { fileURLToPath } from "url";

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

function mimeTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  switch (ext) {
    case ".html":
      return "text/html; charset=utf-8";
    case ".js":
      return "text/javascript; charset=utf-8";
    case ".css":
      return "text/css; charset=utf-8";
    case ".json":
      return "application/json; charset=utf-8";
    case ".svg":
      return "image/svg+xml";
    case ".png":
      return "image/png";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".gif":
      return "image/gif";
    case ".webp":
      return "image/webp";
    case ".ico":
      return "image/x-icon";
    case ".txt":
      return "text/plain; charset=utf-8";
    case ".map":
      return "application/json; charset=utf-8";
    case ".mp4":
      return "video/mp4";
    case ".m4a":
      return "audio/mp4";
    case ".mp3":
      return "audio/mpeg";
    case ".wav":
      return "audio/wav";
    case ".ttf":
      return "font/ttf";
    case ".otf":
      return "font/otf";
    case ".woff":
      return "font/woff";
    case ".woff2":
      return "font/woff2";
    default:
      return "application/octet-stream";
  }
}

function safeResolve(rootDir, unsafeRelativePath) {
  const abs = path.resolve(rootDir, unsafeRelativePath);
  const rel = path.relative(rootDir, abs);
  if (rel.startsWith("..") || path.isAbsolute(rel)) return null;
  return abs;
}

function parseRangeHeader(rangeHeader, size) {
  const m = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader || "");
  if (!m) return null;

  const rawStart = m[1];
  const rawEnd = m[2];

  let start;
  let end;

  if (rawStart === "" && rawEnd === "") return null;
  if (rawStart === "") {
    const suffix = Number(rawEnd);
    if (!Number.isFinite(suffix) || suffix <= 0) return null;
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(rawStart);
    if (!Number.isFinite(start) || start < 0) return null;
    if (rawEnd === "") {
      end = size - 1;
    } else {
      end = Number(rawEnd);
      if (!Number.isFinite(end) || end < start) return null;
    }
  }

  if (start >= size) return null;
  end = Math.min(end, size - 1);
  return { start, end };
}

async function statOrNull(filePath) {
  try {
    return await fs.promises.stat(filePath);
  } catch {
    return null;
  }
}

async function serveFile(req, res, filePath, opts) {
  const stat = await statOrNull(filePath);
  if (!stat || !stat.isFile()) {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    res.end("not found");
    return;
  }

  const method = (req.method || "GET").toUpperCase();
  const mime = opts?.mimeType || mimeTypeFor(filePath);
  const cacheControl = opts?.cacheControl || "no-cache";
  const rangeHeader = req.headers.range;

  const headers = {
    "content-type": mime,
    "cache-control": cacheControl,
    "accept-ranges": "bytes",
  };

  // Serve precompressed assets when possible (skip when Range is used).
  const acceptEncoding = String(req.headers["accept-encoding"] || "");
  const canGzip = opts?.allowGzip && !rangeHeader && acceptEncoding.includes("gzip");
  let effectivePath = filePath;
  let encoding = null;
  if (canGzip) {
    const gzPath = `${filePath}.gz`;
    const gzStat = await statOrNull(gzPath);
    if (gzStat && gzStat.isFile()) {
      effectivePath = gzPath;
      encoding = "gzip";
    }
  }

  const effectiveStat = effectivePath === filePath ? stat : await statOrNull(effectivePath);
  if (!effectiveStat || !effectiveStat.isFile()) {
    res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
    res.end("failed to stat file");
    return;
  }

  if (encoding) {
    headers["content-encoding"] = encoding;
    headers["vary"] = "Accept-Encoding";
  }

  if (rangeHeader) {
    const range = parseRangeHeader(String(rangeHeader), effectiveStat.size);
    if (!range) {
      res.writeHead(416, { "content-range": `bytes */${effectiveStat.size}` });
      res.end();
      return;
    }

    const { start, end } = range;
    headers["content-range"] = `bytes ${start}-${end}/${effectiveStat.size}`;
    headers["content-length"] = String(end - start + 1);
    res.writeHead(206, headers);
    if (method === "HEAD") {
      res.end();
      return;
    }
    fs.createReadStream(effectivePath, { start, end }).pipe(res);
    return;
  }

  headers["content-length"] = String(effectiveStat.size);
  res.writeHead(200, headers);
  if (method === "HEAD") {
    res.end();
    return;
  }
  fs.createReadStream(effectivePath).pipe(res);
}

async function main() {
  const selfDir = path.dirname(fileURLToPath(import.meta.url));
  const repoRoot = findRepoRoot(selfDir);

  const buildDir = path.join(repoRoot, "apps", "ui-frontend", "build");
  const indexPath = path.join(buildDir, "index.html");
  if (!fs.existsSync(indexPath)) {
    console.error(`[serve_acer] missing build output: ${indexPath}`);
    console.error(`[serve_acer] run: (cd apps/ui-frontend && npm run build:acer)`);
    process.exit(2);
  }

  const remotionInputDir = path.join(repoRoot, "workspaces", "video", "input");
  const remotionOutDir = path.join(repoRoot, "apps", "remotion", "out");
  const remotionPublicDir = path.join(repoRoot, "apps", "remotion", "public");

  const port = Number(process.env.PORT || 3000);
  const host = String(process.env.HOST || "127.0.0.1");

  const server = http.createServer(async (req, res) => {
    try {
      const method = (req.method || "GET").toUpperCase();
      if (method !== "GET" && method !== "HEAD") {
        res.writeHead(405, { "content-type": "text/plain; charset=utf-8" });
        res.end("method not allowed");
        return;
      }

      const url = new URL(req.url || "/", "http://localhost");
      const pathname = url.pathname || "/";

      // Remotion static directories (matches CRA dev setupProxy.js).
      if (pathname.startsWith("/remotion/input/")) {
        const rel = pathname.slice("/remotion/input/".length);
        const abs = safeResolve(remotionInputDir, rel);
        if (!abs) {
          res.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
          res.end("bad path");
          return;
        }
        await serveFile(req, res, abs, { cacheControl: "no-cache", allowGzip: false });
        return;
      }
      if (pathname.startsWith("/remotion/out/")) {
        const rel = pathname.slice("/remotion/out/".length);
        const abs = safeResolve(remotionOutDir, rel);
        if (!abs) {
          res.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
          res.end("bad path");
          return;
        }
        await serveFile(req, res, abs, { cacheControl: "no-cache", allowGzip: false });
        return;
      }
      if (pathname.startsWith("/remotion/public/")) {
        const rel = pathname.slice("/remotion/public/".length);
        const abs = safeResolve(remotionPublicDir, rel);
        if (!abs) {
          res.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
          res.end("bad path");
          return;
        }
        await serveFile(req, res, abs, { cacheControl: "no-cache", allowGzip: false });
        return;
      }

      // UI static build under /ui (SPA fallback to index.html).
      if (pathname === "/ui" || pathname === "/ui/" || pathname.startsWith("/ui/")) {
        const rawRel = pathname.slice("/ui".length) || "/";
        const rel = rawRel === "/" ? "index.html" : rawRel.replace(/^\/+/, "");
        const abs = safeResolve(buildDir, rel);
        if (!abs) {
          res.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
          res.end("bad path");
          return;
        }

        const stat = await statOrNull(abs);
        if (stat && stat.isFile()) {
          const cacheControl = rel.startsWith("static/") ? "public, max-age=31536000, immutable" : "no-cache";
          await serveFile(req, res, abs, { cacheControl, allowGzip: rel.startsWith("static/") || rel === "index.html" });
          return;
        }

        await serveFile(req, res, indexPath, { cacheControl: "no-cache", allowGzip: true, mimeType: "text/html; charset=utf-8" });
        return;
      }

      res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      res.end("not found");
    } catch (err) {
      res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
      res.end(`internal error: ${err?.message || String(err)}`);
    }
  });

  server.listen(port, host, () => {
    console.log(`[serve_acer] listening on http://${host}:${port}`);
    console.log(`[serve_acer] ui build: ${buildDir}`);
  });
}

main().catch((err) => {
  console.error(`[serve_acer] fatal: ${err?.stack || err}`);
  process.exit(1);
});
