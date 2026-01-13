"""Background job manager for video_pipeline (React UI backend)."""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from video_pipeline.src.config.template_registry import resolve_template_path, is_registered_template

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_DEFAULT_MIN_IMAGE_BYTES = 60_000


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class CommandSpec:
    """Command metadata required to execute a job."""

    command: List[str]
    cwd: Path
    env: Dict[str, str]
    summary: str


@dataclass
class JobRecord:
    """Internal representation of a job."""

    id: str
    project_id: str
    action: str
    options: Dict[str, Any]
    note: Optional[str]
    status: JobStatus
    created_at: datetime
    command: List[str] = field(default_factory=list)
    cwd: Optional[Path] = None
    env: Dict[str, str] = field(default_factory=dict)
    summary: Optional[str] = None
    log_path: Optional[Path] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    log_excerpt: List[str] = field(default_factory=list)


class JobManager:
    """Simple background job executor with in-memory registry."""

    def __init__(
        self,
        *,
        project_root: Path,
        output_root: Path,
        tools_root: Path,
        log_root: Path,
        scripts_root: Path,
        project_loader: Callable[[str], Any],
        python_executable: str,
    ) -> None:
        self._project_root = project_root
        self._output_root = output_root
        self._tools_root = tools_root
        self._log_root = log_root
        self._scripts_root = scripts_root
        self._project_loader = project_loader
        self._python = python_executable

        self._queue: "queue.Queue[str]" = queue.Queue()
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

        self._log_root.mkdir(parents=True, exist_ok=True)

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create_job(self, project_id: str, action: str, options: Dict[str, Any], note: Optional[str]) -> JobRecord:
        job_id = uuid.uuid4().hex
        command_spec = self._build_command(project_id, action, options)

        log_path = self._log_root / f"{job_id}.log"
        record = JobRecord(
            id=job_id,
            project_id=project_id,
            action=action,
            options=options,
            note=note,
            status=JobStatus.QUEUED,
            created_at=datetime.utcnow(),
            command=command_spec.command,
            cwd=command_spec.cwd,
            env=command_spec.env,
            summary=command_spec.summary,
            log_path=log_path,
        )

        with self._lock:
            self._jobs[job_id] = record

        self._queue.put(job_id)
        return record

    def list_jobs(self, *, project_id: Optional[str] = None, limit: Optional[int] = None) -> List[JobRecord]:
        with self._lock:
            records: Iterable[JobRecord] = self._jobs.values()
            if project_id:
                records = [job for job in records if job.project_id == project_id]
            ordered = sorted(records, key=lambda job: job.created_at, reverse=True)
            if limit is not None:
                return ordered[: limit]
            return ordered

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._execute_job(job_id)
            except Exception as exc:  # pragma: no cover - unexpected safeguard
                with self._lock:
                    record = self._jobs.get(job_id)
                    if record:
                        record.status = JobStatus.FAILED
                        record.finished_at = datetime.utcnow()
                        record.error = f"Unexpected executor error: {exc}"
                continue
            finally:
                self._queue.task_done()

    def _execute_job(self, job_id: str) -> None:
        record = self.get_job(job_id)
        if not record:
            return

        with self._lock:
            record.status = JobStatus.RUNNING
            record.started_at = datetime.utcnow()

        log_handle = record.log_path.open("w", encoding="utf-8") if record.log_path else None
        try:
            process = subprocess.Popen(
                record.command,
                cwd=str(record.cwd) if record.cwd else None,
                env=record.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            with self._lock:
                record.status = JobStatus.FAILED
                record.finished_at = datetime.utcnow()
                record.exit_code = None
                record.error = f"Command not found: {exc}"
                record.log_excerpt.append(str(exc))
            if log_handle:
                log_handle.write(f"[error] {exc}\n")
                log_handle.flush()
            return

        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\n")
                if log_handle:
                    log_handle.write(raw_line)
                    log_handle.flush()
                with self._lock:
                    record.log_excerpt.append(line)
                    if len(record.log_excerpt) > 200:
                        record.log_excerpt = record.log_excerpt[-200:]
        finally:
            if log_handle:
                log_handle.close()

        exit_code = process.wait()
        with self._lock:
            record.exit_code = exit_code
            record.finished_at = datetime.utcnow()
            if exit_code == 0:
                record.status = JobStatus.SUCCEEDED
            else:
                record.status = JobStatus.FAILED
                if not record.error:
                    record.error = f"Process exited with status {exit_code}"

    # ------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------
    def _build_command(self, project_id: str, action: str, options: Dict[str, Any]) -> CommandSpec:
        builder = CommandBuilder(
            project_root=self._project_root,
            output_root=self._output_root,
            tools_root=self._tools_root,
            scripts_root=self._scripts_root,
            python_executable=self._python,
            project_loader=self._project_loader,
        )
        return builder.build(project_id=project_id, action=action, options=options)


@dataclass
class ProjectContext:
    project_id: str
    project_dir: Path
    detail: Any
    info: Dict[str, Any]
    srt_path: Optional[Path]
    template_used: Optional[str]
    draft_path: Optional[Path]
    channel_id: Optional[str]


class CommandBuilder:
    """Translate action identifiers into concrete CLI commands."""

    def __init__(
        self,
        *,
        project_root: Path,
        output_root: Path,
        tools_root: Path,
        scripts_root: Path,
        python_executable: str,
        project_loader: Callable[[str], Any],
    ) -> None:
        self._project_root = project_root
        self._output_root = output_root
        self._tools_root = tools_root
        self._scripts_root = scripts_root
        self._python = python_executable
        self._project_loader = project_loader
        self._config_root = project_root / "config"
        self._preset_cache: Dict[str, Any] = {"mtime": None, "data": {}}

    def build(self, *, project_id: str, action: str, options: Dict[str, Any]) -> CommandSpec:
        context = self._load_context(project_id)
        if action == "analyze_srt":
            return self._build_analyze_srt(context, options)
        if action == "regenerate_images":
            return self._build_regenerate_images(context, options)
        if action == "generate_image_variants":
            return self._build_generate_image_variants(context, options)
        if action == "generate_belt":
            return self._build_generate_belt(context, options)
        if action == "validate_capcut":
            return self._build_validate_capcut(context, options)
        if action == "build_capcut_draft":
            return self._build_capcut_draft(context, options)
        if action == "render_remotion":
            return self._build_render_remotion(context, options)
        if action == "upload_remotion_drive":
            return self._build_upload_remotion_drive(context, options)
        if action == "swap_images":
            return self._build_swap_images(context, options)
        raise ValueError(f"Unsupported job action: {action}")

    # ------------------------------------------------------------------
    # Individual builders
    # ------------------------------------------------------------------
    def _build_analyze_srt(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        if not context.srt_path or not context.srt_path.exists():
            raise ValueError("SRTファイルが見つかりません。capcut_draft_info.json の srt_file を確認してください。")
        out_dir = context.project_dir
        env = self._base_env()
        if (target_sections := options.get("target_sections")) is not None:
            env["SRT2IMAGES_TARGET_SECTIONS"] = str(target_sections)

        template_path = self._resolve_template_path(options.get("prompt_template"), context.template_used)

        command: List[str] = [
            str(self._run_srt2images_script()),
            "--srt",
            str(context.srt_path),
            "--out",
            str(out_dir),
            "--engine",
            "none",
            "--nanobanana",
            "none",
            "--cue-mode",
            str(options.get("cue_mode", "grouped")),
            "--force",
            "--prompt-template",
            str(template_path),
        ]

        if (style := options.get("style")):
            command.extend(["--style", str(style)])
        if (channel := options.get("channel")):
            command.extend(["--channel", str(channel)])

        summary = f"LLM 分析 (image_cues生成) ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_regenerate_images(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        if not context.srt_path:
            raise ValueError("SRT ファイルパスが取得できません。capcut_draft_info.json を確認してください。")
        if not context.srt_path.exists():
            raise ValueError(f"SRT ファイルが存在しません: {context.srt_path}")

        out_dir = context.project_dir
        template = self._resolve_template_path(options.get("prompt_template"), context.template_used)

        env = self._base_env()
        if prompt_style := options.get("style"):
            env["COMMENTARY02_PROMPT_STYLE"] = str(prompt_style)

        nanobanana = str(options.get("nanobanana", "batch"))
        if nanobanana not in ("batch", "direct", "none"):
            nanobanana = "direct"

        command: List[str] = [
            str(self._run_srt2images_script()),
            "--srt",
            str(context.srt_path),
            "--out",
            str(out_dir),
            "--engine",
            str(options.get("engine", "capcut")),
            "--nanobanana",
            nanobanana,
            "--prompt-template",
            str(template),
            "--cue-mode",
            str(options.get("cue_mode", "grouped")),
            "--imgdur",
            str(options.get("imgdur", 20.0)),
            "--crossfade",
            str(options.get("crossfade", 0.5)),
            "--fps",
            str(options.get("fps", 30)),
        ]

        if options.get("force"):
            command.append("--force")
        if options.get("use_aspect_guide"):
            command.append("--use-aspect-guide")
        if (style := options.get("style")) is not None:
            command.extend(["--style", str(style)])
        if (negative := options.get("negative")) is not None:
            command.extend(["--negative", str(negative)])
        if (concurrency := options.get("concurrency")) is not None:
            command.extend(["--concurrency", str(concurrency)])
        # nanobanana_bin/config deprecated; ignore if provided
        if (template_seed := options.get("seed")) is not None:
            command.extend(["--seed", str(template_seed)])
        if (channel := options.get("channel")) is not None:
            command.extend(["--channel", str(channel)])

        summary = f"SRT→画像再生成 ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_generate_belt(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        episode_info = context.project_dir / "episode_info.json"
        chapters = context.project_dir / "chapters.json"
        output_path = context.project_dir / "belt_config.json"

        for required in (episode_info, chapters):
            if not required.exists():
                raise ValueError(f"必須ファイルが不足しています: {required}")

        env = self._base_env()
        command = [
            str(self._with_env_script()),
            self._python,
            str(self._tools_root / "generate_belt_layers.py"),
            "--episode-info",
            str(episode_info),
            "--chapters",
            str(chapters),
            "--output",
            str(output_path),
        ]

        summary = f"ベルト設定生成 ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_generate_image_variants(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        run_dir = context.project_dir
        cues_path = run_dir / "image_cues.json"
        if not cues_path.exists():
            raise ValueError(f"image_cues.json が見つかりません: {cues_path}")

        env = self._base_env()
        command: List[str] = [
            str(self._with_env_script()),
            self._python,
            str(self._tools_root / "generate_image_variants.py"),
            "--run",
            str(run_dir),
        ]

        # styles
        style_presets = options.get("style_presets")
        if isinstance(style_presets, list):
            for item in style_presets:
                if isinstance(item, str) and item.strip():
                    command.extend(["--preset", item.strip()])

        custom_styles = options.get("custom_styles")
        if isinstance(custom_styles, list):
            for item in custom_styles:
                if isinstance(item, str) and item.strip():
                    command.extend(["--style", item.strip()])

        if (model_key := options.get("model_key")) is not None:
            mk = str(model_key).strip()
            if mk:
                command.extend(["--model-key", mk])

        if (max_cues := options.get("max")) is not None:
            try:
                max_int = int(max_cues)
                if max_int > 0:
                    command.extend(["--max", str(max_int)])
            except Exception:
                pass

        if (negative := options.get("negative")) is not None:
            neg = str(negative).strip()
            if neg:
                command.extend(["--negative", neg])

        if (timeout_sec := options.get("timeout_sec")) is not None:
            try:
                t = int(timeout_sec)
                if t > 0:
                    command.extend(["--timeout-sec", str(t)])
            except Exception:
                pass

        if options.get("retry_until_success"):
            command.append("--retry-until-success")

        if (max_retries := options.get("max_retries")) is not None:
            try:
                r = int(max_retries)
                if r > 0:
                    command.extend(["--max-retries", str(r)])
            except Exception:
                pass

        if options.get("force"):
            command.append("--force")

        if (channel := options.get("channel")) is not None:
            ch = str(channel).strip()
            if ch:
                command.extend(["--channel", ch])

        summary = f"画像バリアント生成 ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_validate_capcut(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        run_dir = context.project_dir
        if not run_dir.exists():
            raise ValueError(f"プロジェクト出力ディレクトリが存在しません: {run_dir}")

        env = self._base_env()
        command = [
            str(self._with_env_script()),
            self._python,
            str(self._tools_root / "comprehensive_validation.py"),
            "--run",
            str(run_dir),
        ]

        if context.draft_path and (options.get("use_existing_draft", True)):
            command.extend(["--draft-dir", str(context.draft_path)])
        if context.srt_path and context.srt_path.exists():
            command.extend(["--srt-file", str(context.srt_path)])

        json_output = options.get("json_output")
        if json_output:
            output_path = self._resolve_project_relative(run_dir, json_output)
            command.extend(["--json-output", str(output_path)])

        summary = f"CapCutドラフト検証 ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_capcut_draft(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        run_dir = context.project_dir
        if not run_dir.exists():
            raise ValueError(f"プロジェクト出力ディレクトリが存在しません: {run_dir}")

        draft_root = Path(options.get("draft_root") or (context.draft_path.parent if context.draft_path else Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"))
        requested_channel = options.get("channel") or context.channel_id
        preset = self._get_channel_preset(requested_channel)
        template_name = options.get("template") or context.template_used
        if not template_name:
            template_name = preset.get("capcut_template") if preset else None
        if not template_name:
            raise ValueError("テンプレート名が不明です。options.template もしくは capcut_draft_info.json の template_used を指定してください。")

        guard_result = evaluate_capcut_guard(run_dir, preset, raise_on_failure=True)

        new_name = options.get("new_draft_name") or f"{context.project_id}_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"
        belt_config = options.get("belt_config") or str(run_dir / "belt_config.json")

        env = self._base_env()
        if guard_result.get("cue_count") is not None:
            env["IMAGE_CUES_COUNT"] = str(guard_result["cue_count"])
        if guard_result.get("image_count") is not None:
            env["IMAGE_FILE_COUNT"] = str(guard_result["image_count"])
        env["CAPCUT_GUARD_STATUS"] = guard_result.get("status", "unknown")
        command: List[str] = [
            str(self._with_env_script()),
            self._python,
            str(self._tools_root / "capcut_bulk_insert.py"),
            "--run",
            str(run_dir),
            "--draft-root",
            str(draft_root),
            "--template",
            str(template_name),
            "--new",
            str(new_name),
            "--belt-config",
            str(self._resolve_project_relative(run_dir, belt_config)),
            "--opening-offset",
            str(self._resolve_opening_offset(options, context.info, preset)),
        ]

        if context.srt_path and context.srt_path.exists():
            command.extend(["--srt-file", str(context.srt_path)])
        title = options.get("title") or context.info.get("title")
        if title:
            command.extend(["--title", str(title)])

        transform_override = {
            key: options.get(key)
            for key in ("tx", "ty", "scale")
            if options.get(key) is not None
        }
        transform_context = context.info.get("transform") or {}
        if not transform_context and preset:
            position = preset.get("position") or {}
            transform_context = {k: position.get(k) for k in ("tx", "ty", "scale") if k in position}
        for key in ("tx", "ty", "scale"):
            if transform_override.get(key) is not None:
                command.extend([f"--{key}", str(transform_override[key])])
            elif key in transform_context:
                command.extend([f"--{key}", str(transform_context[key])])

        if options.get("validate_only", True):
            command.append("--validate-only")
        if (voice_file := options.get("voice_file")):
            command.extend(["--voice-file", str(self._resolve_project_relative(run_dir, voice_file))])
        if (rank_from_top := options.get("rank_from_top")) is not None:
            command.extend(["--rank-from-top", str(rank_from_top)])
        if options.get("inject_into_main"):
            command.append("--inject-into-main")
        if requested_channel:
            command.extend(["--channel", str(requested_channel)])
        if (fade_duration := options.get("fade_duration")) is not None:
            command.extend(["--fade-duration", str(fade_duration)])
        if options.get("auto_fade") is False:
            command.append("--disable-auto-fade")

        summary = f"CapCutドラフト生成 ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_render_remotion(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        if not context.srt_path or not context.srt_path.exists():
            raise ValueError("Remotion レンダリングには SRT ファイルが必要です。capcut_draft_info.json の srt_file を確認してください。")
        project_dir = context.project_dir
        images_dir = project_dir / "images"
        image_cues_path = project_dir / "image_cues.json"
        belt_config_path = project_dir / "belt_config.json"
        if not images_dir.exists():
            raise ValueError(f"images ディレクトリが見つかりません: {images_dir}")
        if not image_cues_path.exists():
            raise ValueError(f"image_cues.json が見つかりません: {image_cues_path}。まず analyze_srt を実行してください。")
        if not belt_config_path.exists():
            raise ValueError(f"belt_config.json が見つかりません: {belt_config_path}。まず generate_belt を実行してください。")

        env = self._base_env()
        channel = options.get("channel") or context.channel_id
        title = options.get("title") or context.info.get("title")

        command: List[str] = [
            str(self._with_env_script()),
            self._python,
            str(self._scripts_root / "remotion_export.py"),
            "render",
            "--run-dir",
            str(project_dir),
            "--srt",
            str(context.srt_path),
            "--fps",
            str(options.get("fps", 30)),
            "--size",
            str(options.get("size", "1920x1080")),
            "--crossfade",
            str(options.get("crossfade", 0.5)),
        ]
        if channel:
            command += ["--channel", str(channel)]
        if title:
            command += ["--title", str(title)]

        summary = f"Remotionレンダリング (mp4出力) ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_upload_remotion_drive(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        project_dir = context.project_dir
        remotion_mp4 = project_dir / "remotion" / "output" / "final.mp4"
        if not remotion_mp4.exists():
            raise ValueError(f"mp4 が見つかりません: {remotion_mp4}。先に render_remotion を実行してください。")

        env = self._base_env()
        channel = options.get("channel") or context.channel_id
        drive_name = options.get("name")
        folder_id = options.get("folder")

        command: List[str] = [
            str(self._with_env_script()),
            self._python,
            str(self._scripts_root / "remotion_export.py"),
            "upload",
            "--run-dir",
            str(project_dir),
        ]
        if channel:
            command += ["--channel", str(channel)]
        if drive_name:
            command += ["--name", str(drive_name)]
        if folder_id:
            command += ["--folder", str(folder_id)]

        summary = f"Google Driveへアップロード (Remotion) ({context.project_id})"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    def _build_swap_images(self, context: ProjectContext, options: Dict[str, Any]) -> CommandSpec:
        """Invoke safe_image_swap for specified indices (UI用の簡易エントリ)."""
        run_dir = Path(options.get("run_dir") or context.project_dir).resolve()
        if not run_dir.exists():
            raise ValueError(f"run_dir が存在しません: {run_dir}")

        draft_path = options.get("draft_path") or context.info.get("draft_path") or context.draft_path
        if not draft_path:
            raise ValueError("draft_path が指定されていません (capcut_draft_info.json の draft_path も未設定)")
        draft_path = Path(draft_path).resolve()
        if not draft_path.exists():
            raise ValueError(f"draft が存在しません: {draft_path}")

        indices_opt = options.get("indices") or options.get("index")
        if not indices_opt:
            raise ValueError("indices を指定してください (例: [4,5,6] or \"4,5,6\")")
        if isinstance(indices_opt, str):
            indices_list = [int(x.strip()) for x in indices_opt.split(",") if x.strip()]
        elif isinstance(indices_opt, (list, tuple)):
            indices_list = [int(x) for x in indices_opt]
        else:
            raise ValueError("indices は配列またはカンマ区切り文字列で指定してください")
        if not indices_list:
            raise ValueError("有効な indices がありません")

        style_mode = options.get("style_mode", "illustration")
        custom_prompt = options.get("custom_prompt") or ""
        apply_flag = bool(options.get("apply"))
        only_allow = options.get("only_allow_draft_substring") or draft_path.name
        validate_after = bool(options.get("validate_after"))
        rollback_on_validate_fail = bool(options.get("rollback_on_validate_fail"))

        env = self._base_env()
        command: List[str] = [
            self._python,
            str(self._tools_root / "safe_image_swap.py"),
            "--run-dir",
            str(run_dir),
            "--draft",
            str(draft_path),
            "--indices",
            *[str(i) for i in indices_list],
            "--style-mode",
            str(style_mode),
            "--only-allow-draft-substring",
            str(only_allow),
        ]
        if custom_prompt:
            command += ["--custom-prompt", str(custom_prompt)]
        if apply_flag:
            command.append("--apply")
        else:
            command.append("--dry-run")
        if validate_after:
            command.append("--validate-after")
        if validate_after and rollback_on_validate_fail:
            command.append("--rollback-on-validate-fail")

        summary = f"画像差し替え (safe_image_swap) {context.project_id} indices={indices_list} apply={apply_flag}"
        return CommandSpec(command=command, cwd=self._project_root, env=env, summary=summary)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _script_path(self, script_name: str) -> Path:
        path = self._scripts_root / script_name
        if not path.exists():
            raise ValueError(f"{script_name} が見つかりません: {path}")
        if not os.access(path, os.X_OK):
            raise ValueError(f"{script_name} が実行できません: {path}")
        return path

    def _run_srt2images_script(self) -> Path:
        return self._script_path("run_srt2images.sh")

    def _with_env_script(self) -> Path:
        return self._script_path("with_ytm_env.sh")

    def _base_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        python_path_parts = [
            str(self._project_root),
            str(self._project_root / "src"),
            env.get("PYTHONPATH", ""),
        ]
        env["PYTHONPATH"] = os.pathsep.join([part for part in python_path_parts if part])
        env.setdefault("COMMENTARY02_ROOT", str(self._project_root))
        env.setdefault("COMMENTARY02_OUTPUT_ROOT", str(self._output_root))
        return env

    def _load_channel_presets(self) -> Dict[str, Any]:
        path = self._config_root / "channel_presets.json"
        if not path.exists():
            return {}
        mtime = path.stat().st_mtime
        cache = self._preset_cache
        if cache["mtime"] == mtime:
            return cache["data"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        cache["mtime"] = mtime
        cache["data"] = data
        return data

    def _get_channel_preset(self, channel_code: Optional[str]) -> Optional[Dict[str, Any]]:
        if not channel_code:
            return None
        presets = self._load_channel_presets().get("channels", {})
        return presets.get(channel_code.upper())

    def _resolve_opening_offset(self, options: Dict[str, Any], info: Dict[str, Any], preset: Optional[Dict[str, Any]]) -> float:
        if "opening_offset" in options and options["opening_offset"] is not None:
            return float(options["opening_offset"])
        if "opening_offset" in info and info["opening_offset"] is not None:
            return float(info["opening_offset"])
        if preset:
            belt = preset.get("belt") or {}
            if belt.get("opening_offset") is not None:
                return float(belt["opening_offset"])
        return 3.0

    def _resolve_template_path(self, override: Optional[str], template_used: Optional[str]) -> Path:
        """
        Resolve prompt template with registry awareness.
        """
        candidate = override or template_used
        if candidate:
            path = resolve_template_path(candidate)
            if not is_registered_template(path):
                logger.warning("Prompt template not registered: %s", path)
            if not Path(path).exists():
                raise ValueError(f"プロンプトテンプレートが見つかりません: {path}")
            return Path(path)
        # Fallback default
        fallback = self._project_root / "templates" / "default.txt"
        if not fallback.exists():
            raise ValueError(f"プロンプトテンプレートが見つかりません: {fallback}")
        return fallback

    def _resolve_project_relative(self, project_dir: Path, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        return (project_dir / value).resolve()

    def _load_context(self, project_id: str) -> ProjectContext:
        project_dir = (self._output_root / project_id).resolve()
        detail = self._project_loader(project_id)

        info: Dict[str, Any] = {}
        info_path = project_dir / "capcut_draft_info.json"
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                info = {}

        srt_path: Optional[Path] = None
        template_used: Optional[str] = None
        summary = getattr(detail, "summary", None) if detail else None
        if summary is not None:
            template_used = getattr(summary, "template_used", None)
            srt_value = getattr(summary, "srt_file", None)
            if srt_value:
                srt_candidate = Path(srt_value)
                if not srt_candidate.is_absolute():
                    srt_candidate = (self._project_root / srt_candidate).resolve()
                srt_path = srt_candidate
        if not srt_path and "srt_file" in info:
            srt_candidate = Path(info["srt_file"])
            if not srt_candidate.is_absolute():
                srt_candidate = (self._project_root / srt_candidate).resolve()
            srt_path = srt_candidate

        draft_path: Optional[Path] = None
        if info.get("draft_path"):
            draft_candidate = Path(info["draft_path"])
            if draft_candidate.exists():
                draft_path = draft_candidate

        channel_code: Optional[str] = info.get("channel_id")
        if not channel_code and summary is not None:
            channel_code = getattr(summary, "channelId", None) or getattr(summary, "channel_id", None)
        if not channel_code and "-" in project_id:
            channel_code = project_id.split("-", 1)[0]
        if channel_code:
            channel_code = channel_code.upper()

        preset = self._get_channel_preset(channel_code)
        if preset:
            if not info.get("transform"):
                position = preset.get("position") or {}
                info["transform"] = {
                    "tx": float(position.get("tx", 0.0)),
                    "ty": float(position.get("ty", 0.0)),
                    "scale": float(position.get("scale", 1.0)),
                }
            if "opening_offset" not in info and preset.get("belt"):
                belt = preset["belt"]
                if "opening_offset" in belt:
                    info["opening_offset"] = belt["opening_offset"]

        return ProjectContext(
            project_id=project_id,
            project_dir=project_dir,
            detail=detail,
            info=info,
            srt_path=srt_path,
            template_used=template_used,
            draft_path=draft_path,
            channel_id=channel_code,
        )

def evaluate_capcut_guard(
    run_dir: Path,
    preset: Optional[Dict[str, Any]],
    *,
    raise_on_failure: bool = False,
) -> Dict[str, Any]:
    """Inspect image assets / image_cues to ensure CapCut can run safely."""

    result: Dict[str, Any] = {
        "status": "ok",
        "issues": [],
        "cue_count": None,
        "image_count": None,
        "min_image_bytes": int((preset or {}).get("image_min_bytes") or _DEFAULT_MIN_IMAGE_BYTES),
        "persona_required": bool((preset or {}).get("persona_required")),
        "missing_profiles": [],
        "tiny_images": [],
        "project_dir": str(run_dir),
        "image_dir": str(run_dir / "images"),
        "recommended_commands": [
            'PYTHONPATH=".:packages" python3 -m video_pipeline.tools.run_pipeline --srt <workspaces/video/input/.../*.srt> --out workspaces/video/runs/<project> --force',
            'PYTHONPATH=".:packages" python3 -m video_pipeline.tools.generate_belt_layers --episode-info workspaces/video/runs/<project>/episode_info.json --chapters workspaces/video/runs/<project>/chapters.json --output workspaces/video/runs/<project>/belt_config.json',
        ],
    }

    def add_issue(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        issue = {"code": code, "message": message}
        if details:
            issue["details"] = details
        result["issues"].append(issue)

    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        add_issue("missing_cues", f"image_cues.json が見つかりません: {cues_path}")
        result["status"] = "fail"
        if raise_on_failure:
            raise ValueError(result["issues"][0]["message"])
        return result

    try:
        cues_payload = json.loads(cues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add_issue("invalid_cues", f"image_cues.json の解析に失敗しました: {exc}")
        result["status"] = "fail"
        if raise_on_failure:
            raise ValueError(result["issues"][0]["message"])
        return result

    cues_list = cues_payload.get("sections")
    if not isinstance(cues_list, list) or not cues_list:
        cues_list = cues_payload.get("cues")
    if not isinstance(cues_list, list) or not cues_list:
        add_issue("empty_cues", "image_cues.json に有効な sections/cues がありません")
        result["status"] = "fail"
        if raise_on_failure:
            raise ValueError(result["issues"][0]["message"])
        return result

    result["cue_count"] = len(cues_list)

    images_dir = run_dir / "images"
    if not images_dir.exists():
        add_issue("missing_images", f"images ディレクトリが存在しません: {images_dir}")
        result["status"] = "fail"
        if raise_on_failure:
            raise ValueError(result["issues"][0]["message"])
        return result

    image_files = sorted(
        [path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES]
    )
    result["image_count"] = len(image_files)
    if not image_files:
        add_issue("no_images", f"images/ 配下に PNG/JPEG ファイルがありません: {images_dir}")

    if result["cue_count"] is not None and result["image_count"] is not None:
        if result["cue_count"] != result["image_count"]:
            add_issue(
                "cue_mismatch",
                f"画像枚数({result['image_count']})と image_cues セクション数({result['cue_count']}) が一致しません",
            )

    min_bytes = result["min_image_bytes"]
    tiny_images = [path.name for path in image_files if path.stat().st_size < min_bytes]
    if tiny_images:
        result["tiny_images"] = tiny_images
        add_issue(
            "tiny_images",
            f"{min_bytes} bytes 未満のプレースホルダ疑い画像があります",
            {"files": tiny_images[:10]},
        )

    persona_required = result["persona_required"]
    missing_profiles: List[int] = []
    if persona_required:
        for idx, cue in enumerate(cues_list, start=1):
            if not str(cue.get("character_profile", "")).strip():
                missing_profiles.append(idx)
        if missing_profiles:
            result["missing_profiles"] = missing_profiles
            add_issue(
                "missing_character_profile",
                "character_profile が入力されていないセクションがあります",
                {"indices": missing_profiles[:20]},
            )

    if result["issues"]:
        result["status"] = "fail"
        if raise_on_failure:
            raise ValueError("; ".join(issue["message"] for issue in result["issues"]))

    return result


def job_to_dict(record: JobRecord) -> Dict[str, Any]:
    """Convert JobRecord to serialisable dict."""
    return {
        "id": record.id,
        "project_id": record.project_id,
        "action": record.action,
        "options": record.options,
        "status": record.status.value,
        "created_at": record.created_at.isoformat() + "Z",
        "started_at": record.started_at.isoformat() + "Z" if record.started_at else None,
        "finished_at": record.finished_at.isoformat() + "Z" if record.finished_at else None,
        "exit_code": record.exit_code,
        "error": record.error,
        "command": record.command,
        "note": record.note,
        "summary": record.summary,
        "log_path": str(record.log_path) if record.log_path else None,
        "log_excerpt": record.log_excerpt,
    }
