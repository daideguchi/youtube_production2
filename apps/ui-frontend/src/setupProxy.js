const path = require("path");
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");

module.exports = function configureProxy(app) {
  // API backend
  app.use(
    "/api",
    createProxyMiddleware({
      target: "http://localhost:8000",
      changeOrigin: true,
    })
  );

  // Non-API asset routes served by the backend (avoid CORS in dev).
  // - /thumbnails/assets/* (workspaces/thumbnails/assets)
  // - /thumbnails/library/* (thumbnail library)
  app.use(
    "/thumbnails/assets",
    createProxyMiddleware({
      target: "http://localhost:8000",
      changeOrigin: true,
    })
  );
  app.use(
    "/thumbnails/library",
    createProxyMiddleware({
      target: "http://localhost:8000",
      changeOrigin: true,
    })
  );

  // Serve Remotion-related artifacts for UI preview.
  // __dirname = apps/ui-frontend/src. Repo root = ../../.. from here.
  const repoRoot = path.resolve(__dirname, "../../..");
  const videoInputDir = path.join(repoRoot, "workspaces", "video", "input");
  const remotionOutDir = path.join(repoRoot, "apps", "remotion", "out");
  const remotionPublicDir = path.join(repoRoot, "apps", "remotion", "public");

  // Run inputs: belt_config.json, image_cues.json, images, wav
  app.use("/remotion/input", express.static(videoInputDir));
  // Optional rendered outputs (mp4)
  app.use("/remotion/out", express.static(remotionOutDir));
  // Generated public assets (_auto/_bgm) and tracked assets (asset/*)
  app.use("/remotion/public", express.static(remotionPublicDir));

};
