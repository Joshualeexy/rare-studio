"""
==============================================================================
Viral Title & Thumbnail Generator
==============================================================================
Generates high-CTR curiosity titles, thumbnail text hooks, and custom
photorealistic SDXL visuals via ComfyUI with high-impact typography overlays.
"""

import os
import json
import subprocess
from pathlib import Path
from loguru import logger
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.services import llm
from core.comfy_client import ComfyClient


def generate_title_and_thumbnail_concepts(script: str, topic: str, profile: dict, viral_hooks: list = None) -> dict:
    """
    Generates high-CTR title, thumbnail short text, and ComfyUI SDXL visual prompt
    anchored strictly to the topic domain and scene context, aligned with real viral search inquiries.
    """
    hooks_context = ""
    if viral_hooks:
        hooks_list = "\n".join(f"- \"{h}\"" for h in viral_hooks[:4])
        hooks_context = f"\nHigh-Intent Search Queries (What Viewers Are Actively Searching):\n{hooks_list}\nYou are strongly encouraged to align the title with one of these high-search-volume queries.\n"

    prompt = f"""You are a viral YouTube Shorts and TikTok thumbnail copywriter and creative director.
Given this video script about '{topic}':

"{script}"
{hooks_context}
Generate:
1. "title": A high-CTR viral video title (under 55 characters, curiosity hook, e.g. "What They Found Under Antarctica Terrifies Scientists").
2. "thumbnail_text": 2 to 4 words MAX for the thumbnail overlay in ALL CAPS (e.g. "DO NOT ENTER", "THEY HID THIS", "IMPOSSIBLE FIND", "BURIED IN ICE"). Must evoke extreme curiosity.
3. "thumbnail_prompt": A detailed textless cinematic SDXL image prompt for ComfyUI.

CRITICAL RULES FOR THUMBNAIL PROMPT:
- The image MUST depict '{topic}' and the core discovery/scene described in the script.
- Every visual element MUST be set directly in the environment of '{topic}'.
- If topic is '{topic}', describe the physical environment, lighting, and subjects of '{topic}' (e.g. "dramatic cinematic photorealistic shot of massive industrial drill rig boring into frozen ice sheet in {topic}, subterranean cavern, volumetric blue lighting, 8k, national geographic photography, textless, no words").
- NEVER depict unrelated rooms, hospitals, generic buildings, or modern city streets.

Return ONLY a JSON object with keys "title", "thumbnail_text", "thumbnail_prompt". No explanations, no markdown."""

    from runners.worker import _get_llm_config
    app_cfg = _get_llm_config(profile)
    resp = llm._generate_response(prompt, app_config=app_cfg)
    try:
        cleaned = resp.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip())
        if isinstance(data, dict) and "title" in data:
            # Enforce topic anchoring in the prompt
            p_text = data.get("thumbnail_prompt", "")
            if topic.lower() not in p_text.lower():
                data["thumbnail_prompt"] = f"dramatic cinematic shot of {topic}, {p_text}"
            return data
    except Exception as e:
        logger.warning(f"Failed to parse title/thumbnail JSON: {e}")

    return {
        "title": f"The Forbidden Mystery of {topic}",
        "thumbnail_text": "THEY HID THIS",
        "thumbnail_prompt": f"dramatic cinematic photorealistic shot of mysterious discovery buried deep in {topic}, volumetric lighting, 8k, national geographic photography, textless"
    }


def render_thumbnail_image(
    concept: dict,
    output_path: str,
    font_path: str = "resource/fonts/Montserrat-Black.ttf",
    comfy_client: ComfyClient = None
) -> str:
    """
    Generates the SDXL base visual via ComfyUI and overlays bold viral typography.
    """
    if comfy_client is None:
        comfy_client = ComfyClient()

    base_image_path = output_path.replace(".jpg", "_raw.jpg")
    acquired = False

    # 1. Primary Source: Authentic Real Photograph from episode topic scenes (100% Real Photo)
    task_dir = concept.get("task_dir") or os.path.dirname(output_path)
    real_photo_path = concept.get("real_photo_path")

    if not real_photo_path and os.path.exists(task_dir):
        # Look for real scene art photos in task_dir (prefer topic scenes 2, 3, 4, 5...)
        for scene_idx in [2, 3, 4, 5, 1, 6, 7]:
            cand = os.path.join(task_dir, f"scene_art_{scene_idx}.jpg")
            if os.path.exists(cand) and os.path.getsize(cand) > 10000:
                real_photo_path = cand
                break
        if not real_photo_path:
            cands = [os.path.join(task_dir, f) for f in os.listdir(task_dir) if f.startswith("scene_art_") and f.endswith(".jpg")]
            if cands:
                real_photo_path = cands[0]

    use_real_photo = concept.get("use_real_photo", True)
    if use_real_photo and real_photo_path and os.path.exists(real_photo_path):
        logger.info(f"[Thumbnail] Using authentic episode photograph for thumbnail base: {real_photo_path}")
        import shutil
        shutil.copy2(real_photo_path, base_image_path)
        acquired = True

    # 2. Secondary Fallback: Extract authentic frame from rendered video
    if not acquired:
        video_path = concept.get("video_path")
        if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            if os.path.exists(task_dir):
                vids = [os.path.join(task_dir, f) for f in os.listdir(task_dir) if f.endswith(".mp4")]
                if vids:
                    video_path = vids[0]

        if video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            logger.info(f"[Thumbnail] Extracting authentic video frame from {video_path}")
            for ts in ["00:00:05.0", "00:00:03.5", "00:00:08.0", "00:00:02.0"]:
                cmd = ["ffmpeg", "-y", "-ss", ts, "-i", video_path, "-vframes", "1", "-q:v", "2", base_image_path]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if os.path.exists(base_image_path) and os.path.getsize(base_image_path) > 0:
                    acquired = True
                    break

    # 3. Tertiary Fallback: ComfyUI SDXL prompt (only if no real photo or video frame exists)
    if not acquired and comfy_client is not None:
        prompt_txt = concept.get("thumbnail_prompt", "")
        logger.info(f"[Thumbnail] Falling back to ComfyUI SDXL visual for: '{prompt_txt[:60]}...'")
        if comfy_client.ensure_running():
            acquired = comfy_client.generate_scene_image(prompt_txt, base_image_path)

    if not acquired or not os.path.exists(base_image_path):
        logger.warning("[Thumbnail] No visual source available, using dark atmospheric canvas")
        img = Image.new("RGB", (1080, 1920), (10, 14, 22))
        img.save(base_image_path)

    # 4. Save Clean, Textless Photograph directly as Thumbnail (No text/caption overlays)
    with Image.open(base_image_path).convert("RGB") as base_img:
        # Scale to fill 1080x1920 keeping original aspect ratio
        w, h = base_img.size
        target_w, target_h = 1080, 1920
        ratio = max(target_w / w, target_h / h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        resized = base_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # Center crop to 1080x1920
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        cropped = resized.crop((left, top, left + target_w, top + target_h))
        
        cropped.save(output_path, "JPEG", quality=98)
        logger.info(f"  ✓ Clean textless real-photo thumbnail saved: {output_path}")

    # Clean up raw temp base
    if os.path.exists(base_image_path) and base_image_path != output_path:
        try:
            os.remove(base_image_path)
        except OSError:
            pass

    return output_path
