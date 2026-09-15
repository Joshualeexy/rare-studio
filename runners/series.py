#!/usr/bin/env python3
"""
==============================================================================
Autonomous Niche Video Series Runner (run_series.py)
==============================================================================
Generates multi-episode video series sequentially or endlessly across any niche.
Handles research, 30.5B scriptwriting, 8B movie directing, ComfyUI SDXL scenes,
hardware NVENC video rendering, and infinite dynamic AI topic brainstorming.
==============================================================================
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from loguru import logger

from app.services import llm
from core.profile_loader import load_profile
from runners.worker import _get_llm_config, run_worker_pipeline


def _norm_topic(t: str) -> str:
    """Normalize topic strings for collision-free comparison."""
    return re.sub(r"[^a-z0-9]", "", str(t).lower())


def get_niche_archive_info(niche_slug: str):
    """
    Scans output/{niche_slug}, 2026-*/{niche_slug}, and all archive folders across the workspace to return:
      - dict of existing topics (lowercase -> metadata)
      - max episode number found in folder names (e.g. 05_... -> 5)
    """
    existing = {}
    max_ep_num = 0
    search_dirs = [Path("output")] + [p for p in Path(".").glob("202*") if p.is_dir()]

    for root_p in search_dirs:
        niche_out_dir = root_p / niche_slug
        if niche_out_dir.exists():
            for d in niche_out_dir.iterdir():
                if d.is_dir():
                    m = re.match(r"^(\d+)_", d.name)
                    if m:
                        try:
                            ep_val = int(m.group(1))
                            if ep_val > max_ep_num:
                                max_ep_num = ep_val
                        except ValueError:
                            pass
                    meta_file = d / "metadata.json"
                    if meta_file.exists():
                        try:
                            with open(meta_file, "r", encoding="utf-8") as mf:
                                m_data = json.load(mf)
                                t_name = m_data.get("topic", "").strip()
                                if t_name:
                                    existing[t_name.lower()] = m_data
                        except Exception:
                            pass
    return existing, max(max_ep_num, len(existing))


def generate_dynamic_niche_topic(profile: dict, existing_topics: list) -> str:
    """
    Brainstorms a brand new, factual, viral documentary topic using AI.
    Guarantees endless continuous generation without hitting topic limits.
    """
    niche_name = profile["niche"]["name"]
    niche_desc = profile["niche"]["description"]
    current_arc = profile.get("series", {}).get("current_arc", f"{niche_name} Mysteries")
    tone = profile.get("persona", {}).get("tone", "suspenseful investigative documentary")

    covered_sample = [t for t in existing_topics if t][-25:]
    covered_str = "\n".join(f"- {t}" for t in covered_sample) if covered_sample else "None yet"

    prompt = f"""You are the Executive Producer for viral short-form slideshow documentaries.
Niche: {niche_name} ({niche_desc})
Series Arc: {current_arc}
Tone: {tone}

The following topics have ALREADY been produced and MUST NOT be repeated:
{covered_str}

Brainstorm exactly ONE brand new, real, fascinating, and photo-rich documentary topic suitable for a 5-photo countdown short.
It MUST focus on a category or subject with broad photographic search potential (e.g., famous landmarks before completion, World War II declassified archives, deep sea discoveries, ancient megaliths, or rare space phenomena).
Return ONLY the topic title (5 to 10 words). No quotes, no markdown, no punctuation at the end, no commentary."""

    app_cfg = _get_llm_config(profile)
    resp = None
    for attempt in range(3):
        try:
            r = llm._generate_response(prompt, app_config=app_cfg)
            if r and not r.startswith("Error:"):
                resp = r
                break
            time.sleep(1)
        except Exception:
            time.sleep(1)

    # Local Ollama fallback if cloud LLM is unreachable
    if not resp or resp.startswith("Error:"):
        fallback_cfg = dict(app_cfg)
        fallback_cfg["llm_provider"] = "ollama"
        fallback_cfg["ollama_model_name"] = "qwen3:8b"
        fallback_cfg["ollama_base_url"] = "http://127.0.0.1:11434/v1"
        resp = llm._generate_response(prompt, app_config=fallback_cfg)

    clean = (resp or "").strip().strip("\"'\"'*`")
    clean = re.sub(r"^(Topic:|\d+\.|\-)\s*", "", clean)
    clean = re.sub(r"[\r\n]+.*", "", clean).strip()
    return clean or f"The Hidden Paradox of {niche_name}"


def run_next_niche_episode(profile_name: str) -> dict:
    """
    Renders the single next sequential episode for a given niche profile:
      1. Renders any unrendered pre-configured episode from series.episodes
      2. If all pre-configured episodes are finished, autonomously brainstorms
         a fresh viral topic via AI with sequential numbering (06_, 07_, 400_...)
    """
    profile = load_profile(profile_name)
    niche_slug = profile["niche"]["slug"]
    series_cfg = profile.get("series", {})
    arc_title = series_cfg.get("current_arc", f"{profile['niche']['name']} Series")
    preconfigured = series_cfg.get("episodes", [])

    existing_topics, max_ep_num = get_niche_archive_info(niche_slug)
    norm_existing = {_norm_topic(k) for k in existing_topics.keys()}

    # 1. Look for next unrendered preconfigured episode (if any remain)
    chosen_topic = None
    chosen_ep_num = None

    for idx, ep in enumerate(preconfigured, 1):
        if _norm_topic(ep) not in norm_existing:
            chosen_topic = ep
            chosen_ep_num = idx if idx > max_ep_num else max_ep_num + 1
            break

    # 2. Limitless AI Brainstorming: Generate fresh unique topic dynamically
    if not chosen_topic:
        print(f"  ⚡ [{niche_slug.upper()}] Initial starter topics completed ({len(existing_topics)} archives on disk).")
        print(f"  🧠 Autonomously brainstorming brand-new viral topic via AI (Limitless Mode)...")
        chosen_topic = generate_dynamic_niche_topic(profile, list(existing_topics.keys()))
        chosen_ep_num = max_ep_num + 1

    print("\n" + "#" * 65)
    print(f"  🎬 [{niche_slug.upper()}] EPISODE {chosen_ep_num}: {chosen_topic}")
    print(f"  Arc: {arc_title}")
    print("#" * 65 + "\n")

    start_time = time.time()
    run_worker_pipeline(
        profile_path=profile_name,
        topic_override=chosen_topic,
        clear_state=True,
        episode_num=chosen_ep_num
    )

    # Find the newly created folder in output/{niche_slug}
    niche_out_dir = Path("output") / niche_slug
    if niche_out_dir.exists():
        task_dirs = sorted([d for d in niche_out_dir.iterdir() if d.is_dir()], key=lambda d: d.stat().st_mtime, reverse=True)
        if task_dirs:
            latest_dir = task_dirs[0]
            meta_file = latest_dir / "metadata.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        meta["elapsed_seconds"] = int(time.time() - start_time)
                        meta["episode"] = chosen_ep_num
                        return meta
                except Exception:
                    pass

    return {
        "episode": chosen_ep_num,
        "topic": chosen_topic,
        "elapsed_seconds": int(time.time() - start_time)
    }


def run_series(profile_name: str, count: int = 5):
    """Runs a series of episodes up to count, generating fresh topics if needed."""
    profile = load_profile(profile_name)
    niche_slug = profile["niche"]["slug"]
    series_cfg = profile.get("series", {})
    arc_title = series_cfg.get("current_arc", f"{profile['niche']['name']} Series")

    print("\n" + "=" * 65)
    print(f" 🎬 LAUNCHING {count}-EPISODE SERIES: [{niche_slug.upper()}]")
    print(f" Arc: {arc_title}")
    print("=" * 65 + "\n")

    completed_episodes = []

    for _ in range(count):
        existing_topics, _ = get_niche_archive_info(niche_slug)
        if len(existing_topics) >= count:
            print(f"  ✓ Target of {count} episodes already achieved for [{niche_slug.upper()}].")
            break

        try:
            meta = run_next_niche_episode(profile_name)
            if meta:
                completed_episodes.append(meta)
        except Exception as e:
            print(f"\n❌ Episode failed: {e}")
            break

    # Summary Card
    print("\n" + "=" * 65)
    print(f" 🎉 BATCH COMPLETE: {len(completed_episodes)} EPISODES PRODUCED")
    print(f" Niche: {profile['niche']['name']} | Arc: {arc_title}")
    print("=" * 65)
    for ep in completed_episodes:
        print(f"\n[Episode {ep.get('episode')}] {ep.get('title', ep.get('topic'))}")
        print(f"  • Subject:   {ep.get('topic')}")
        print(f"  • Duration:  {ep.get('duration_seconds', 0)}s | Size: {ep.get('file_size_mb', 0)} MB")
        print(f"  • Video:     {ep.get('file_path', '')}")
        print(f"  • Thumbnail: {ep.get('thumbnail_file', '')}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Rare Photos Series Runner")
    parser.add_argument("--profile", default="rarely_seen", help="Niche profile slug or path (default: rarely_seen)")
    parser.add_argument("--count", type=int, default=5, help="Number of episodes to generate")
    args = parser.parse_args()

    run_series(args.profile, count=args.count)
