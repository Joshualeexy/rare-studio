#!/usr/bin/env python3
"""
Re-burn word-level karaoke subtitles (2-3 words per card, active word golden orange, uppercase)
onto generated videos in 2026-09-10, output/, or any custom directories.
"""
import os
import sys
import glob
import json
import re
import shutil
import subprocess
import argparse

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from app.services import voice

FONTS_DIR = os.path.join(WORKSPACE_ROOT, "resource", "fonts")


_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            print(f"  [Warning] faster_whisper not available: {e}")
            _whisper_model = False
    return _whisper_model


def generate_option_a_ass(media_path: str, srt_path: str, ass_path: str):
    """
    Generates Option A progressive karaoke subtitles:
    - 1 to 3 words per card (break on punctuation or pause > 0.35s)
    - Word 1 in Golden-Orange (#FF9C0C), future words hidden
    - Word 2 in Gold, Word 1 turns White (#FFFFFF)
    - Word 3 in Gold, Words 1 & 2 White
    - Card disappears immediately once phrase ends (no lingering text)
    - Montserrat Black font, dark opaque rounded background at 70% height
    - Transcribed acoustically with faster_whisper down to the millisecond
    """
    words = []
    model = get_whisper_model()
    if model and media_path and os.path.exists(media_path):
        try:
            segments, _ = model.transcribe(media_path, word_timestamps=True)
            for s in segments:
                for w in s.words:
                    cleaned = w.word.strip()
                    if cleaned:
                        words.append({
                            "word": cleaned,
                            "start": float(w.start),
                            "end": float(w.end)
                        })
        except Exception as e:
            print(f"  [Warning] Whisper acoustic transcription failed ({e}), falling back to SRT")
            words = []

    # Fallback to SRT if whisper had no words
    if not words and srt_path and os.path.exists(srt_path):
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        blocks = re.split(r'\n\s*\n', content.strip())
        for block in blocks:
            lines = [l.strip() for l in block.splitlines() if l.strip()]
            if len(lines) < 3:
                continue
            m = re.match(r'(\d+):(\d+):(\d+)[,\.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,\.](\d+)', lines[1])
            if not m:
                continue
            start_sec = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/1000.0
            end_sec = int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7)) + int(m.group(8))/1000.0
            text = ' '.join(lines[2:])
            w_list = text.split()
            if not w_list:
                continue
            dur_sec = max(0.1, end_sec - start_sec)
            total_chars = sum(len(w) for w in w_list)
            curr_t = start_sec
            for w in w_list:
                w_dur = dur_sec * (len(w) / total_chars)
                words.append({
                    "word": w,
                    "start": round(curr_t, 3),
                    "end": round(curr_t + w_dur, 3)
                })
                curr_t += w_dur

    if not words:
        return []

    # Group into 1-3 word phrases (breaking on punctuation or pause > 0.35s)
    phrases = []
    curr = []
    for i, w in enumerate(words):
        curr.append(w)
        raw = w["word"]
        ends_punct = bool(re.search(r'[.?!,;:]$', raw))
        has_gap = False
        if i < len(words) - 1:
            if words[i+1]["start"] - w["end"] > 0.35:
                has_gap = True
        if len(curr) >= 3 or ends_punct or has_gap:
            phrases.append(curr)
            curr = []
    if curr:
        phrases.append(curr)

    def to_ass_time(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    c_gold = "{\\c&H000C9CFF&}"
    c_white = "{\\c&H00FFFFFF&}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat Black,56,&H00FFFFFF,&H000C9CFF,&H00000000,&HF0101010,-1,0,0,0,100,100,0,0,3,18,0,2,40,40,540,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for phrase in phrases:
        clean_words = [re.sub(r"[^\w\']", "", c["word"]).upper() for c in phrase]
        for i, cue in enumerate(phrase):
            start_t = cue["start"]
            if i < len(phrase) - 1:
                end_t = phrase[i+1]["start"]
            else:
                end_t = cue["end"]

            if end_t <= start_t:
                end_t = start_t + 0.1

            start_str = to_ass_time(start_t)
            end_str = to_ass_time(end_t)

            line_parts = []
            for j in range(i + 1):
                w_text = clean_words[j]
                if j == i:
                    line_parts.append(f"{c_gold}{w_text}")
                else:
                    line_parts.append(f"{c_white}{w_text}")

            card_text = " ".join(line_parts)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{card_text}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

    return words


def get_or_generate_srt(video_dir: str, meta: dict) -> str:
    """Finds existing SRT or synthesizes fresh word-accurate SRT via Edge-TTS."""
    local_srt = os.path.join(video_dir, "subtitle.srt")
    if os.path.exists(local_srt) and os.path.getsize(local_srt) > 20:
        return local_srt

    task_id = meta.get("task_id")
    if task_id:
        task_srt = os.path.join(WORKSPACE_ROOT, "storage", "tasks", task_id, "subtitle.srt")
        if os.path.exists(task_srt) and os.path.getsize(task_srt) > 20:
            shutil.copy2(task_srt, local_srt)
            return local_srt

    # Look up checkpoint for voice name and voice rate
    voice_name = None
    voice_rate = 1.12
    if task_id:
        cp_matches = glob.glob(os.path.join(WORKSPACE_ROOT, "storage", "checkpoints", "**", task_id, "completed_state.json"), recursive=True)
        if cp_matches:
            try:
                with open(cp_matches[0], "r", encoding="utf-8") as cf:
                    c = json.load(cf)
                    voice_name = c.get("voice_name")
                    voice_rate = float(c.get("voice_rate") or 1.12)
            except Exception:
                pass

    if not voice_name:
        voice_name = "en-US-ChristopherNeural"

    script = meta.get("script")
    if not script:
        print(f"  [Error] No script found in metadata for {video_dir}")
        return None

    # Clean script for accurate synthesis
    clean_script = re.sub(r'\[.*?\]|\(.*?\)', '', script).strip()
    clean_script = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', clean_script).strip()
    clean_script = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', clean_script, flags=re.IGNORECASE).strip()
    clean_script = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', clean_script, flags=re.IGNORECASE).strip()

    print(f"  Synthesizing subtitle timings with {voice_name} (rate: {voice_rate}x)...")
    temp_audio = os.path.join(video_dir, ".temp_synth.mp3")
    try:
        sub = voice.azure_tts_v1(clean_script, voice_name, voice_rate, temp_audio)
        if not sub:
            voice_name = "en-US-ChristopherNeural"
            sub = voice.azure_tts_v1(clean_script, voice_name, voice_rate, temp_audio)

        if sub:
            voice.create_subtitle(sub, clean_script, local_srt)
            if os.path.exists(local_srt) and os.path.getsize(local_srt) > 20:
                print(f"  ✓ Subtitle track generated successfully: {local_srt}")
                return local_srt
    except Exception as e:
        print(f"  [Error] Subtitle synthesis failed: {e}")
    finally:
        if os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except Exception:
                pass

    return None


def process_video(meta_path: str, force: bool = False) -> bool:
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        print(f"Error reading {meta_path}: {e}")
        return False

    video_dir = os.path.dirname(os.path.abspath(meta_path))

    # Identify destination video
    dest_video = None
    mp4_files = [f for f in glob.glob(os.path.join(video_dir, "*.mp4")) if not f.endswith(".reburned.mp4")]
    if mp4_files:
        dest_video = mp4_files[0]
    elif meta.get("file_path") and os.path.exists(meta["file_path"]):
        dest_video = meta["file_path"]

    if not dest_video or not os.path.exists(dest_video):
        print(f"  [Skip] Destination video not found in {video_dir}")
        return False

    # Check if this video has source backup or clean copy
    task_id = meta.get("task_id")
    source_video = dest_video
    if task_id:
        task_dir = os.path.join(WORKSPACE_ROOT, "storage", "tasks", task_id)
        combined_candidate = os.path.join(task_dir, "combined.mp4")
        if os.path.exists(combined_candidate):
            source_video = combined_candidate

    if source_video == dest_video and meta.get("subtitles_burned"):
        print(f"  [Skip] No clean combined.mp4 found for {video_dir} and subtitles are already burned (prevents overlap)")
        return False

    # Pass audio.mp3 or source_video to whisper for acoustic millisecond timestamps
    media_for_transcription = source_video
    if task_id:
        task_dir = os.path.join(WORKSPACE_ROOT, "storage", "tasks", task_id)
        task_audio = os.path.join(task_dir, "audio.mp3")
        if os.path.exists(task_audio):
            media_for_transcription = task_audio

    srt_path = os.path.join(video_dir, "subtitle.srt")
    if not os.path.exists(srt_path):
        srt_path = get_or_generate_srt(video_dir, meta)

    ass_path = os.path.join(video_dir, "karaoke.ass")
    word_cues = generate_option_a_ass(media_for_transcription, srt_path, ass_path)

    # Save karaoke.json as well
    with open(os.path.join(video_dir, "karaoke.json"), "w", encoding="utf-8") as kf:
        json.dump(word_cues, kf, indent=2)

    if task_id:
        task_dir = os.path.join(WORKSPACE_ROOT, "storage", "tasks", task_id)
        if os.path.exists(task_dir):
            try:
                shutil.copy2(ass_path, os.path.join(task_dir, "karaoke.ass"))
                shutil.copy2(os.path.join(video_dir, "karaoke.json"), os.path.join(task_dir, "karaoke.json"))
            except Exception:
                pass

    tmp_out = dest_video + ".reburned.mp4"
    print(f"  Re-burning: {meta.get('title', os.path.basename(dest_video))} ({len(word_cues)} cues)...")

    cmd = [
        "ffmpeg", "-y",
        "-i", source_video,
        "-vf", f"ass={ass_path}:fontsdir={FONTS_DIR}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "copy",
        tmp_out
    ]

    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) < 100000:
        print(f"  [Error] FFmpeg failed: {res.stderr.decode()[-300:]}")
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return False

    # Replace destination video
    shutil.move(tmp_out, dest_video)
    if task_id:
        task_dir = os.path.join(WORKSPACE_ROOT, "storage", "tasks", task_id)
        if os.path.exists(task_dir):
            try:
                shutil.copy2(dest_video, os.path.join(task_dir, "final.mp4"))
            except Exception:
                pass

    # Update metadata file size and mark subtitles_burned
    new_size_mb = round(os.path.getsize(dest_video) / (1024 * 1024), 2)
    meta["file_path"] = os.path.abspath(dest_video)
    meta["file_size_mb"] = new_size_mb
    meta["subtitles_burned"] = True
    meta["karaoke_style"] = "word_highlight_gold"
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    print(f"  ✓ Success: {dest_video} ({new_size_mb} MB)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Re-burn word-level karaoke subtitles onto generated videos.")
    parser.add_argument("directories", nargs="*", default=["2026-09-10", "output"], help="Directories to scan for metadata.json (default: 2026-09-10 output)")
    parser.add_argument("--force", action="store_true", default=True, help="Force re-burning")
    args = parser.parse_args()

    meta_files = []
    for d in args.directories:
        p = os.path.abspath(d) if os.path.isabs(d) else os.path.join(WORKSPACE_ROOT, d)
        if os.path.exists(p):
            found = sorted(glob.glob(os.path.join(p, "**/metadata.json"), recursive=True))
            print(f"Found {len(found)} video archives in {d}")
            meta_files.extend(found)
        else:
            print(f"Warning: Directory not found: {d}")

    seen = set()
    unique_metas = []
    for mf in meta_files:
        if mf not in seen:
            seen.add(mf)
            unique_metas.append(mf)

    print(f"\nTotal video archives to process: {len(unique_metas)}")
    success_count = 0
    for idx, mf in enumerate(unique_metas, 1):
        print(f"\n[{idx}/{len(unique_metas)}] Processing {mf}...")
        if process_video(mf, force=True):
            success_count += 1
    print(f"\n🎉 Finished re-burning authentic karaoke subtitles on {success_count}/{len(unique_metas)} videos!")


if __name__ == "__main__":
    main()
