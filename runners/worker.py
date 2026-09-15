#!/usr/bin/env python3
"""
==============================================================================
Niche Video Worker Orchestrator
==============================================================================
Autonomous video creation worker with full checkpointing, crash recovery,
and NVENC hardware-accelerated rendering. Modeled on the AffiliateKage design.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import socket

# Force IPv4 resolution to prevent SSLError / connection timeouts on hosts with unreachable IPv6
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _getaddrinfo_ipv4

# Ensure MoviePy and imageio use system FFmpeg with NVENC hardware acceleration
os.environ["IMAGEIO_FFMPEG_EXE"] = "/usr/bin/ffmpeg"

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from loguru import logger

# Route low-level library debug spam to pipeline_debug.log, keeping stdout clean & informative
try:
    logger.remove()
    logger.add("pipeline_debug.log", rotation="25 MB", retention="5 days", level="DEBUG", encoding="utf-8")
    logger.add(sys.stderr, level="ERROR", format="<red>[ERROR]</red> {message}")
except Exception:
    pass

from app.config import config
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams
from app.services import llm, material, subtitle, video, voice
from app.utils import utils
from core.checkpoint import CheckpointManager
def srt_to_word_cues(srt_path: str) -> list:
    """Parses subtitle.srt and interpolates proportional word-level timestamps."""
    if not os.path.exists(srt_path):
        return []
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r'\n\s*\n', content.strip())
    word_cues = []
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r'(\d+):(\d+):(\d+)[,\.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,\.](\d+)', lines[1])
        if not m:
            continue
        start_sec = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/1000.0
        end_sec = int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7)) + int(m.group(8))/1000.0
        text = " ".join(lines[2:])
        words = text.split()
        if not words:
            continue
        total_chars = sum(len(w) for w in words)
        dur = max(0.1, end_sec - start_sec)
        curr_t = start_sec
        for w in words:
            w_dur = dur * (len(w) / total_chars)
            word_cues.append({
                "word": w,
                "start": round(curr_t, 3),
                "end": round(curr_t + w_dur, 3)
            })
            curr_t += w_dur
    return word_cues


def get_sentence_scenes(srt_path: str, audio_duration: float, min_dur: float = 2.5, max_dur: float = 4.8) -> list:
    """Parses subtitle.srt into frame-accurate, contiguous narrative scenes synchronized to narrator speech pauses."""
    if not os.path.exists(srt_path):
        return []
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r"(\d+)\n(\d{2}:\d{2}:\d{2}[,\.]\d{3}) --> (\d{2}:\d{2}:\d{2}[,\.]\d{3})\n(.*?)(?=\n\n|\n*$)"
    matches = re.findall(pattern, content, re.DOTALL)

    def to_sec(ts):
        ts = ts.replace(",", ".")
        h, m, s = ts.split(":")
        return int(h)*3600 + int(m)*60 + float(s)

    items = []
    for m in matches:
        s_sec = to_sec(m[1])
        e_sec = to_sec(m[2])
        text = m[3].replace("\n", " ").strip()
        items.append({"start": s_sec, "end": e_sec, "text": text})

    if not items:
        return []

    # Merge standalone countdown markers ("Number 5:", "Number 4:", etc.) directly into the next topic sentence
    merged_items = []
    i = 0
    countdown_regex = re.compile(r"^(?:Number|#|No\.)\s*\d+[\.:]?$", re.IGNORECASE)
    while i < len(items):
        item = items[i]
        text_clean = item["text"].strip()
        if countdown_regex.match(text_clean) and (i + 1) < len(items):
            next_item = items[i + 1]
            merged_items.append({
                "start": item["start"],
                "end": next_item["end"],
                "text": f"{text_clean} {next_item['text']}"
            })
            i += 2
        else:
            merged_items.append(item)
            i += 1
    items = merged_items

    scenes = []
    curr = None
    for item in items:
        if curr is None:
            curr = {"start": item["start"], "end": item["end"], "text": item["text"]}
        else:
            cand_dur = item["end"] - curr["start"]
            if cand_dur <= max_dur and (curr["end"] - curr["start"] < min_dur or not curr["text"].endswith((".", "!", "?"))):
                curr["end"] = item["end"]
                curr["text"] += " " + item["text"]
            else:
                scenes.append(curr)
                curr = {"start": item["start"], "end": item["end"], "text": item["text"]}
    if curr:
        scenes.append(curr)

    # Adjust contiguous boundaries to cover 0.0 -> audio_duration with zero micro-gaps
    for i in range(len(scenes)):
        if i == 0:
            scenes[i]["start"] = 0.0
        else:
            scenes[i]["start"] = scenes[i-1]["end"]

        if i == len(scenes) - 1:
            scenes[i]["end"] = max(scenes[i]["start"] + 1.0, float(audio_duration))
        else:
            next_start = items[min(len(items)-1, i+1)]["start"]
            if next_start > scenes[i]["end"]:
                scenes[i]["end"] = next_start
        scenes[i]["duration"] = round(scenes[i]["end"] - scenes[i]["start"], 3)

    return scenes


def _slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9\s_-]", "", text).strip()
    return re.sub(r"[\s_-]+", "_", clean).lower()


def align_photos_to_scenes(sentence_scenes: list[dict], selected_photos: list[dict], task_dir: str):
    """
    Aligns pre-selected archival candidate photos strictly to scenes based on script item numbers.
    Rotates secondary photos per item if an item spans multiple scenes to guarantee visual diversity.
    """
    if not selected_photos or not sentence_scenes:
        return

    import shutil

    # Group all selected photos by item_number: item_map[num] = [photo1, photo2, ...]
    item_map = {}
    for p in selected_photos:
        num = p.get("item_number")
        if num is not None and 1 <= num <= 5:
            if num not in item_map:
                item_map[num] = []
            item_map[num].append(p)

    # Fallback map by index if item_number tag was absent
    if not item_map:
        for idx, p in enumerate(selected_photos[:5]):
            count_num = 5 - idx
            item_map[count_num] = [p]

    word_num_map = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}

    def extract_item_number(scene_text: str, prev_scene_text: str = ''):
        # 1. Direct match: 'Number 5', 'Item 5', 'No. 5', '#5', 'Number Five', etc.
        m = re.search(r'\b(?:Number|Item|#|No\.)\s*(?:(\d+)|(one|two|three|four|five))\b', scene_text, re.IGNORECASE)
        if m:
            if m.group(1):
                return int(m.group(1))
            elif m.group(2):
                return word_num_map[m.group(2).lower()]

        # 2. Split boundary match: previous scene ended with 'Number' and current scene starts with number
        if prev_scene_text and re.search(r'\b(?:Number|Item|#|No\.)\s*$', prev_scene_text, re.IGNORECASE):
            m2 = re.search(r'^\s*(?:(\d+)|(one|two|three|four|five))\b', scene_text, re.IGNORECASE)
            if m2:
                if m2.group(1):
                    return int(m2.group(1))
                elif m2.group(2):
                    return word_num_map[m2.group(2).lower()]

        # 3. Scene starting with item number e.g. '1 Nazi book burning...', '5 Five Bell...'
        m3 = re.search(r'^\s*(?:(\d+)|(one|two|three|four|five))[\s\.:]', scene_text, re.IGNORECASE)
        if m3:
            n = int(m3.group(1)) if m3.group(1) else word_num_map[m3.group(2).lower()]
            if 1 <= n <= 5:
                return n

        return None

    current_item_num = 5  # Default to Item 5 for opening hook
    item_usage_counts = {}

    for idx, scene in enumerate(sentence_scenes):
        scene_text = scene.get("text", "")
        prev_text = sentence_scenes[idx - 1].get("text", "") if idx > 0 else ""
        
        parsed_num = extract_item_number(scene_text, prev_text)
        if parsed_num and 1 <= parsed_num <= 5:
            current_item_num = parsed_num

        candidates_for_item = item_map.get(current_item_num, [])
        if not candidates_for_item:
            # Zero-Item-5-Leak Guard: never fall back to Item 5's photo if current_item_num != 5!
            distinct_cands = [p for p in selected_photos if p.get("item_number") != 5]
            candidates_for_item = distinct_cands if distinct_cands else [selected_photos[0]]

        # Rotate candidate photos for the same item across multi-sentence scenes
        use_count = item_usage_counts.get(current_item_num, 0)
        photo = candidates_for_item[use_count % len(candidates_for_item)]
        item_usage_counts[current_item_num] = use_count + 1

        src_p = photo.get("local_path")
        dst_p = os.path.join(task_dir, f"scene_art_{idx+1}.jpg")
        if src_p and os.path.exists(src_p):
            shutil.copy2(src_p, dst_p)
            s_start = scene.get("start", 0.0)
            s_end = scene.get("end", 0.0)
            print(f"  📸 [PhotoAligner] Scene {idx+1}/{len(sentence_scenes)} ({s_start:.1f}s - {s_end:.1f}s) -> Item #{current_item_num} Photo ({use_count+1}/{len(candidates_for_item)}): '{photo.get('title', '')[:45]}...'")
            logger.info(f"[PhotoAligner] Scene {idx+1} ('{scene_text[:30]}...') -> Item #{current_item_num} Photo ({use_count+1}/{len(candidates_for_item)}): '{photo.get('title', '')}'")

from core.comfy_client import ComfyClient
from core.motion import image_to_cinematic_clip
from core.profile_loader import load_profile
from core.researcher import fetch_topic_research
from core.visual_fetcher import VisualFetcher


ELEVENLABS_VOICE_CATALOG = {
    "elevenlabs:JBFqnCBsd6RMkjVDRZzb:George": "warm, captivating British storyteller, rich narrative male (Best for: ancient ruins, forbidden archaeology, ancient mysteries)",
    "elevenlabs:nPczCjzI2devNBz1zQrb:Brian": "deep, resonant, comforting and solemn male (Best for: dark history, forbidden artifacts, taboos, deep secrets)",
    "elevenlabs:IKne3meq5aSn9XLyUdCD:Charlie": "deep, confident, energetic male (Best for: deep sea anomalies, abyss monsters, oceanic dread)",
    "elevenlabs:onwK4e9ZLuTAKqWW03F9:Daniel": "steady, formal British broadcaster male (Best for: out-of-place artifacts, lost cities, scholarly investigations)",
    "elevenlabs:pNInz6obpgDQGcFmaJgB:Adam": "dominant, firm, commanding male (Best for: military black ops, declassified projects, nuclear secrets)",
    "elevenlabs:CwhRBWXzGAHq8TQ4Fs17:Roger": "laid-back, casual, resonant male (Best for: bank heists, diamond center breaches, mastermind crimes)",
    "elevenlabs:pFZP5JQG7iQjIQuC4Bku:Lily": "velvety, confident British actress female (Best for: mythology, gods, ancient legends, epic folklore)",
    "elevenlabs:SOYHLrjzK2X1ezoPC6cr:Harry": "fierce, rough warrior male (Best for: prehistoric beasts, apex predators, extinction catastrophes)",
    "elevenlabs:EXAVITQu4vr4xnSDxMaL:Sarah": "mature, reassuring, confident female (Best for: psychological experiments, mind control, MKUltra)",
    "elevenlabs:pqHfZKP75CvOlQylNhV4:Bill": "wise, mature, crisp old narrator male (Best for: space anomalies, cosmic voids, deep space signals)",
    "elevenlabs:TX3LPaxmHKxFdv7VOQHJ:Liam": "energetic, confident social media male (Best for: cutting-edge tech, AI dominance, silicon labs)",
    "elevenlabs:N2lVS1w4EtoT3dr4eOWO:Callum": "husky, intense trickster male (Best for: true crime cold cases, DB Cooper, unsolved disappearances)",
    "elevenlabs:Xb7hH8MSUJpSbSDYk0k2:Alice": "clear, engaging British educator female (Best for: Dyatlov Pass, mysterious phenomena, unsolved puzzles)",
    "elevenlabs:hpp4J3VqNfWAUOO0d1Us:Bella": "professional, bright, warm female (Best for: disaster simulations, apocalyptic what-if scenarios)",
}

EDGE_VOICE_CATALOG = {
    "en-US-ChristopherNeural": "authoritative, solemn, deep cosmic documentary male (Best for: deep space anomalies, cosmic voids, primordial megafauna, extinction events)",
    "en-GB-RyanNeural": "cinematic, refined, chilling British storytelling male (Best for: ancient ruins, forbidden archaeology, cataclysms, historical mysteries)",
    "en-US-GuyNeural": "gritty, grounded, sharp true-crime investigative male (Best for: true crime cold cases, bank heists, FBI investigations)",
    "en-US-BrianNeural": "measured, gripping, mature investigative American male (Best for: dark psychology, declassified mind control, military black ops)",
    "en-US-AndrewNeural": "fast-paced, urgent, modern high-stakes male (Best for: cutting-edge tech, cyber espionage, drone warfare)",
    "en-GB-SoniaNeural": "atmospheric, eerie, classical British documentary female (Best for: mythological disasters, ancient relics, sunken temples)",
    "en-US-AriaNeural": "sharp, intense, mysterious investigative American female (Best for: psychological experiments, strange medical puzzles, unsolved disappearances)",
}

VOICE_CATALOG = ELEVENLABS_VOICE_CATALOG


def select_dynamic_voice(topic: str, script: str, profile: dict) -> str:
    """Dynamically casts the optimal narrator voice based on story tone and global config."""
    from app.services.voice import get_elevenlabs_api_key
    voice_cfg = profile.get("voice", {})
    global_provider = str(getattr(config, "tts_provider", "") or (config.get("tts_provider", "") if isinstance(config, dict) else "")).lower()

    if global_provider:
        provider = global_provider
    elif not get_elevenlabs_api_key():
        provider = "edge"
    else:
        provider = voice_cfg.get("provider", "elevenlabs").lower()

    configured = voice_cfg.get("voice_name")
    if configured and not voice_cfg.get("dynamic", False) and provider == voice_cfg.get("provider", "elevenlabs").lower():
        return configured

    if provider == "edge":
        catalog = EDGE_VOICE_CATALOG
        default_fallback = configured or "en-US-ChristopherNeural"
    else:
        catalog = ELEVENLABS_VOICE_CATALOG
        default_fallback = configured or "elevenlabs:JBFqnCBsd6RMkjVDRZzb:George"

    catalog_desc = "\n".join([f"- {v}: {desc}" for v, desc in catalog.items()])
    prompt = f"""You are the Executive Audio Casting Director for viral documentary shorts.
Analyze this video topic and script, and select the single best narrator voice from the catalog below to maximize suspense and viewer retention.

Catalog:
{catalog_desc}

Topic: "{topic}"
Script excerpt: "{script[:250]}..."

Return ONLY the exact voice identifier string (e.g. "{list(catalog.keys())[0]}"). No punctuation, no explanation."""

    app_cfg = _get_llm_config(profile)
    try:
        chosen = llm._generate_response(prompt, app_config=app_cfg)
        if chosen:
            chosen = chosen.strip().replace('"', '').replace("'", "")
            for v in catalog:
                if v.lower() in chosen.lower() or v.split(":")[-1].lower() in chosen.lower():
                    logger.info(f"[Casting Director] Selected voice: {v} for '{topic}'")
                    return v
    except Exception as e:
        logger.warning(f"[Casting Director] Voice selection fallback: {e}")

    return default_fallback


def build_system_script_prompt(profile: dict, topic: str, research_context: str = "", selected_photos: list = None) -> str:
    persona = profile.get("persona", {})
    banned = persona.get("banned_phrases", [])
    banned_str = ", ".join(f'"{p}"' for p in banned) if banned else "None"

    target_cfg = profile.get("video_target", {})
    min_w = target_cfg.get("min_words", 65)
    max_w = target_cfg.get("max_words", 90)

    context_block = f"\n## Verified Archival Evidence & Intel:\n{research_context}\n" if research_context else ""

    is_rarely_seen = "rarely_seen" in profile.get("niche", {}).get("slug", "").lower() or any(k in topic.lower() for k in ["5 ", "images", "forbidden", "anomalies", "microscope", "landmarks"])

    if is_rarely_seen:
        photos_block = ""
        if selected_photos and len(selected_photos) >= 5:
            photos_block = "\n## VERIFIED 5 CANDIDATE PHOTOGRAPHS (PRE-SAVED ON DISK):\n"
            for i, p in enumerate(selected_photos[:5]):
                count_num = 5 - i
                photos_block += f"Photo {count_num} (for Number {count_num}:): {p.get('title', '')} — {p.get('caption', '')}\n"

        return f"""# Role: Fast-Paced Curated Image Showcase Screenwriter
# Niche: {profile['niche']['name']} ({profile['niche']['description']})
# Subject: {topic}
{context_block}{photos_block}

## FORMAT MANDATE (COUNTDOWN SLIDESHOW SHOWCASE):
You are writing a fast-paced Short video script (TikTok/Shorts style).
The script MUST present a countdown of 5 COMPLETELY DIFFERENT, jaw-dropping, rare photos counting down from Number 5 to Number 1.

CRITICAL PHOTO NARRATION MANDATE:
- Your narration MUST directly describe the 5 verified photographs listed above saved on disk in order (Number 5 down to Number 1).
- Do NOT invent fictitious photos, fake dates, or unverified events not present in the supplied photo metadata.
- Each item (Number 5 down to Number 1) MUST be a completely DIFFERENT photo subject!

STRUCTURE:
1. Opening Headline (First 5 words): MUST open directly with "Rare photos you've never seen before:"
2. Countdown Items (Number 5 down to Number 1):
   - Start each item explicitly with "Number 5:", "Number 4:", "Number 3:", "Number 2:", "Number 1:".
   - State a quick, punchy 1 to 2 sentence fact directly describing what is shown in that photo (10 to 15 words per photo).
   - End with: "Which photo surprised you most?"

## ABSOLUTE CONSTRAINTS:
- Total Spoken Word Count: Strictly between {min_w} and {max_w} words (~25 to 30 seconds spoken audio).
- Banned Clichés: Absolutely NEVER use {banned_str} or generic channel intros.
- Spoken Audio Only: Zero markdown asterisks, no headers, no quotation marks, no narrator tags, no bracketed notes.
"""

    return f"""# Role: Master Investigative Documentarian & Cinematic Short-Form Screenwriter
# Niche: {profile['niche']['name']} ({profile['niche']['description']})
# Subject: {topic}
{context_block}

## FACTUAL GROUNDING MANDATE:
Every narrative assertion, metric, and chronological anchor must be grounded in the Verified Archival Evidence above.
Never invent fictional expeditions, fabricated artifacts, or synthetic controversies. Ground all mystery and tension in documented physical reality.
If a claim is disputed or fringe, frame it clearly ("Some researchers believe...", "Claims surfaced that...").

## THE RETENTION BLUEPRINT (50-60 Seconds Spoken):
1. THE HOOK (First 3-5 Seconds):
   - Formula: [Specific Fact] + [Physical Anomaly] + [Unanswered Question].
   - FORBIDDEN TO START WITH DATES OR LOCATIONS: Never open with "August 15th, 1977", "In 1997", "June 2011", or "Point Nemo".
   - Open directly with the anomaly that makes the viewer say "Wait, how is that possible?".

2. THE "BUT" ESCALATION RULE:
   - Structure the story as a sequence of contradictions.

3. SENTENCE RHYTHM (CRITICAL FOR NATURAL AUDIO):
   - Write for spoken human narration.
   - Mix flowing 12-to-18 word narrative sentences with short, impactful 4-to-6 word punchlines.

4. THE CIRCULAR CALLBACK (Ending):
   - Do not force a fake resolution. The strongest ending is an unsettling unanswered contradiction that loops back to the original mystery question.

## ABSOLUTE CONSTRAINTS:
- Exact Spoken Word Count: Strictly between {min_w} and {max_w} words (calibrated for strictly 62 to 75 seconds of high-retention narration).
- Banned Clichés: Absolutely NEVER use {banned_str} or phrases like "in this video", "have you ever wondered", "dive into", "let's explore".
- Spoken Audio Only: Zero markdown asterisks, no headers, no quotation marks, no narrator tags, no bracketed notes.
"""


def _get_llm_config(profile: dict) -> dict:
    app_cfg = dict(config.app)
    llm_cfg = profile.get("llm", {}) if profile else {}
    provider = (llm_cfg.get("provider") or app_cfg.get("llm_provider", "deepseek")).lower()
    if provider in ("elevenlabs", "edge", "azure", "edge-tts"):
        provider = (app_cfg.get("llm_provider") or "deepseek").lower()
    model = llm_cfg.get("model") or app_cfg.get(f"{provider}_model_name", "deepseek-chat")
    app_cfg["llm_provider"] = provider
    if provider == "ollama":
        app_cfg["ollama_model_name"] = model
        if not app_cfg.get("ollama_base_url"):
            app_cfg["ollama_base_url"] = "http://127.0.0.1:11434/v1"
    else:
        app_cfg[f"{provider}_model_name"] = model
    return app_cfg


def generate_niche_script(profile: dict, topic: str, research_context: str = "", selected_photos: list = None) -> str:
    """Generate script adhering strictly to niche persona and researched facts."""
    prompt = build_system_script_prompt(profile, topic, research_context, selected_photos=selected_photos)
    app_cfg = _get_llm_config(profile)
    provider = app_cfg.get("llm_provider", "deepseek")
    model_name = app_cfg.get(f"{provider}_model_name") or app_cfg.get("ollama_model_name", "deepseek-chat")
    response = None
    for attempt in range(1, 4):
        try:
            response = llm._generate_response(prompt, app_config=app_cfg)
            if response and not response.startswith("Error:"):
                break
            logger.warning(f"Script generation attempt {attempt}/3 returned: {response}. Retrying in 2s...")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Script generation attempt {attempt}/3 failed: {e}. Retrying in 2s...")
            time.sleep(2)

    # Local Ollama fallback if cloud provider is temporarily unreachable
    if not response or response.startswith("Error:"):
        logger.warning(f"External LLM unreachable after 3 attempts. Falling back to local Ollama (qwen3-coder:30b)...")
        fallback_cfg = dict(app_cfg)
        fallback_cfg["llm_provider"] = "ollama"
        fallback_cfg["ollama_model_name"] = "qwen3-coder:30b"
        fallback_cfg["ollama_base_url"] = "http://127.0.0.1:11434/v1"
        try:
            response = llm._generate_response(prompt, app_config=fallback_cfg)
        except Exception as e:
            logger.warning(f"Ollama fallback failed: {e}")

    if not response or response.startswith("Error:"):
        raise RuntimeError(f"Script generation failed after retries and fallback: {response}")

    script = response.strip()
    script = re.sub(r'\[.*?\]|\(.*?\)', '', script)
    script = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', script)
    script = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', script, flags=re.IGNORECASE)
    script = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', script, flags=re.IGNORECASE)

    target_cfg = profile.get("video_target", {})
    min_w = target_cfg.get("min_words", 170)
    max_w = target_cfg.get("max_words", 195)
    word_count = len(script.split())

    # Mandatory expansion loop: enforce strictly 62-75 second duration
    if word_count < min_w:
        logger.info(f"Draft script is only {word_count} words. Auto-adjusting to target {min_w}-{max_w} words...")
        expand_prompt = f"""You are a High-Retention Short Video Narrator. The draft script below is only {word_count} words.
Adjust this script to strictly between {min_w} and {max_w} words while maintaining its fast-paced micro-caption showcase format starting with "Rare photos you've never seen before."

Draft Script:
\"{script}\"

Return ONLY the final expanded script to be read aloud. No labels, no headers, no word counts."""
        expanded_resp = llm._generate_response(expand_prompt, app_config=app_cfg)
        if expanded_resp and not expanded_resp.startswith("Error:"):
            exp_clean = re.sub(r'\[.*?\]|\(.*?\)', '', expanded_resp)
            exp_clean = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', exp_clean)
            exp_clean = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', exp_clean, flags=re.IGNORECASE)
            exp_clean = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', exp_clean, flags=re.IGNORECASE)
            script = exp_clean.strip()

    return script.strip()


def generate_visual_terms(script: str, topic: str, profile: dict) -> list[dict]:
    """Generate high-precision search queries and hybrid generation routes anchored to the topic."""
    prompt = f"""Given this documentary video script about '{topic}', break it down into 6 to 8 sequential visual scene beats.
For each beat, categorize it as:
- "stock": for filmable real-world footage (aerial landscapes, drone shots, buildings, factories, nature, city streets, machinery).
- "generate": for unfilmable scenes that do NOT exist in stock footage (deep pitch-black subterranean ice caverns, microscopic extremophiles/bacteria, planetary surfaces of Europa/Mars, inside nanometer laser vacuum chambers, ancient lost tombs).

CRITICAL SEARCH & RELEVANCE RULES:
- EVERY query MUST be strictly relevant to '{topic}' and the actual beat described in the script.
- For "stock" beats, specify a cinematic motion search query and 1 to 2 "negative_terms" to explicitly avoid irrelevant misfires (e.g. for polar ice: negative_terms: ["fishing", "scuba", "beach"]; for semiconductor tech: negative_terms: ["food", "casino", "nature"]).
- For "generate" beats, write a descriptive photorealistic SDXL prompt set in '{topic}' (8k, volumetric lighting, national geographic, textless).

Script:
"{script}"

Return a JSON array of 6 to 8 objects where each object has:
- "query": descriptive cinematic query or SDXL prompt for '{topic}'
- "fallback": 1-2 word fallback term
- "type": "stock" or "generate"
- "negative_terms": list of 1 to 3 words to avoid (e.g. ["fishing", "beach"])

Return ONLY the raw JSON array. No explanations, no markdown formatting."""

    app_cfg = _get_llm_config(profile)
    response = llm._generate_response(prompt, app_config=app_cfg)
    
    items = []
    try:
        cleaned = response.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        parsed = json.loads(cleaned.strip())
        if isinstance(parsed, list):
            items = parsed
    except Exception as e:
        logger.debug(f"JSON visual beat parsing fallback: {e}")

    if not items:
        terms = [t.strip().strip('"').strip("'") for t in response.split(",") if t.strip()]
        items = [{"query": t, "fallback": topic, "type": "stock", "negative_terms": []} for t in terms[:8]]

    # Post-process: Guarantee every query is anchored to the topic domain
    anchored_beats = []
    topic_clean = topic.strip()
    topic_words = set(topic_clean.lower().split())

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_q = re.sub(r'["\']', '', item.get("query", "")).strip()
        raw_fb = re.sub(r'["\']', '', item.get("fallback", topic_clean)).strip()
        beat_type = item.get("type", "stock").lower()
        if beat_type not in {"stock", "generate"}:
            beat_type = "stock"

        neg_terms = item.get("negative_terms", [])
        if not isinstance(neg_terms, list):
            neg_terms = []

        if not raw_q:
            continue

        q_words = set(raw_q.lower().split())
        # Anchor with topic if not already present
        if not (topic_words & q_words):
            anchored_q = f"{topic_clean} {raw_q}".strip()
        else:
            anchored_q = raw_q

        fb_words = set(raw_fb.lower().split())
        if not (topic_words & fb_words):
            anchored_fb = f"{topic_clean} {raw_fb}".strip()
        else:
            anchored_fb = raw_fb

        anchored_beats.append({
            "query": anchored_q,
            "fallback": anchored_fb,
            "type": beat_type,
            "negative_terms": [str(t).lower().strip() for t in neg_terms if str(t).strip()]
        })

    return anchored_beats if anchored_beats else [{"query": topic_clean, "fallback": topic_clean, "type": "stock", "negative_terms": []}]


def _disp_width(s: str) -> int:
    w = 0
    for c in s:
        if unicodedata.east_asian_width(c) in ('F', 'W') or ord(c) > 0x1F000:
            w += 2
        else:
            w += 1
    return w


def _center_text(s: str, total_width: int) -> str:
    sw = _disp_width(s)
    rem = max(0, total_width - sw)
    left = rem // 2
    right = rem - left
    return ' ' * left + s + ' ' * right


def print_stage_header(stage_idx: int, total_stages: int, title: str):
    header = f"[{stage_idx:02d}/{total_stages:02d}] {title.upper()}"
    W = 66
    content = header
    if _disp_width(content) > W - 2:
        content = content[:W - 5] + "..."
    rem = (W - 2) - _disp_width(content)
    print("\n╭" + "─" * W + "╮")
    print(f"│ {content}" + " " * max(0, rem) + " │")
    print("╰" + "─" * W + "╯")


def print_worker_panel(profile: dict, state: dict, is_resumed: bool = False):
    niche_name = profile.get("niche", {}).get("name", "Documentary")
    arc = profile.get("series", {}).get("current_arc", "Independent Arc")
    topic = state.get("topic", "Niche Production")
    ep_num = state.get("episode_num")
    ep_str = f"Episode {ep_num:02d}" if ep_num is not None else "Standalone Feature"

    app_cfg = _get_llm_config(profile)
    provider = app_cfg.get("llm_provider", "deepseek")
    model = app_cfg.get(f"{provider}_model_name", "deepseek-chat")
    voice_name = profile.get("voice", {}).get("voice_name", "en-US-ChristopherNeural")
    rate = profile.get("voice", {}).get("voice_rate", 1.12)
    stage = state.get("stage", "start")
    source = (profile.get("visual", {}).get("source") or config.app.get("video_source", "pexels")).upper()

    W = 66
    title_str = "📸 RARE STUDIO WORKER"

    print("\n╭" + "─" * W + "╮")
    print("│" + _center_text(title_str, W) + "│")
    print("├" + "─" * W + "┤")

    def row(label, val):
        content = f"{label:<13} {val}"
        if _disp_width(content) > W - 2:
            content = content[:W - 5] + "..."
        rem = (W - 2) - _disp_width(content)
        return f"│ {content}" + " " * max(0, rem) + " │"

    print(row("Target:", niche_name))
    print(row("Series Arc:", arc))
    print(row("Episode:", ep_str))
    print(row("Subject:", topic))
    print(row("Model:", f"{model} ({provider})"))
    print(row("Director:", "Unified Movie Director (3s Beats)"))
    print(row("Voice:", f"{voice_name} ({rate}x)"))
    print(row("Visuals:", "WIKIMEDIA / WEB ARCHIVES HD | ComfyUI SDXL"))
    print(row("Compositor:", "Pure FFmpeg NVENC (Hardware Accel)"))
    status_str = f"Resuming checkpoint: {stage}" if is_resumed else f"Fresh Production ({stage})"
    print(row("Status:", status_str))
    print("╰" + "─" * W + "╯\n")


def print_production_scorecard(metadata: dict, reg_stats: dict, dest_mp4: Path, meta_path: Path):
    W = 74
    title = "🎬 EPISODE PRODUCTION REPORT"
    word_count = len(metadata["script"].split())
    dur = metadata["duration_seconds"]
    size = metadata["file_size_mb"]

    print("\n╭" + "─" * W + "╮")
    print("│" + _center_text(title, W) + "│")
    print("├" + "─" * W + "┤")

    def row(label, val):
        content = f"{label:<17} {val}"
        if _disp_width(content) > W - 2:
            content = content[:W - 5] + "..."
        rem = (W - 2) - _disp_width(content)
        return f"│ {content}" + " " * max(0, rem) + " │"

    print(row("Title:", metadata["title"]))
    print(row("Subject:", metadata["topic"]))
    print(row("Niche:", metadata["niche"].upper()))
    print(row("Duration:", f"{dur}s ({word_count} words @ 1.12x rate)"))
    print(row("Video Codec:", "Pure FFmpeg NVENC (Hardware Accelerated)"))
    print(row("Asset Registry:", f"{reg_stats.get('total_assets', 0)} total | {reg_stats.get('fresh_assets', 0)} fresh | {reg_stats.get('reused_assets', 0)} ranked reuse"))
    print(row("File Size:", f"{size} MB"))
    print(row("Output Video:", str(dest_mp4)))
    print(row("Thumbnail:", metadata["thumbnail_file"]))
    print(row("Metadata JSON:", str(meta_path)))
    print("╰" + "─" * W + "╯\n")


def ensure_stealth_scraper_running():
    """Spins up persistent stealth Playwright browser microservice on port 4050 if not running."""
    import requests
    import subprocess
    import time
    try:
        r = requests.get("http://127.0.0.1:4050/health", timeout=1.5)
        if r.status_code == 200:
            return
    except Exception:
        pass
    print("  🤖 [Stealth Browser] Launching persistent Playwright Stealth microservice (port 4050)...")
    server_script = os.path.join(PROJECT_ROOT, "services", "scraper", "server.js")
    if os.path.exists(server_script):
        subprocess.Popen(["node", server_script], cwd=os.path.dirname(server_script))
        for _ in range(15):
            time.sleep(1)
            try:
                r = requests.get("http://127.0.0.1:4050/health", timeout=1.5)
                if r.status_code == 200:
                    print("  ✓ [Stealth Browser] Persistent stealth browser context online & ready.")
                    return
            except Exception:
                pass


def shutdown_stealth_scraper():
    """Shutdown persistent stealth Playwright browser context once video generation completes."""
    import requests
    try:
        requests.get("http://127.0.0.1:4050/shutdown", timeout=3.0)
        print("  ✓ [Stealth Browser] Browser context cleanly closed.")
    except Exception:
        pass


def run_worker_pipeline(profile_path: str, topic_override: str = None, clear_state: bool = False, episode_num: int = None):
    try:
        ensure_stealth_scraper_running()
    except Exception as e:
        logger.warning(f"[Worker] Stealth scraper launch warning: {e}")

    try:
        from runners.gallery import ensure_gallery_server_running
        ensure_gallery_server_running()
    except Exception:
        pass

    profile = load_profile(profile_path)
    niche_slug = profile["niche"]["slug"]
    
    checkpoint_file = f"pipeline_state_{niche_slug}.json"
    checkpoint = CheckpointManager(checkpoint_file)

    if clear_state:
        print(f"[Worker: {niche_slug}] Clearing existing state for a fresh session.")
        checkpoint.clear()

    state = checkpoint.load()
    is_resumed = bool(state and state.get("stage") != "completed")
    if not is_resumed:
        task_id = str(uuid4())
        
        # Dynamically brainstorm a brand-new viral topic if no explicit topic was provided
        final_topic = topic_override
        if not final_topic:
            try:
                from runners.series import get_niche_archive_info, generate_dynamic_niche_topic
                existing_topics, _ = get_niche_archive_info(niche_slug)
                final_topic = generate_dynamic_niche_topic(profile, list(existing_topics.keys()))
            except Exception as e:
                logger.warning(f"[Worker] Dynamic topic generation error: {e}")
                final_topic = profile.get("series", {}).get("current_arc") or f"5 Rare Photos You've Never Seen Before"

        state = {
            "task_id": task_id,
            "niche": niche_slug,
            "stage": "start",
            "status": "running",
            "topic": final_topic,
            "episode_num": episode_num,
            "created_at": time.time(),
        }
        checkpoint.save(state)

    print_worker_panel(profile, state, is_resumed=is_resumed)
    if is_resumed:
        print(f"Resuming saved pipeline state from stage: {state.get('stage')}\n")

    task_id = state["task_id"]
    task_dir = utils.task_dir(task_id)

    try:
        # -------------------------------------------------------------
        # STAGE 1: Research, Topic & Grounded Script
        # -------------------------------------------------------------
        if state["stage"] in {"start", "script_generating"}:
            print_stage_header(1, 6, f"Researching & Grounding: '{state['topic']}'")
            
            is_photo_niche = "rarely_seen" in niche_slug.lower() or profile.get("visual", {}).get("mode") == "photo"

            # Step 1A: Photo-First Harvester from Curated Unwatermarked Articles
            selected_photos = state.get("selected_photos")
            candidates = state.get("candidate_pool")
            candidates_dir = os.path.join(task_dir, "candidates")
            
            if is_photo_niche and not candidates:
                from app.services.archival_crawler import harvest_candidates_sync
                print(f"  📸 Archival Crawler: Harvesting photo candidate pool to '{candidates_dir}'...")
                candidates = harvest_candidates_sync(state["topic"], candidates_dir, target_count=15)
                state["candidate_pool"] = candidates
                checkpoint.save(state)

            if is_photo_niche and not selected_photos:
                from core.photo_ranker import PhotoRanker
                ranking_json_path = os.path.join(task_dir, "candidates_ranking.json")
                ranker = PhotoRanker(candidates or [], state["topic"])
                selected_photos = ranker.rank_candidates(output_ranking_json=ranking_json_path)

                # Ensure 5 distinct items with verified local paths
                if len(selected_photos) < 5 and candidates:
                    for c in candidates:
                        if c not in selected_photos and c.get("local_path"):
                            selected_photos.append(dict(c))
                        if len(selected_photos) >= 5:
                            break

                state["selected_photos"] = selected_photos
                checkpoint.save(state)

                if selected_photos:
                    print(f"  ✓ Selected {len(selected_photos)} verified un-watermarked archival photos:")
                    for p in selected_photos:
                        print(f"     • Item #{p.get('item_number')}: '{p.get('title', '')[:50]}...'")

            # Step 1B: Build five_items metadata directly from downloaded article photos
            five_items = state.get("five_items")
            if is_photo_niche and not five_items:
                five_items = []
                for idx, p in enumerate(selected_photos[:5]):
                    five_items.append({
                        "item_number": p.get("item_number", 5 - idx),
                        "title": p.get("title", f"Item #{5-idx}"),
                        "description": p.get("backstory") or p.get("caption") or p.get("title", ""),
                        "search_query": p.get("title", "")
                    })
                state["five_items"] = five_items
                checkpoint.save(state)

            # Step 1C: Build Evidence Pack from authentic article backstories
            research_data = state.get("research")
            if not research_data:
                if is_photo_niche and five_items:
                    item_titles = [it.get("title", "") for it in five_items]
                    summary_context = "\n".join([f"Item {it.get('item_number')}: {it.get('title')} - Backstory: {it.get('description')}" for it in five_items])
                    research_data = {
                        "topic": state["topic"],
                        "title": f"5 Mind-Blowing Archival Photos ({', '.join(item_titles[:2])})",
                        "summary": f"Curated archival documentary featuring: {', '.join(item_titles)}.",
                        "verified_claims": [it.get("description", "") for it in five_items],
                        "context": summary_context,
                        "viral_hooks": [f"Why this photo of {item_titles[0]} left people speechless"]
                    }
                else:
                    research_data = fetch_topic_research(state["topic"])
                state["research"] = research_data
                checkpoint.save(state)

                # Register selected archival photos into persistent AssetRegistry to guarantee zero cross-draft reuse
                from core.asset_registry import registry
                for p in selected_photos:
                    u = p.get("source_url") or p.get("image_url", "")
                    t = p.get("title", "")
                    p_path = p.get("local_path", "")
                    if u:
                        registry.register_asset(u, provider="web_photo", media_type="image", topic=state["topic"], niche=niche_slug, episode_id=task_id)
                    if t:
                        registry.register_asset(t, provider="photo_title", media_type="image", topic=state["topic"], niche=niche_slug, episode_id=task_id)
                    if p_path and os.path.exists(p_path):
                        try:
                            import hashlib
                            with open(p_path, "rb") as _f:
                                _h = hashlib.md5(_f.read()).hexdigest()
                                registry.register_asset(_h, provider="photo_hash", media_type="image", topic=state["topic"], niche=niche_slug, episode_id=task_id)
                        except Exception:
                            pass



            # Step 1C: Generate grounded script, viral title, and thumbnail hook
            # Free ComfyUI models from VRAM before invoking LLM
            try:
                import requests as _req
                _req.post("http://127.0.0.1:8188/free", json={"unload_models": True, "free_memory": True}, timeout=3)
            except Exception as e:
                logger.debug(f"ComfyUI VRAM release attempt: {e}")

            script = state.get("script")
            if not script:
                script = generate_niche_script(
                    profile,
                    state["topic"],
                    research_context=research_data.get("context", ""),
                    selected_photos=selected_photos
                )
                
                # Generate viral title & thumbnail concept
                from core.thumbnail_generator import generate_title_and_thumbnail_concepts
                viral_hooks = research_data.get("viral_hooks", []) if isinstance(research_data, dict) else getattr(research_data, "viral_hooks", [])
                thumb_concept = generate_title_and_thumbnail_concepts(script, state["topic"], profile, viral_hooks=viral_hooks)

                state.update({
                    "script": script,
                    "title": thumb_concept.get("title", f"The Mystery of {state['topic']}"),
                    "thumbnail_text": thumb_concept.get("thumbnail_text", "THEY HID THIS"),
                    "thumbnail_prompt": thumb_concept.get("thumbnail_prompt", f"dramatic cinematic shot of {state['topic']}"),
                    "stage": "script_generated"
                })
                checkpoint.save(state)

            print(f"  ✓ Researched: '{research_data.get('title', state['topic'])}'")
            if selected_photos:
                print(f"  ✓ Photo-First: Selected Top {len(selected_photos)} verified archival photos (Report: candidates_ranking.json)")
            print(f"  ✓ Script ready ({len(state['script'].split())} words)")

        # -------------------------------------------------------------
        # STAGE 2: Voice Narration (Edge TTS with AI Dynamic Casting)
        # -------------------------------------------------------------
        if state["stage"] in {"script_generated", "audio_generating"}:
            print_stage_header(2, 6, "Synthesizing Narration & Dynamic Voice Casting")
            audio_file = os.path.join(task_dir, "audio.mp3")
            voice_config = profile.get("voice", {})
            voice_name = select_dynamic_voice(state["topic"], state["script"], profile)
            voice_rate = float(voice_config.get("voice_rate", 1.12))

            # Ensure 100% clean script before voice synthesis (zero bracketed metrics or word counts)
            clean_script = re.sub(r'\[.*?\]|\(.*?\)', '', state["script"]).strip()
            clean_script = re.sub(r'(?i)\bword\s*count\s*:\s*\d+\b', '', clean_script).strip()
            clean_script = re.sub(r'\*\*(?:Narrator|Voiceover|Audio|Host)\s*:\*\*', '', clean_script, flags=re.IGNORECASE).strip()
            clean_script = re.sub(r'(?:Narrator|Voiceover|Audio|Host)\s*:\s*', '', clean_script, flags=re.IGNORECASE).strip()
            state["script"] = clean_script

            print(f"  🎙️ Cast Narrator Voice: {voice_name} (Rate: {voice_rate}x)")
            sub_maker = voice.tts(
                text=clean_script,
                voice_name=voice_name,
                voice_rate=voice_rate,
                voice_file=audio_file,
            )
            if not os.path.exists(audio_file):
                raise RuntimeError("Failed to synthesize narration audio.")

            duration = voice.get_audio_duration(audio_file)
            audio_duration = math.ceil(duration)
            if audio_duration <= 0:
                raise RuntimeError("Generated narration audio has 0s duration.")

            state.update({
                "audio_file": audio_file,
                "audio_duration": audio_duration,
                "voice_name": voice_name,
                "voice_rate": voice_rate,
                "stage": "audio_generated"
            })
            checkpoint.save(state)
            print(f"  ✓ Narration synthesized: {audio_duration}s ({audio_file})")

        # -------------------------------------------------------------
        # STAGE 3: Subtitles Alignment
        # -------------------------------------------------------------
        if state["stage"] in {"audio_generated", "subtitle_generating"}:
            print_stage_header(3, 6, "Word-Aligned Subtitles & Karaoke Highlighting")
            subtitle_path = os.path.join(task_dir, "subtitle.srt")
            active_voice = state.get("voice_name", profile.get("voice", {}).get("voice_name", "en-US-ChristopherNeural"))
            active_rate = float(state.get("voice_rate", profile.get("voice", {}).get("voice_rate", 1.12)))
            
            # Re-generate sub_maker for exact cues
            sub_maker = voice.tts(
                text=state["script"],
                voice_name=active_voice,
                voice_rate=active_rate,
                voice_file=state["audio_file"],
            )
            voice.create_subtitle(text=state["script"], sub_maker=sub_maker, subtitle_file=subtitle_path)
            
            # Export word-level cues for mature bold karaoke synchronization
            word_cues = []
            if hasattr(sub_maker, "cues") and sub_maker.cues:
                for c in sub_maker.cues:
                    word_cues.append({
                        "word": str(c.content).strip(),
                        "start": float(c.start.total_seconds()),
                        "end": float(c.end.total_seconds())
                    })
            if not word_cues and os.path.exists(subtitle_path):
                word_cues = srt_to_word_cues(subtitle_path)
            karaoke_json_file = os.path.join(task_dir, "karaoke.json")
            with open(karaoke_json_file, "w", encoding="utf-8") as kf:
                json.dump(word_cues, kf, indent=2)
            print(f"  ✓ Extracted {len(word_cues)} word-level cues for mature bold karaoke highlighting.")

            if not os.path.exists(subtitle_path):
                raise RuntimeError("Failed to generate subtitle track.")

            state.update({
                "subtitle_path": subtitle_path,
                "karaoke_path": karaoke_json_file,
                "stage": "subtitle_generated"
            })
            checkpoint.save(state)
            print(f"  ✓ Subtitles ready ({subtitle_path})")

        # -------------------------------------------------------------
        # STAGE 4: AI Movie Director — Storyboard & Visual Harvesting
        # -------------------------------------------------------------
        if state["stage"] in {"subtitle_generated", "materials_sourcing"}:
            print_stage_header(4, 6, "AI Movie Director Storyboard & Asset Harvesting")
            from core.cinema_engine import get_sentence_scenes, harvest_unique_timeline
            from core.director import MovieDirector, DirectorShot
            import dataclasses

            audio_duration = float(state["audio_duration"])
            is_photo_niche = (profile.get("visual", {}).get("mode") == "photo" or "rarely_seen" in profile.get("niche", {}).get("slug", "").lower())
            if is_photo_niche:
                sentence_scenes = get_sentence_scenes(state["subtitle_path"], audio_duration, min_dur=2.5, max_dur=4.0)
            else:
                sentence_scenes = get_sentence_scenes(state["subtitle_path"], audio_duration)
            needed_clips = len(sentence_scenes) if sentence_scenes else max(18, math.ceil(audio_duration / 2.8))
            print(f"  🎬 Cinema Director: Planning {needed_clips} unique scenes with frame-accurate speech alignment.")

            director = MovieDirector(profile)
            cached_shots = state.get("director_shots")
            if cached_shots and isinstance(cached_shots, list):
                shots = [DirectorShot(**s) for s in cached_shots]
            else:
                shots = director.plan_shotlist(state["script"], state["topic"], needed_clips)
                state["director_shots"] = [dataclasses.asdict(s) for s in shots]
                checkpoint.save(state)

                # Free Ollama memory
                try:
                    import requests as _req
                    app_cfg = _get_llm_config(profile)
                    m_name = app_cfg.get("ollama_model_name", "qwen3:8b")
                    _req.post("http://127.0.0.1:11434/api/generate", json={"model": m_name, "keep_alive": 0}, timeout=5)
                except Exception as e:
                    logger.debug(f"Ollama VRAM release attempt: {e}")

            # Align pre-selected archival photos strictly to narrated item sections
            selected_photos = state.get("selected_photos", [])
            if selected_photos:
                align_photos_to_scenes(sentence_scenes, selected_photos, task_dir)

            cinematic_clips = harvest_unique_timeline(
                scenes=sentence_scenes,
                task_dir=task_dir,
                topic=state["topic"],
                profile=profile,
                director_shots=shots,
                episode_id=state.get("episode_id", state.get("task_id", ""))
            )

            if not cinematic_clips:
                raise RuntimeError("Failed to acquire authentic visual footage.")

            state.update({
                "materials": cinematic_clips,
                "stage": "materials_ready"
            })
            checkpoint.save(state)
            print(f"  ✓ Successfully produced {len(cinematic_clips)} 100% UNIQUE visual scene clips (ZERO repetition).")

        # -------------------------------------------------------------
        # STAGE 5: Hardware-Accelerated NVENC Compositing
        # -------------------------------------------------------------
        if state["stage"] in {"materials_ready", "video_rendering"}:
            print_stage_header(5, 6, "Pure FFmpeg NVENC Hardware Video Compositing")
            combined_video_path = os.path.join(task_dir, "combined.mp4")
            final_video_path = os.path.join(task_dir, "final.mp4")
            
            visual_cfg = profile.get("visual", {})
            aspect = VideoAspect(visual_cfg.get("aspect_ratio", "9:16"))
            aspect = VideoAspect(visual_cfg.get("aspect_ratio", "9:16"))
            
            # Step A: Hardware concatenate all unique clips via pure FFmpeg NVENC (Zero repetition)
            from core.cinema_engine import assemble_final_nvenc_video
            watermark_cfg = profile.get("watermark", {})
            assemble_final_nvenc_video(
                clip_files=state["materials"],
                audio_file=state["audio_file"],
                output_file=combined_video_path,
                watermark_text=watermark_cfg.get("text", "rare-studio"),
                watermark_enabled=watermark_cfg.get("enabled", True)
            )

            # Step B: Burn subtitles and mix audio
            subtitle_cfg = profile.get("subtitle", {})
            params = VideoParams(
                video_subject=state.get("topic", "Short Video"),
                video_aspect=aspect,
                font_name="Montserrat-Black.ttf",
                font_size=subtitle_cfg.get("font_size", 52),
                text_fore_color=subtitle_cfg.get("color", "#FFFFFF"),
                text_background_color=subtitle_cfg.get("text_background_color", "#000000"),
                rounded_subtitle_background=subtitle_cfg.get("rounded_subtitle_background", True),
                stroke_color="#000000",
                stroke_width=subtitle_cfg.get("stroke_width", 2),
                subtitle_position=subtitle_cfg.get("position", "custom"),
                custom_position=float(subtitle_cfg.get("custom_position", 70.0)),
                bgm_type=profile.get("audio", {}).get("bgm_type", "random"),
                bgm_volume=float(profile.get("audio", {}).get("bgm_volume", 0.18)),
            )

            video.generate_video(
                video_path=combined_video_path,
                audio_path=state["audio_file"],
                subtitle_path=state["subtitle_path"],
                output_file=final_video_path,
                params=params,
            )

            if not os.path.exists(final_video_path) or os.path.getsize(final_video_path) == 0:
                raise RuntimeError("Video rendering failed or output file is 0 bytes.")

            state.update({
                "final_video": final_video_path,
                "stage": "video_rendered"
            })
            checkpoint.save(state)
            print(f"  ✓ Render complete: {final_video_path}")

        # -------------------------------------------------------------
        # STAGE 6: Archive & Metadata Assembly
        # -------------------------------------------------------------
        if state["stage"] == "video_rendered":
            print_stage_header(6, 6, "High-CTR Thumbnail & Archive Packaging")
            ep_num = state.get("episode_num")
            clean_slug = _slugify(state["topic"])[:45]
            if ep_num is not None:
                folder_name = f"{int(ep_num):02d}_{clean_slug}"
            else:
                folder_name = f"{time.strftime('%Y%m%d_%H%M')}_{clean_slug}"

            out_dir = Path("output") / niche_slug / folder_name
            out_dir.mkdir(parents=True, exist_ok=True)

            dest_mp4 = out_dir / f"{folder_name}.mp4"
            shutil.copy2(state["final_video"], dest_mp4)

            # Copy ranking report and all scene art photos into output archive for full transparency
            ranking_src = os.path.join(task_dir, "candidates_ranking.json")
            if os.path.exists(ranking_src):
                shutil.copy2(ranking_src, out_dir / "candidates_ranking.json")

            import glob
            for sa in glob.glob(os.path.join(task_dir, "scene_art_*.jpg")):
                if os.path.exists(sa):
                    shutil.copy2(sa, out_dir / os.path.basename(sa))

            # Step 6B: Render High-CTR Viral Thumbnail via Authentic Topic Photo & Bold Typography
            thumb_path = out_dir / "thumbnail.jpg"
            thumb_prompt = state.get("thumbnail_prompt", f"dramatic cinematic shot of {state['topic']}")
            print(f"  [ThumbnailGenerator] Rendering thumbnail using authentic photo from episode topics...")
            try:
                from core.thumbnail_generator import render_thumbnail_image
                render_thumbnail_image(
                    concept={
                        "thumbnail_text": state.get("thumbnail_text", "THEY HID THIS"),
                        "thumbnail_prompt": thumb_prompt,
                        "task_dir": task_dir,
                        "video_path": str(dest_mp4),
                        "use_real_photo": True
                    },
                    output_path=str(thumb_path),
                    font_path="resource/fonts/Montserrat-Black.ttf"
                )
            except Exception as t_err:
                logger.warning(f"Thumbnail generation error: {t_err}")

            # Build rich syndication metadata
            metadata = {
                "task_id": task_id,
                "niche": niche_slug,
                "title": state.get("title", f"The Secret of {state['topic']}"),
                "topic": state["topic"],
                "script": state["script"],
                "thumbnail_file": str(thumb_path.resolve()) if thumb_path.exists() else "",
                "duration_seconds": state["audio_duration"],
                "file_path": str(dest_mp4.resolve()),
                "file_size_mb": round(dest_mp4.stat().st_size / (1024 * 1024), 2),
                "visual_terms": state.get("visual_beats") or state.get("terms", []),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["created_at"])),
                "completed_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            }

            meta_path = out_dir / "metadata.json"
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            state["stage"] = "completed"
            checkpoint.save(state)
            checkpoint.archive(str(Path("storage") / "checkpoints" / niche_slug), task_id)

            # Comprehensive Production Scorecard & Observability
            from core.asset_registry import registry
            reg_stats = registry.get_stats()
            print_production_scorecard(metadata, reg_stats, dest_mp4, meta_path)

            # Shutdown stealth browser context only when video generation completes 100% successfully
            shutdown_stealth_scraper()

    except KeyboardInterrupt:
        print(f"\n[Worker] Execution paused at stage '{state.get('stage')}'. Checkpoint saved.")
        state["status"] = "interrupted"
        checkpoint.save(state)
        sys.exit(0)

    except Exception as e:
        print(f"\n[Worker] Pipeline failed at stage '{state.get('stage')}': {e}")
        state["status"] = "failed"
        state["last_error"] = str(e)
        checkpoint.save(state)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Niche Video Worker")
    parser.add_argument("--profile", default="rarely_seen", help="Niche profile slug or path")
    parser.add_argument("--topic", default=None, help="Explicit topic override")
    parser.add_argument("--clear-state", action="store_true", help="Clear saved state and start fresh")
    parser.add_argument("--episode", type=int, default=None, help="Episode number in series (e.g. 1, 2, 3)")
    args = parser.parse_args()

    run_worker_pipeline(
        profile_path=args.profile,
        topic_override=args.topic,
        clear_state=args.clear_state,
        episode_num=args.episode
    )
