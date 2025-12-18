#!/usr/bin/env python3
"""Hit OpenRouter thumbnail caption endpoint to ensure keys/models are fully functional."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List

import requests

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()
DEFAULT_MODEL = "qwen/qwen2.5-vl-32b-instruct:free"
FALLBACK_MODELS = [
    "qwen/qwen3-vl-8b-instruct:free",
    "qwen/qwen3-vl-8b-instruct",
    "qwen/qwen3-vl-30b-a3b-instruct",
    "qwen/qwen2.5-vl-32b-instruct",
    "qwen/qwen-2.5-vl-7b-instruct",
]
SAMPLE_IMAGE_DATA = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMA"
    "ASsJTYQAAAAASUVORK5CYII="
)
PROMPT = (
    "以下の画像の内容を40文字前後の日本語で説明してください。"
    "人物/背景/文字があれば触れてください。"
)


def load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def build_model_chain(preferred: str | None) -> List[str]:
    chain: List[str] = []
    if preferred:
        chain.append(preferred)
    if DEFAULT_MODEL not in chain:
        chain.append(DEFAULT_MODEL)
    for candidate in FALLBACK_MODELS:
        if candidate not in chain:
            chain.append(candidate)
    return chain


def caption_with_model(model: str, api_key: str, *, timeout: float, headers: dict[str, str]) -> str:
    data_url = f"data:image/png;base64,{SAMPLE_IMAGE_DATA}"
    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    if response.status_code == 404 and "No endpoints found" in response.text:
        raise ValueError(f"invalid-model:{model}")
    if response.status_code == 400 and "not a valid model" in response.text.lower():
        raise ValueError(f"invalid-model:{model}")
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - malformed payload
        raise RuntimeError(f"unexpected response: {data}") from exc
    if isinstance(content, list):
        text = " ".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    else:
        text = str(content).strip()
    if not text:
        raise RuntimeError("response contained no caption text")
    return text


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenRouter caption probe")
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout seconds")
    args = parser.parse_args(list(argv) if argv is not None else None)

    load_env()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    preferred_model = os.getenv("THUMBNAIL_CAPTION_MODEL")
    models = build_model_chain(preferred_model)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_REFERRER", "https://youtube-master.local/healthcheck"),
        "X-Title": os.getenv("OPENROUTER_TITLE", "YouTube Master Healthcheck"),
    }

    invalid_notes: List[str] = []
    for model in models:
        try:
            text = caption_with_model(model, api_key, timeout=args.timeout, headers=headers)
        except ValueError as exc:
            if str(exc).startswith("invalid-model"):
                invalid_notes.append(model)
                continue
            print(f"{model}: {exc}", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(f"{model}: {exc}", file=sys.stderr)
            return 1
        except requests.RequestException as exc:
            print(f"{model}: request failed ({exc})", file=sys.stderr)
            return 1
        print(f"Caption probe OK via {model}: {text[:60]}")
        return 0

    print(f"All caption models rejected: {invalid_notes}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover - manual execution
    raise SystemExit(main())
