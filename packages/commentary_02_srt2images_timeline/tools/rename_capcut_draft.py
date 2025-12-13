#!/usr/bin/env python3
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
import shutil


from typing import Optional, Tuple, Dict, Any


def load_info(run_dir: Path) -> Optional[Dict[str, Any]]:
    info_path = run_dir / 'capcut_draft_info.json'
    if info_path.exists():
        try:
            return json.loads(info_path.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def determine_current_draft(run_dir: Path, default_root: Path) -> Tuple[Path, str]:
    info = load_info(run_dir)
    if info and info.get('draft_path') and info.get('draft_name'):
        return Path(info['draft_path']), info['draft_name']
    # Fallback: resolve via symlink
    link = run_dir / 'capcut_draft'
    if link.is_symlink():
        path = link.resolve()
        return path, path.name
    # Not found
    raise FileNotFoundError(f"Cannot locate draft for {run_dir}")


def build_prefixed_name(run_dir_name: str, base_name: str, tags: Optional[str] = None) -> str:
    # Extract trailing digits from run_dir_name or base_name
    m = re.search(r'(\d+)', run_dir_name) or re.search(r'(\d+)', base_name)
    num = m.group(1) if m else '0'
    prefix = f"{int(num):03d}"
    now = datetime.now().strftime('%Y%m%d_%H%M%S')
    parts = [prefix, base_name]
    if tags:
        parts.append(tags)
    parts.append('16x9')
    parts.append(now)
    return '_'.join(parts)


def rename_draft(run_dir: Path, draft_root: Path, new_name: str):
    draft_path, old_name = determine_current_draft(run_dir, draft_root)
    if draft_path.parent != draft_root:
        # Move under the expected root if needed
        draft_path = draft_path
    new_path = draft_root / new_name
    # If destination exists, allow replace by moving old to archive or removing
    if new_path.exists():
        shutil.rmtree(new_path)
    shutil.move(str(draft_path), str(new_path))

    # Update symlink
    link = run_dir / 'capcut_draft'
    try:
        if link.exists() or link.is_symlink():
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link)
    except Exception:
        pass
    link.symlink_to(new_path)

    # Update info JSON
    info = load_info(run_dir) or {}
    info.update({
        'draft_name': new_name,
        'draft_path': str(new_path),
        'renamed_at': datetime.now().isoformat(timespec='seconds'),
        'previous_name': old_name,
    })
    (run_dir / 'capcut_draft_info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='Rename CapCut draft with a prefixed numbering convention and update output links')
    ap.add_argument('--run', required=True, help='Output run directory (contains capcut_draft*)')
    ap.add_argument('--draft-root', default=str(Path.home() / 'Movies/CapCut/User Data/Projects/com.lveditor.draft'))
    ap.add_argument('--new-name', help='Explicit new draft name to set')
    ap.add_argument('--tags', default='文字なし_日本シニア_やさしい_人物統一')
    args = ap.parse_args()

    run_dir = Path(args.run).resolve()
    draft_root = Path(args.draft_root).resolve()

    if args.new_name:
        new_name = args.new_name
    else:
        # Derive base name from current draft name or run dir
        _, cur_name = determine_current_draft(run_dir, draft_root)
        base_name = cur_name
        new_name = build_prefixed_name(run_dir.name, base_name, args.tags)

    rename_draft(run_dir, draft_root, new_name)
    print(f"Renamed draft to: {new_name}")


if __name__ == '__main__':
    main()
