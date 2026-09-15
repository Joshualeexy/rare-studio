#!/usr/bin/env python3
"""
==============================================================================
Infinite Multi-Niche Autonomous Video Engine (run_infinite_generator.py)
==============================================================================
Runs continuously across all niches with ZERO limits:
  • Autonomously renders pre-configured series episodes across all 13 niches
  • Dynamically brainstorms fresh viral topics via AI once initial arcs complete
  • Continuous round-robin production across all profiles
  • Frame-accurate sentence-timed visual cuts (zero audio desync)
  • Clean chronological folder naming (01_..., 02_..., 03_... 400_...)
  • ComfyUI SDXL Hero Scene cinematics & High-CTR viral thumbnails
  • Pexels 1080x1920 HD vertical footage & Montserrat-Black subtitles
  • Automated VRAM memory handshakes (zero CUDA OOM)
  • Self-healing crash recovery & infinite autonomous operation
==============================================================================
"""

import os
import sys
import time
import requests
from pathlib import Path
from loguru import logger

# Route detailed debug to log file, keeping stdout clean
try:
    logger.remove()
    logger.add("pipeline_debug.log", rotation="25 MB", retention="5 days", level="DEBUG", encoding="utf-8")
    logger.add(sys.stderr, level="ERROR", format="<red>[ERROR]</red> {message}")
except Exception:
    pass

from runners.series import run_next_niche_episode, get_niche_archive_info
from core.profile_loader import load_profile


def get_niches_queue():
    """Dynamically scans all profiles in profiles/*.toml to queue every available niche."""
    profiles_dir = Path("profiles")
    discovered = []
    priority_order = ["rarely_seen"]
    seen = set()
    for slug in priority_order:
        p_path = profiles_dir / f"{slug}.toml"
        if p_path.exists():
            discovered.append(slug)
            seen.add(slug)
    for p in sorted(profiles_dir.glob("*.toml")):
        if p.stem not in seen:
            discovered.append(p.stem)
    return discovered


def free_all_gpu_vram():
    """Flushes both ComfyUI and Ollama weights from GPU memory."""
    try:
        requests.post("http://127.0.0.1:8188/free", json={"unload_models": True, "free_memory": True}, timeout=3)
    except Exception:
        pass
    try:
        for model in ["qwen3-coder-agent:latest", "qwen3:8b", "qwen3-8b-agent:latest", "deepseek-chat"]:
            requests.post("http://127.0.0.1:11434/api/generate", json={"model": model, "keep_alive": 0}, timeout=3)
    except Exception:
        pass


def log_generator(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open("infinite_pipeline.log", "a", encoding="utf-8") as f:
        f.write(formatted + "\n")


def run_infinite_loop(gallery_port: int = 5050):
    try:
        from runners.gallery import ensure_gallery_server_running
        ensure_gallery_server_running(port=gallery_port)
    except Exception as e:
        logger.warning(f"[Infinite] Gallery auto-start check warning: {e}")

    niches = get_niches_queue()
    log_generator("=" * 65)
    log_generator("🚀 STARTING INFINITE MULTI-NICHE AUTONOMOUS GENERATOR")
    log_generator(f"   Profiles Active ({len(niches)}): {', '.join(niches)}")
    log_generator("   Mode: Uncapped 24/7 Autonomous Production (No Video Limit)")
    log_generator(f"   Studio Gallery: http://localhost:{gallery_port}")
    log_generator("=" * 65)

    cycle = 1
    session_generated_count = 0

    while True:
        niches = get_niches_queue()
        log_generator(f"\n🔄 --- STARTING PIPELINE CYCLE #{cycle} ---")
        
        for niche_slug in niches:
            log_generator(f"\n========================================================")
            log_generator(f"🎬 NICHE ACTIVE: [{niche_slug.upper()}] (Cycle #{cycle})")
            log_generator(f"========================================================")
            
            # Flush VRAM before starting niche
            free_all_gpu_vram()
            time.sleep(2)

            try:
                meta = run_next_niche_episode(niche_slug)
                if meta:
                    session_generated_count += 1
                    ep_title = meta.get("title") or meta.get("topic")
                    ep_num = meta.get("episode", "?")
                    elapsed = meta.get("elapsed_seconds", 0)
                    log_generator(f"✓ [{niche_slug.upper()}] Episode #{ep_num} '{ep_title}' rendered successfully in {elapsed}s!")
                    log_generator(f"   📊 Session Total Generated: {session_generated_count} videos")
            except Exception as e:
                log_generator(f"❌ Error during niche [{niche_slug.upper()}]: {e}")
                log_generator("Resuming next niche in 10 seconds...")
                time.sleep(10)

            # Flush VRAM after completing niche
            free_all_gpu_vram()
            time.sleep(3)

        # Calculate total videos currently in archive
        total_rendered_videos = len(list(Path("output").rglob("*.mp4")))
        log_generator(f"\n🎉 Cycle #{cycle} complete across all {len(niches)} niches!")
        log_generator(f"📁 Total Library Archive: {total_rendered_videos} completed MP4 videos across all profiles.")
        log_generator(f"🔄 Continuing immediately to Cycle #{cycle + 1}...")
        cycle += 1
        time.sleep(5)


if __name__ == "__main__":
    run_infinite_loop()
