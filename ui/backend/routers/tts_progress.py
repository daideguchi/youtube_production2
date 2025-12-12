"""TTS Progress monitoring router for the UI."""

from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from factory_common.paths import audio_artifacts_root, script_data_root

router = APIRouter(prefix="/api/tts-progress", tags=["tts-progress"])

# SoT roots (Path SSOT)
TTS_OUTPUT_DIR = audio_artifacts_root() / "final"
SCRIPT_DATA_DIR = script_data_root()


class ChannelProgress(BaseModel):
    """Progress for a single channel."""
    channel: str
    total_episodes: int
    completed_episodes: int
    completed_ids: list[str]
    missing_ids: list[str]
    progress_percent: float


class TtsProgressResponse(BaseModel):
    """Response for TTS progress endpoint."""
    channels: list[ChannelProgress]
    overall_progress: float


def get_channel_episodes(channel: str) -> list[str]:
    """Get all episode IDs for a channel from the script data root."""
    channel_dir = SCRIPT_DATA_DIR / channel
    if not channel_dir.exists():
        return []
    
    episodes = []
    for item in channel_dir.iterdir():
        if item.is_dir() and item.name.isdigit():
            # Check if assembled.md exists
            assembled = item / "content" / "assembled.md"
            if assembled.exists():
                episodes.append(item.name)
    
    return sorted(episodes)


def get_completed_episodes(channel: str) -> list[str]:
    """Get completed episode IDs from TTS output directory.
    
    完了判定基準:
    1. 最終wavファイル ({channel}-{id}.wav) が存在する
    2. またはchunksディレクトリに10個以上のwavファイルがある
    """
    output_dir = TTS_OUTPUT_DIR / channel
    if not output_dir.exists():
        return []
    
    completed = []
    for item in output_dir.iterdir():
        if item.is_dir() and item.name.isdigit():
            # 優先: 最終wavファイルをチェック
            final_wav = item / f"{channel}-{item.name}.wav"
            if final_wav.exists():
                completed.append(item.name)
                continue
            
            # 代替: chunksに十分なwavファイルがあるか
            chunks_dir = item / "chunks"
            if chunks_dir.exists():
                wav_count = len(list(chunks_dir.glob("*.wav")))
                if wav_count >= 10:  # 最低10チャンク以上で完了とみなす
                    completed.append(item.name)
    
    return sorted(completed)



@router.get("", response_model=TtsProgressResponse)
async def get_tts_progress():
    """Get TTS generation progress for all channels."""
    channels_progress = []
    
    # Check all CH* channels
    for channel_code in ["CH02", "CH04", "CH06"]:
        total_episodes = get_channel_episodes(channel_code)
        all_completed = get_completed_episodes(channel_code)
        
        # スクリプトに存在するエピソードのうち、TTS生成済みのもの
        total_set = set(total_episodes)
        completed_episodes = [ep for ep in all_completed if ep in total_set]
        missing = [ep for ep in total_episodes if ep not in set(all_completed)]
        
        total = len(total_episodes)
        completed = len(completed_episodes)
        progress = min((completed / total * 100) if total > 0 else 0, 100.0)  # 100%上限
        
        channels_progress.append(ChannelProgress(
            channel=channel_code,
            total_episodes=total,
            completed_episodes=completed,
            completed_ids=completed_episodes,
            missing_ids=missing,
            progress_percent=round(progress, 1)
        ))
    
    # Calculate overall progress
    total_all = sum(cp.total_episodes for cp in channels_progress)
    completed_all = sum(cp.completed_episodes for cp in channels_progress)
    overall = (completed_all / total_all * 100) if total_all > 0 else 0
    
    return TtsProgressResponse(
        channels=channels_progress,
        overall_progress=round(overall, 1)
    )


@router.get("/{channel}", response_model=ChannelProgress)
async def get_channel_tts_progress(channel: str):
    """Get TTS progress for a specific channel."""
    total_episodes = get_channel_episodes(channel)
    all_completed = get_completed_episodes(channel)
    
    if not total_episodes:
        raise HTTPException(status_code=404, detail=f"Channel {channel} not found")
    
    # スクリプトに存在するエピソードのうち、TTS生成済みのもの
    total_set = set(total_episodes)
    completed_episodes = [ep for ep in all_completed if ep in total_set]
    missing = [ep for ep in total_episodes if ep not in set(all_completed)]
    
    total = len(total_episodes)
    completed = len(completed_episodes)
    progress = min((completed / total * 100) if total > 0 else 0, 100.0)
    
    return ChannelProgress(
        channel=channel,
        total_episodes=total,
        completed_episodes=completed,
        completed_ids=completed_episodes,
        missing_ids=missing,
        progress_percent=round(progress, 1)
    )
