import shutil
import re
from pathlib import Path

def prepare_inputs():
    channels = ["CH02", "CH04", "CH06", "CH05", "CH09"]
    
    for channel in channels:
        base_src = Path(f"script_pipeline/data/{channel}")
        base_dst = Path(f"audio_tts_v2/inputs/{channel}")
        base_dst.mkdir(parents=True, exist_ok=True)
        
        if not base_src.exists():
            print(f"[WARN] Source directory missing for {channel}")
            continue

        # Auto-discover video directories (identifiers like '001', '002'...)
        # Filter for identifiers that are digits
        video_dirs = sorted([d.name for d in base_src.iterdir() if d.is_dir() and d.name.isdigit()])
        
        print(f"[{channel}] Found {len(video_dirs)} videos.")

        for vid in video_dirs:
            src = base_src / vid / "content/assembled.md"
            dst = base_dst / f"{vid}.txt"
            
            if not src.exists():
                # print(f"[WARN] Assembled source missing for {channel}/{vid}")
                continue
                
            text = src.read_text(encoding="utf-8")
            
            # [Sanitization]
            lines = text.splitlines()
            has_heading = any(line.strip().startswith("#") for line in lines)
            
            if not has_heading:
                print(f"[{channel}/{vid}] No headings found! Fixing...")
                new_lines = []
                for line in lines:
                    if re.match(r"^第[0-9]+章", line.strip()):
                        new_lines.append("# " + line)
                    else:
                        new_lines.append(line)
                text = "\n".join(new_lines)
                
            # Fix commas in headings
            def fix_heading(m):
                content = m.group(0)
                return content.replace("、", "　").replace("，", "　")
                
            text = re.sub(r"^#{1,6}\s*.*$", fix_heading, text, flags=re.MULTILINE)
    
            dst.write_text(text, encoding="utf-8")
            # print(f"[{channel}/{vid}] Prepared input.")
            
    print("All inputs prepared.")

if __name__ == "__main__":
    prepare_inputs()
