#!/usr/bin/env python3
"""
==============================================================================
Procedural Motion Graphics Engine (core/motion_graphics.py)
==============================================================================
Generates standalone, high-impact procedural animation clips:
  1. Classified Redacted Dossier (animating black marker over secret text)
  2. Sonar / Radar Pulse (pulsing forensic detection sweep)
  3. Tech Laser HUD / Data Stream (cyber scanning lines)
  4. Vintage Map Location Ping (crosshair & target pulse)
All rendered directly via PIL & FFmpeg with NVENC acceleration.
==============================================================================
"""

import os
import math
import subprocess
from PIL import Image, ImageDraw, ImageFont


def create_redacted_dossier_clip(
    output_path: str,
    title: str = "DEEP CLASSIFIED INTEL",
    agency: str = "CENTRAL INTELLIGENCE AGENCY",
    duration: float = 3.0,
    fps: int = 30
) -> str:
    """
    Renders an authentic aged government dossier where a black highlighter
    dynamically sweeps across classified lines.
    """
    w, h = 1080, 1920
    total_frames = int(duration * fps)
    temp_dir = os.path.join(os.path.dirname(output_path), "redact_frames")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        font_mono = ImageFont.truetype("resource/fonts/Montserrat-Black.ttf", 36)
        font_header = ImageFont.truetype("resource/fonts/Montserrat-Black.ttf", 52)
        font_stamp = ImageFont.truetype("resource/fonts/Montserrat-Black.ttf", 68)
    except Exception:
        font_mono = ImageFont.load_default()
        font_header = font_mono
        font_stamp = font_mono

    # Base classified document page
    base = Image.new("RGB", (w, h), (26, 26, 26))
    d_base = ImageDraw.Draw(base)

    # Document card container
    margin = 80
    card = [margin, 180, w - margin, h - 220]
    d_base.rounded_rectangle(card, radius=24, fill=(240, 236, 226), outline=(180, 170, 150), width=6)

    # Top Secret Red Stamp
    d_base.rounded_rectangle([margin + 40, 220, w - margin - 40, 320], radius=12, outline=(190, 30, 30), width=8)
    d_base.text((w // 2, 270), "TOP SECRET // EYES ONLY", font=font_stamp, fill=(190, 30, 30), anchor="mm")

    # Agency & Title
    d_base.text((margin + 50, 370), f"ORIGIN: {agency}", font=font_mono, fill=(50, 50, 50))
    d_base.text((margin + 50, 420), f"SUBJECT: {title[:32].upper()}", font=font_header, fill=(20, 20, 20))
    d_base.line([(margin + 50, 490), (w - margin - 50, 490)], fill=(120, 120, 120), width=3)

    # Dossier paragraphs
    lines_text = [
        "1. SUBJECT ENCOUNTERED UNDER NON-STANDARD FORENSIC CIRCUMSTANCES.",
        "2. RETRIEVED EVIDENCE EXHIBITS ANOMALOUS PHYSICAL DEVIATIONS.",
        "3. WITNESS TESTIMONY SUPPRESSED UNDER EXECUTIVE DIRECTIVE 44-B.",
        "4. TARGET WAS LOCATED AT UNMARKED COORDINATES PRIOR TO BREACH.",
        "5. PHYSICAL TRACES DEMONSTRATE TECHNOLOGY OUTSIDE MODERN PARADIGMS.",
        "6. ALL ASSOCIATED FIELD PERSONNEL ORDERED SILENT UNDER PENALTY.",
        "7. RECOVERY OPERATION DEEMED COMPLETE. ARCHIVE SEALED INDEFINITIVELY."
    ]

    base_y = 540
    line_boxes = []
    for i, line in enumerate(lines_text):
        ly = base_y + i * 140
        d_base.text((margin + 50, ly), line, font=font_mono, fill=(40, 40, 40))
        if i in [1, 2, 4, 5]:
            line_boxes.append([margin + 45, ly - 6, w - margin - 60, ly + 46])

    # Render animated frames
    for f in range(total_frames):
        progress = f / float(total_frames)
        frame = base.copy()
        d_frame = ImageDraw.Draw(frame)

        for idx, box in enumerate(line_boxes):
            line_start = idx / float(len(line_boxes))
            line_end = (idx + 1) / float(len(line_boxes))
            if progress > line_start:
                pct = min(1.0, (progress - line_start) / (line_end - line_start))
                cur_w = (box[2] - box[0]) * pct
                d_frame.rounded_rectangle([box[0], box[1], box[0] + cur_w, box[3]], radius=8, fill=(15, 15, 15))

        frame_file = os.path.join(temp_dir, f"frame_{f:04d}.jpg")
        frame.save(frame_file, quality=90)

    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", os.path.join(temp_dir, "frame_%04d.jpg"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}", output_path
    ]
    subprocess.run(cmd, capture_output=True, check=False)

    for f in os.listdir(temp_dir):
        try:
            os.remove(os.path.join(temp_dir, f))
        except OSError:
            pass
    try:
        os.rmdir(temp_dir)
    except OSError:
        pass

    return output_path


def create_radar_pulse_clip(
    output_path: str,
    target_label: str = "ANOMALY DETECTED",
    duration: float = 3.0,
    fps: int = 30
) -> str:
    """
    Renders an authentic glowing deep-sea / military radar detection sweep.
    """
    w, h = 1080, 1920
    cx, cy = w // 2, h // 2
    r_max = 420
    total_frames = int(duration * fps)
    temp_dir = os.path.join(os.path.dirname(output_path), "radar_frames")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        font_hud = ImageFont.truetype("resource/fonts/Montserrat-Black.ttf", 44)
        font_sub = ImageFont.truetype("resource/fonts/Montserrat-Black.ttf", 32)
    except Exception:
        font_hud = ImageFont.load_default()
        font_sub = font_hud

    for f in range(total_frames):
        t = f / float(fps)
        angle = (t * 2.2 * math.pi) % (2 * math.pi)

        img = Image.new("RGB", (w, h), (4, 12, 18))
        d = ImageDraw.Draw(img)

        for ring in [120, 220, 320, 420]:
            d.ellipse([cx - ring, cy - ring, cx + ring, cy + ring], outline=(0, 180, 160), width=3)
        d.line([(cx - r_max, cy), (cx + r_max, cy)], fill=(0, 140, 120), width=2)
        d.line([(cx, cy - r_max), (cx, cy + r_max)], fill=(0, 140, 120), width=2)

        lx = cx + r_max * math.cos(angle)
        ly = cy + r_max * math.sin(angle)
        d.line([(cx, cy), (lx, ly)], fill=(0, 255, 200), width=6)

        blip_x = cx + int(240 * math.cos(0.8))
        blip_y = cy + int(240 * math.sin(0.8))
        pulse = abs(math.sin(t * 5.0))
        b_rad = int(14 + 10 * pulse)
        d.ellipse([blip_x - b_rad, blip_y - b_rad, blip_x + b_rad, blip_y + b_rad], fill=(255, 60, 60))
        d.ellipse([blip_x - b_rad - 6, blip_y - b_rad - 6, blip_x + b_rad + 6, blip_y + b_rad + 6], outline=(255, 100, 100), width=2)

        d.text((cx, cy - r_max - 80), "ACOUSTIC SONAR ARRAY // 4000m DEEP", font=font_hud, fill=(0, 230, 200), anchor="mm")
        d.text((cx, cy + r_max + 70), f"CONTACT: {target_label.upper()}", font=font_hud, fill=(255, 70, 70), anchor="mm")
        d.text((cx, cy + r_max + 130), f"BEARING: 042° // RANGE: 1.4km // PING #{f+1:03d}", font=font_sub, fill=(0, 180, 150), anchor="mm")

        frame_file = os.path.join(temp_dir, f"frame_{f:04d}.jpg")
        img.save(frame_file, quality=90)

    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", os.path.join(temp_dir, "frame_%04d.jpg"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}", output_path
    ]
    subprocess.run(cmd, capture_output=True, check=False)

    for f in os.listdir(temp_dir):
        try:
            os.remove(os.path.join(temp_dir, f))
        except OSError:
            pass
    try:
        os.rmdir(temp_dir)
    except OSError:
        pass

    return output_path
