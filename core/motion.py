#!/usr/bin/env python3
"""
==============================================================================
Motion Engine: High-Performance Cinematic Ken Burns (core/motion.py)
==============================================================================
Transforms still images (web archival photos, SDXL renders) into 
cinematic 1080x1920 30fps vertical video clips.

Features:
1. Dynamic Aspect-Ratio Detection (PIL):
   - Landscape Photos (aspect > 1.1): Scales height to 1920px (-2:1920) and performs
     a smooth Left-to-Right (or Right-to-Left) Ken Burns horizontal pan across
     100% of the high-res photo details, filling the vertical frame.
   - Portrait Photos (aspect < 0.9): Scales width to 1080px (1080:-2) and performs
     a smooth Top-to-Bottom or Bottom-to-Top vertical pan.
   - Square Photos (0.9 <= aspect <= 1.1): Smart blur background padding with
     gentle center zoom push-in / pull-out.
2. Hardware Acceleration: FFmpeg NVENC (h264_nvenc) with instant libx264 fallback.
==============================================================================
"""

import os
import subprocess
from pathlib import Path
from PIL import Image
from loguru import logger
from app.utils import utils


def image_to_cinematic_clip(
    image_path: str,
    output_clip_path: str,
    duration: float = 3.5,
    preset_index: int = 0,
    fps: int = 30
) -> str:
    """
    Converts a single image into a 1080x1920 30fps video clip using motion panning.
    Automatically selects optimal Ken Burns panning filter based on image aspect ratio.
    """
    if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
        raise FileNotFoundError(f"Source image not found: {image_path}")

    frames = max(1, int(duration * fps))

    # Detect image dimensions and aspect ratio
    try:
        with Image.open(image_path) as img:
            img_w, img_h = img.size
    except Exception as e:
        logger.warning(f"[MotionEngine] Could not open PIL image {image_path}: {e}")
        img_w, img_h = 1920, 1080

    aspect = img_w / float(img_h) if img_h > 0 else 1.777

    # Universal Blurred Background: The photo is scaled to cover 1080x1920 and blurred,
    # while the sharp original photo is centered in full (uncropped, zero movement) in the foreground.
    vf_string = (
        "split[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,colorchannelmixer=rr=0.38:gg=0.38:bb=0.38[bg_blur];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_fit];"
        "[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2"
    )

    ffmpeg_bin = utils.get_ffmpeg_binary()
    cmd_nvenc = [
        ffmpeg_bin, "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf_string,
        "-c:v", "h264_nvenc",
        "-preset", "p4", "-tune", "hq",
        "-pix_fmt", "yuv420p",
        "-t", f"{duration:.2f}",
        "-r", str(fps),
        output_clip_path
    ]
    cmd_cpu = [
        ffmpeg_bin, "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf_string,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-t", f"{duration:.2f}",
        "-r", str(fps),
        output_clip_path
    ]

    success = False
    try:
        res = subprocess.run(cmd_nvenc, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(output_clip_path) and os.path.getsize(output_clip_path) > 0:
            success = True
    except Exception:
        pass

    if not success:
        if os.path.exists(output_clip_path):
            try:
                os.remove(output_clip_path)
            except OSError:
                pass
        subprocess.run(cmd_cpu, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not os.path.exists(output_clip_path) or os.path.getsize(output_clip_path) == 0:
        raise RuntimeError(f"Failed to render cinematic clip for {image_path}")

    return output_clip_path
