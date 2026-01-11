from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from scripts._bootstrap import bootstrap

bootstrap(load_env=True)

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "Missing dependencies for vision_pack.\n"
        "Install (repo venv): pip install pillow numpy opencv-python-headless\n"
        f"error={e}"
    )

try:
    from factory_common.paths import workspace_root
except Exception:  # pragma: no cover
    workspace_root = None  # type: ignore[assignment]


SCHEMA_PACK_V1 = "ytm.vision_pack.v1"
SCHEMA_CROPS_V1 = "ytm.vision_pack.crops.v1"


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    width: int
    height: int


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


def _hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def _relpath(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except Exception:
        return str(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_png_pil(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def _save_png_cv(img: "np.ndarray", path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), img)
    if not ok:
        raise RuntimeError(f"failed to write image: {path}")


def _load_pil_rgb(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")


def _image_info(path: Path) -> ImageInfo:
    with Image.open(path) as img:
        return ImageInfo(path=path, width=img.width, height=img.height)


def _make_enhanced(img_rgb: Image.Image, *, scale: int) -> Image.Image:
    if scale < 1:
        scale = 1
    w, h = img_rgb.size
    if scale != 1:
        img_rgb = img_rgb.resize((w * scale, h * scale), resample=Image.Resampling.LANCZOS)
    img_rgb = ImageOps.autocontrast(img_rgb)
    img_rgb = img_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=160, threshold=3))
    img_rgb = ImageEnhance.Contrast(img_rgb).enhance(1.10)
    return img_rgb


def _make_gray(img_rgb: Image.Image) -> Image.Image:
    return img_rgb.convert("L")


def _make_edge(gray_cv: "np.ndarray") -> "np.ndarray":
    # Slight blur helps stabilize edges on noisy UI screenshots.
    blurred = cv2.GaussianBlur(gray_cv, (3, 3), 0)
    return cv2.Canny(blurred, 80, 200)


def _make_bin_variants(gray_cv: "np.ndarray", *, fixed_threshold_pct: int) -> dict[str, "np.ndarray"]:
    fixed_threshold_pct = max(0, min(100, fixed_threshold_pct))
    thr = int(round(255 * fixed_threshold_pct / 100.0))

    # Equalize to help low-contrast UI text.
    eq = cv2.equalizeHist(gray_cv)

    _, bin_fixed = cv2.threshold(eq, thr, 255, cv2.THRESH_BINARY)
    _, bin_otsu = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bin_otsu_inv = cv2.bitwise_not(bin_otsu)
    return {"fixed": bin_fixed, "otsu": bin_otsu, "otsu_inv": bin_otsu_inv}


def _nms_boxes(boxes: list[list[int]], *, overlap: float = 0.6) -> list[list[int]]:
    # Greedy prune. overlap is relative to min(area_a, area_b) to handle nested boxes.
    out: list[list[int]] = []
    for b in sorted(boxes, key=lambda x: ((x[1] // 50) * 50, x[0], -(x[2] - x[0]) * (x[3] - x[1]))):
        keep = True
        x0, y0, x1, y1 = b
        area_b = max(1, (x1 - x0) * (y1 - y0))
        for p in out:
            xa = max(x0, p[0])
            ya = max(y0, p[1])
            xb = min(x1, p[2])
            yb = min(y1, p[3])
            inter = max(0, xb - xa) * max(0, yb - ya)
            area_p = max(1, (p[2] - p[0]) * (p[3] - p[1]))
            if inter / max(1, min(area_b, area_p)) >= overlap:
                keep = False
                break
        if keep:
            out.append(b)
    return out


def _find_text_like_regions(img_bgr: "np.ndarray") -> list[list[int]]:
    """
    UIスクショ/サムネ向けの雑なテキスト領域候補検出。
    1) グレー + Canny
    2) 横長の閉包 (close)
    3) bbox抽出 + フィルタ + NMS
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    edges = cv2.Canny(gray, 80, 200)

    # 横長テキスト行の繋がりを作る（画面サイズに応じてカーネルを調整）
    kx = max(25, int(round(w * 0.03)))
    ky = max(3, int(round(h * 0.004)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

    dil = cv2.dilate(closed, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=1)
    contours, _ = cv2.findContours(dil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[list[int]] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < max(120, int(round(w * 0.08))) or bh < max(22, int(round(h * 0.02))):
            continue
        area = bw * bh
        if area > 0.6 * (w * h):
            continue
        ar = bw / max(bh, 1)
        if ar < 1.6:
            continue

        pad = max(8, int(round(min(bw, bh) * 0.05)))
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w, x + bw + pad)
        y1 = min(h, y + bh + pad)
        boxes.append([x0, y0, x1, y1])

    return _nms_boxes(boxes, overlap=0.6)


def _draw_boxes_overlay(img_bgr: "np.ndarray", boxes: list[list[int]]) -> "np.ndarray":
    overlay = img_bgr.copy()
    for (x0, y0, x1, y1) in boxes:
        cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 255, 0), 2)
    return overlay


def _maybe_ocr(
    image_path: Path, *, lang: str, psm: int, prefer: str = "pytesseract"
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """
    Best-effort OCR. Returns (text, meta). meta is set on success/failure attempts.
    """
    if prefer == "pytesseract":
        try:
            import pytesseract  # type: ignore

            with Image.open(image_path) as img:
                text = pytesseract.image_to_string(img, lang=lang, config=f"--psm {psm}")
            return text, {"engine": "pytesseract", "lang": lang, "psm": psm}
        except ImportError:
            pass
        except Exception as e:
            return None, {"engine": "pytesseract", "lang": lang, "psm": psm, "error": str(e)}

    tesseract = shutil.which("tesseract")
    if not tesseract:
        return None, {"engine": "tesseract", "lang": lang, "psm": psm, "error": "tesseract not found"}

    try:
        proc = subprocess.run(
            [tesseract, str(image_path), "stdout", "-l", lang, "--psm", str(psm)],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None, {
                "engine": "tesseract",
                "lang": lang,
                "psm": psm,
                "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[:4000],
            }
        return proc.stdout, {"engine": "tesseract", "lang": lang, "psm": psm}
    except Exception as e:
        return None, {"engine": "tesseract", "lang": lang, "psm": psm, "error": str(e)}


def _extract_palette_kmeans(img_rgb: Image.Image, *, k: int) -> list[dict[str, Any]]:
    k = max(2, min(24, int(k)))
    small = img_rgb.resize((256, 256), resample=Image.Resampling.BILINEAR)
    arr = np.array(small, dtype=np.uint8).reshape((-1, 3))
    Z = np.float32(arr)

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _compactness, labels, centers = cv2.kmeans(
        Z,
        k,
        None,
        criteria,
        10,
        cv2.KMEANS_RANDOM_CENTERS,
    )
    centers_u8 = np.clip(centers, 0, 255).astype(np.uint8)
    labels = labels.reshape((-1,))

    counts = np.bincount(labels, minlength=k)
    total = int(counts.sum()) or 1
    order = list(np.argsort(-counts))

    palette: list[dict[str, Any]] = []
    for idx in order:
        r, g, b = (int(x) for x in centers_u8[idx].tolist())
        palette.append(
            {
                "rgb": [r, g, b],
                "hex": _hex((r, g, b)),
                "ratio": float(counts[idx] / total),
            }
        )
    return palette


def _write_grid(img_rgb: Image.Image, *, out_dir: Path, cols: int, rows: int) -> list[str]:
    cols = max(1, min(12, int(cols)))
    rows = max(1, min(12, int(rows)))
    w, h = img_rgb.size
    cell_w = max(1, w // cols)
    cell_h = max(1, h // rows)
    grid_dir = out_dir / "grid"
    grid_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            x0 = c * cell_w
            y0 = r * cell_h
            x1 = w if c == cols - 1 else (c + 1) * cell_w
            y1 = h if r == rows - 1 else (r + 1) * cell_h
            cell = img_rgb.crop((x0, y0, x1, y1))
            fn = grid_dir / f"cell_{idx:02d}.png"
            _save_png_pil(cell, fn)
            files.append(_relpath(fn, out_dir))
            idx += 1
    return files


def _default_out_dir(*, kind: str) -> Path:
    if workspace_root is None:
        return Path.cwd().resolve() / f"vision_pack_{kind}_{_now_ts()}"
    return workspace_root() / "tmp" / "vision_packs" / f"{kind}_{_now_ts()}"


def _emit_prompt_templates(out_dir: Path, *, kind: str) -> None:
    if kind == "screenshot":
        prompt = (
            "目的: 画面内の文言を正確に転記。\n"
            "- 推測禁止。読めない箇所は [??]\n"
            "- 画面の改行/段組/箇条書きは維持\n"
            "- ボタン/ラベル/見出しは階層が分かるように書く\n"
            "- 最後に [??] を解消するために必要な追加切り出し指示を1行で出す（bboxを0-1正規化で）\n"
        )
    else:
        prompt = (
            "目的: 画像（サムネ）を“量産可能な設計図”に落とす。\n"
            "出力:\n"
            "1) レイアウト仕様（0-1正規化bbox: 文字ブロック/主役/余白）\n"
            "2) 色仕様（palette.jsonのhexを背景/主役/アクセント/文字に割当）\n"
            "3) エフェクト仕様（影/縁取り/帯/粒子/質感）\n"
            "4) 画像生成用プロンプト（再現性重視、ネガティブ含む）\n"
            "推測禁止: 読めない文字は [??]。追加で必要な切り出し案も出す。\n"
        )
    _write_text(out_dir / "prompt_template.txt", prompt)


def build_pack(
    *,
    kind: str,
    input_path: Path,
    out_dir: Path,
    scale: int,
    fixed_threshold_pct: int,
    max_crops: int,
    do_crops: bool,
    do_ocr: bool,
    ocr_lang: str,
    ocr_psm: int,
    palette_k: Optional[int],
    grid: Optional[tuple[int, int]],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    img_rgb = _load_pil_rgb(input_path)
    raw_path = out_dir / "raw.png"
    _save_png_pil(img_rgb, raw_path)
    raw_info = _image_info(raw_path)

    enh = _make_enhanced(img_rgb, scale=scale)
    enh_path = out_dir / f"enh{scale}x.png" if scale != 2 else out_dir / "enh2x.png"
    _save_png_pil(enh, enh_path)
    enh_info = _image_info(enh_path)

    gray = _make_gray(img_rgb)
    gray_path = out_dir / "gray.png"
    _save_png_pil(gray, gray_path)

    # Bin/edge are made from scaled gray for OCR-friendliness.
    gray_cv = np.array(gray, dtype=np.uint8)
    if scale != 1:
        gray_cv = cv2.resize(gray_cv, (raw_info.width * scale, raw_info.height * scale), interpolation=cv2.INTER_CUBIC)

    edge = _make_edge(gray_cv)
    edge_path = out_dir / "edge.png"
    _save_png_cv(edge, edge_path)

    bins = _make_bin_variants(gray_cv, fixed_threshold_pct=fixed_threshold_pct)
    bin_paths: dict[str, str] = {}
    for key, mat in bins.items():
        name = f"bin{scale}x_{key}.png" if scale != 2 else f"bin2x_{key}.png"
        p = out_dir / name
        _save_png_cv(mat, p)
        bin_paths[key] = _relpath(p, out_dir)

    crops_index: Optional[dict[str, Any]] = None
    ocr_index: Optional[dict[str, Any]] = None

    crop_files: list[str] = []
    if do_crops:
        img_bgr = cv2.imread(str(enh_path), cv2.IMREAD_COLOR)
        if img_bgr is None:
            raise RuntimeError(f"failed to read image (cv2): {enh_path}")
        boxes = _find_text_like_regions(img_bgr)
        boxes = boxes[: max(0, int(max_crops))]

        crops_dir = out_dir / "crops"
        crops_dir.mkdir(parents=True, exist_ok=True)
        crops: list[dict[str, Any]] = []
        for i, (x0, y0, x1, y1) in enumerate(boxes, start=1):
            crop = img_bgr[y0:y1, x0:x1]
            fn = crops_dir / f"crop_{i:02d}.png"
            _save_png_cv(crop, fn)
            rel = _relpath(fn, out_dir)
            crop_files.append(rel)

            # Map to raw coordinate space (best-effort).
            sx = enh_info.width / max(1, raw_info.width)
            sy = enh_info.height / max(1, raw_info.height)
            rx0 = int(round(x0 / sx))
            ry0 = int(round(y0 / sy))
            rx1 = int(round(x1 / sx))
            ry1 = int(round(y1 / sy))
            crops.append(
                {
                    "file": rel,
                    "bbox_enh": [int(x0), int(y0), int(x1), int(y1)],
                    "bbox_raw": [rx0, ry0, rx1, ry1],
                    "bbox_raw_norm": [
                        rx0 / max(1, raw_info.width),
                        ry0 / max(1, raw_info.height),
                        rx1 / max(1, raw_info.width),
                        ry1 / max(1, raw_info.height),
                    ],
                }
            )

        overlay = _draw_boxes_overlay(img_bgr, boxes)
        overlay_path = out_dir / "crops_overlay.png"
        _save_png_cv(overlay, overlay_path)

        crops_index = {
            "schema": SCHEMA_CROPS_V1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": _relpath(input_path, Path.cwd().resolve()),
            "raw": _relpath(raw_path, out_dir),
            "enhanced": _relpath(enh_path, out_dir),
            "overlay": _relpath(overlay_path, out_dir),
            "scale": scale,
            "crops": crops,
        }
        _write_json(out_dir / "crops.json", crops_index)

    if do_ocr:
        items: list[dict[str, Any]] = []
        for rel in crop_files[:]:
            p = out_dir / rel
            text, meta = _maybe_ocr(p, lang=ocr_lang, psm=ocr_psm)
            rec: dict[str, Any] = {"file": rel, "ok": bool(text and text.strip())}
            if meta:
                rec["meta"] = meta
            if text is not None:
                rec["text"] = text
            items.append(rec)

        # Also run OCR on binarized full images (often helps recover UI labels).
        for key, rel in bin_paths.items():
            p = out_dir / rel
            text, meta = _maybe_ocr(p, lang=ocr_lang, psm=ocr_psm)
            rec = {"file": rel, "tag": f"bin:{key}", "ok": bool(text and text.strip())}
            if meta:
                rec["meta"] = meta
            if text is not None:
                rec["text"] = text
            items.append(rec)

        ocr_index = {
            "schema": "ytm.vision_pack.ocr.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lang": ocr_lang,
            "psm": ocr_psm,
            "items": items,
        }
        _write_json(out_dir / "ocr.json", ocr_index)

        # Convenience: a flattened text view.
        lines: list[str] = []
        for it in items:
            text = it.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            lines.append(f"# {it.get('file')}")
            lines.append(text.strip())
            lines.append("")
        _write_text(out_dir / "ocr_all.txt", "\n".join(lines).rstrip() + "\n")

    palette_path: Optional[Path] = None
    grid_files: Optional[list[str]] = None
    if palette_k is not None:
        palette = _extract_palette_kmeans(img_rgb, k=palette_k)
        palette_path = out_dir / "palette.json"
        _write_json(
            palette_path,
            {
                "schema": "ytm.vision_pack.palette.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": _relpath(raw_path, out_dir),
                "k": int(palette_k),
                "palette": palette,
            },
        )

    if grid is not None:
        cols, rows = grid
        grid_files = _write_grid(img_rgb, out_dir=out_dir, cols=cols, rows=rows)

    # Emit recommended image list for LLM ingestion (relative paths).
    llm_images: list[str] = [
        _relpath(raw_path, out_dir),
        _relpath(enh_path, out_dir),
        _relpath(edge_path, out_dir),
    ]
    llm_images.extend([bin_paths[k] for k in ("otsu", "otsu_inv", "fixed") if k in bin_paths])
    llm_images.extend(crop_files)

    _write_text(out_dir / "images_for_llm.txt", ",".join(llm_images) + "\n")
    _emit_prompt_templates(out_dir, kind=kind)

    pack: dict[str, Any] = {
        "schema": SCHEMA_PACK_V1,
        "kind": kind,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path.resolve()),
        "out_dir": str(out_dir.resolve()),
        "artifacts": {
            "raw": _relpath(raw_path, out_dir),
            "enhanced": _relpath(enh_path, out_dir),
            "gray": _relpath(gray_path, out_dir),
            "edge": _relpath(edge_path, out_dir),
            "bin": bin_paths,
            "crops_json": "crops.json" if crops_index else None,
            "ocr_json": "ocr.json" if ocr_index else None,
            "palette_json": _relpath(palette_path, out_dir) if palette_path else None,
            "grid": grid_files,
            "images_for_llm": "images_for_llm.txt",
            "prompt_template": "prompt_template.txt",
        },
        "image": {
            "raw": {"w": raw_info.width, "h": raw_info.height},
            "enhanced": {"w": enh_info.width, "h": enh_info.height, "scale": scale},
        },
        "notes": {
            "cleanup": "This output is intended for workspaces/tmp. Delete the whole pack dir when done.",
            "ocr": "OCR is best-effort. If empty/garbled, try installing Tesseract + language data or tweak binarization/scale.",
        },
    }
    _write_json(out_dir / "pack.json", pack)
    return out_dir / "pack.json"


def _parse_grid(s: str) -> tuple[int, int]:
    s = s.strip().lower()
    if "x" not in s:
        raise ValueError("grid must be like 4x3")
    a, b = s.split("x", 1)
    return int(a), int(b)


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a 'Vision Pack' to improve screenshot/thumbnail understanding.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("image", type=str, help="input image path (png/jpg/webp/...)")
        sp.add_argument("--out", type=str, default=None, help="output dir (default: workspaces/tmp/vision_packs/...)")
        sp.add_argument("--scale", type=int, default=2, help="upscale factor for enh/bin/edge (default: 2)")
        sp.add_argument("--bin-threshold", type=int, default=60, help="fixed threshold percent (0-100), plus Otsu variants")
        sp.add_argument("--max-crops", type=int, default=24, help="max auto-crops (default: 24)")
        sp.add_argument("--no-crops", action="store_true", help="disable auto-cropping")
        sp.add_argument("--ocr", action="store_true", help="best-effort OCR (pytesseract or tesseract)")
        sp.add_argument("--ocr-lang", type=str, default="jpn+eng", help="OCR language (default: jpn+eng)")
        sp.add_argument("--ocr-psm", type=int, default=6, help="Tesseract PSM (default: 6)")

    sp_sc = sub.add_parser("screenshot", help="pack for UI screenshots (text extraction)")
    add_common(sp_sc)

    sp_th = sub.add_parser("thumbnail", help="pack for thumbnails (layout/palette analysis)")
    add_common(sp_th)
    sp_th.add_argument("--palette-k", type=int, default=10, help="k-means palette size (default: 10)")
    sp_th.add_argument("--grid", type=str, default="4x3", help="grid split like 4x3 (default: 4x3)")

    args = ap.parse_args()
    input_path = Path(args.image).expanduser()
    if not input_path.exists():
        raise SystemExit(f"image not found: {input_path}")

    kind = "screenshot" if args.cmd == "screenshot" else "thumbnail"
    out_dir = Path(args.out).expanduser() if args.out else _default_out_dir(kind=kind)

    palette_k: Optional[int] = None
    grid: Optional[tuple[int, int]] = None
    if kind == "thumbnail":
        palette_k = int(args.palette_k)
        try:
            grid = _parse_grid(str(args.grid))
        except Exception as e:
            raise SystemExit(f"invalid --grid: {args.grid} ({e})")

    pack_path = build_pack(
        kind=kind,
        input_path=input_path,
        out_dir=out_dir,
        scale=int(args.scale),
        fixed_threshold_pct=int(args.bin_threshold),
        max_crops=int(args.max_crops),
        do_crops=not bool(args.no_crops),
        do_ocr=bool(args.ocr),
        ocr_lang=str(args.ocr_lang),
        ocr_psm=int(args.ocr_psm),
        palette_k=palette_k,
        grid=grid,
    )

    print(f"[vision_pack] wrote: {pack_path}")
    print(f"[vision_pack] out_dir: {out_dir}")
    print(f"[vision_pack] images_for_llm: {out_dir / 'images_for_llm.txt'}")
    print(f"[vision_pack] prompt_template: {out_dir / 'prompt_template.txt'}")
    if (out_dir / "crops.json").exists():
        print(f"[vision_pack] crops: {out_dir / 'crops.json'} (overlay: {out_dir / 'crops_overlay.png'})")
    if (out_dir / "ocr.json").exists():
        print(f"[vision_pack] ocr: {out_dir / 'ocr.json'} (flat: {out_dir / 'ocr_all.txt'})")


if __name__ == "__main__":
    main()

