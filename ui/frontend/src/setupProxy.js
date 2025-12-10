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

  // Serve Remotion assets (belt_config.json, image_cues.json, images, audio) directly from repo root.
  // This keeps CapCutと分離した remotion/input/<id> を UI プレビューで読めるようにする。
  // __dirname = ui/frontend/src. Repo root = ../../.. from here.
  const remotionDir = path.resolve(__dirname, "../../../remotion");
  app.use("/remotion", express.static(remotionDir));

};
