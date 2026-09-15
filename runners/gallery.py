#!/usr/bin/env python3
"""
==============================================================================
Rare-Studio - Unified Video Gallery & Web Player
==============================================================================
First-party web server and interactive video player gallery for Rare-Studio.
Scans output directories, auto-generates thumbnails, streams MP4 videos,
and features a robust fullscreen video player with live pipeline updates.

Usage:
    python gallery.py [--port 5000] [--no-browser] [--host 0.0.0.0]
"""

import os
import sys
import json
import time
import re
import socket
import urllib.parse
import mimetypes
import subprocess
import webbrowser
import threading
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

DEFAULT_PORT = 5050
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(WORKSPACE_ROOT, "output")

VIDEO_EXTS = ('.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v')
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')

def format_size(bytes_val):
    if not bytes_val:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(bytes_val) < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"

def format_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return ""

def ensure_thumbnail(video_path):
    """
    Checks if a thumbnail exists for the video.
    If missing, uses ffmpeg to extract a frame at 1s.
    """
    parent_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    # Pre-existing candidates
    for candidate in ["thumbnail.jpg", "thumbnail.png", "thumb.jpg", f"{base_name}.jpg"]:
        c_path = os.path.join(parent_dir, candidate)
        if os.path.isfile(c_path):
            return c_path
            
    # Generate new thumbnail via ffmpeg
    target_thumb = os.path.join(parent_dir, "thumbnail.jpg")
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", "00:00:01",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            target_thumb
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if os.path.isfile(target_thumb):
            return target_thumb
    except Exception:
        pass
    return None

def base64_url_id(val):
    return base64.urlsafe_b64encode(val.encode('utf-8')).decode('utf-8').rstrip('=')

def get_all_videos():
    """
    Scans output directories and date folders for generated videos.
    """
    search_dirs = [OUTPUT_DIR]
    
    # Add date folders in workspace root (e.g. 2026-09-10)
    try:
        for entry in os.listdir(WORKSPACE_ROOT):
            full_p = os.path.join(WORKSPACE_ROOT, entry)
            if os.path.isdir(full_p) and re.match(r"^\d{4}-\d{2}-\d{2}", entry):
                if full_p not in search_dirs:
                    search_dirs.append(full_p)
    except Exception:
        pass

    # Check for symlinked outputs
    for sd in list(search_dirs):
        if not os.path.exists(sd):
            continue
        try:
            for item in os.listdir(sd):
                fp = os.path.join(sd, item)
                if os.path.islink(fp) and os.path.isdir(fp) and fp not in search_dirs:
                    search_dirs.append(fp)
        except Exception:
            pass

    videos = []
    visited_files = set()

    for sdir in search_dirs:
        if not os.path.exists(sdir):
            continue

        for root, dirs, files in os.walk(sdir):
            # Exclude hidden folders
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.lower().endswith(VIDEO_EXTS):
                    full_video_path = os.path.abspath(os.path.join(root, f))
                    if full_video_path in visited_files:
                        continue
                    visited_files.add(full_video_path)

                    try:
                        st = os.stat(full_video_path)
                    except OSError:
                        continue

                    # Metadata extraction
                    meta = {}
                    meta_file = os.path.join(root, "metadata.json")
                    if os.path.isfile(meta_file):
                        try:
                            with open(meta_file, "r", encoding="utf-8") as mf:
                                meta = json.load(mf)
                        except Exception:
                            pass

                    # Thumbnail lookup or auto-extract
                    thumb_path = ensure_thumbnail(full_video_path)
                    thumb_url = None
                    if thumb_path:
                        rel_thumb = os.path.relpath(thumb_path, WORKSPACE_ROOT)
                        thumb_url = f"/stream/{urllib.parse.quote(rel_thumb)}"

                    # Title derivation
                    title = meta.get("title") or meta.get("topic")
                    if not title:
                        base_no_ext = os.path.splitext(f)[0]
                        title = re.sub(r'^\d+[\s_-]*', '', base_no_ext).replace('_', ' ').replace('-', ' ').title()
                        if not title:
                            title = f

                    # Niche derivation
                    niche = meta.get("niche")
                    if not niche or niche == "null":
                        rel_parts = os.path.relpath(full_video_path, WORKSPACE_ROOT).split(os.sep)
                        if len(rel_parts) >= 3 and rel_parts[0] in ("output", "2026-09-07", "2026-09-10"):
                            niche = rel_parts[1]
                        elif len(rel_parts) >= 2:
                            niche = rel_parts[0]
                        else:
                            niche = "AI Video"

                    rel_video = os.path.relpath(full_video_path, WORKSPACE_ROOT)

                    videos.append({
                        "id": base64_url_id(rel_video),
                        "filename": f,
                        "title": title,
                        "niche": niche,
                        "topic": meta.get("topic", ""),
                        "duration_seconds": meta.get("duration_seconds", 0),
                        "script": meta.get("script", ""),
                        "size": st.st_size,
                        "size_fmt": format_size(st.st_size),
                        "mtime": st.st_mtime,
                        "mtime_fmt": format_time(st.st_mtime),
                        "rel_path": rel_video,
                        "thumbnail_url": thumb_url,
                        "stream_url": f"/stream/{urllib.parse.quote(rel_video)}",
                        "download_url": f"/dl/{urllib.parse.quote(rel_video)}"
                    })

    videos.sort(key=lambda x: x["mtime"], reverse=True)
    return videos

def safe_path_resolve(rel_path):
    unquoted = urllib.parse.unquote(rel_path)
    # Sanitize path to prevent directory traversal
    clean_rel = os.path.normpath(unquoted).lstrip('/\\')
    full_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, clean_rel))
    if full_path.startswith(WORKSPACE_ROOT) and os.path.exists(full_path):
        return full_path
    return None

def serve_stream_range(handler, filepath, is_download=False):
    """
    Streams a file with HTTP 206 Range request support for smooth scrubbing.
    """
    if not filepath or not os.path.isfile(filepath):
        handler.send_error(404, "File not found")
        return

    try:
        file_size = os.path.getsize(filepath)
    except OSError:
        handler.send_error(404, "Unable to read file")
        return

    filename = os.path.basename(filepath)
    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type:
        mime_type = "application/octet-stream"

    range_header = handler.headers.get("Range")

    if range_header and range_header.startswith("bytes="):
        match = re.search(r"bytes=(\d*)-(\d*)", range_header)
        if match:
            start_str, end_str = match.groups()
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            if end >= file_size:
                end = file_size - 1
            if start > end or start >= file_size:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{file_size}")
                handler.end_headers()
                return

            content_length = end - start + 1
            handler.send_response(206)
            handler.send_header("Content-Type", mime_type)
            handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            handler.send_header("Content-Length", str(content_length))
            handler.send_header("Accept-Ranges", "bytes")
            if is_download:
                handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            handler.end_headers()

            with open(filepath, "rb") as f:
                f.seek(start)
                remaining = content_length
                chunk_size = 65536
                while remaining > 0:
                    read_len = min(chunk_size, remaining)
                    chunk = f.read(read_len)
                    if not chunk:
                        break
                    try:
                        handler.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)
            return

    # Normal GET response
    handler.send_response(200)
    handler.send_header("Content-Type", mime_type)
    handler.send_header("Content-Length", str(file_size))
    handler.send_header("Accept-Ranges", "bytes")
    if is_download:
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()

    with open(filepath, "rb") as f:
        chunk_size = 65536
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class GalleryRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress routine GET logging to keep console clean
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_UI_TEMPLATE.encode("utf-8"))

        elif path == "/api/videos":
            videos = get_all_videos()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"videos": videos, "count": len(videos)}).encode("utf-8"))

        elif path == "/api/videos/latest":
            videos = get_all_videos()
            latest = videos[0] if videos else None
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"latest": latest}).encode("utf-8"))

        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "online",
                "workspace": WORKSPACE_ROOT,
                "output_dir": OUTPUT_DIR,
                "video_count": len(get_all_videos())
            }).encode("utf-8"))

        elif path.startswith("/stream/"):
            rel_path = path[len("/stream/"):]
            filepath = safe_path_resolve(rel_path)
            if filepath:
                serve_stream_range(self, filepath, is_download=False)
            else:
                self.send_error(404, "File not found")

        elif path.startswith("/dl/"):
            rel_path = path[len("/dl/"):]
            filepath = safe_path_resolve(rel_path)
            if filepath:
                serve_stream_range(self, filepath, is_download=True)
            else:
                self.send_error(404, "File not found")
        else:
            self.send_error(404, "Not Found")


def get_local_ips():
    ips = ["127.0.0.1"]
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbynameex(hostname)[2]:
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def ensure_gallery_server_running(port=DEFAULT_PORT):
    """
    Checks if the gallery server is currently listening on port and responding.
    If not running, launches gallery.py as a background process.
    """
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "online":
                    print(f"🎬 [Gallery] Video gallery live at http://localhost:{port}")
                    return True
    except Exception:
        pass

    # Start gallery server as background process
    gallery_script = os.path.abspath(__file__)
    cmd = [sys.executable, gallery_script, "--port", str(port), "--no-browser"]
    try:
        subprocess.Popen(
            cmd,
            cwd=WORKSPACE_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(1.2)
        print(f"🎬 [Gallery] Auto-started video gallery server at http://localhost:{port}")
        return True
    except Exception as e:
        print(f"❌ [Gallery] Failed to auto-start gallery server: {e}")
        return False


HTML_UI_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rare Studio - Video Gallery & Player</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #090d16;
            --bg-card: rgba(19, 27, 46, 0.7);
            --bg-card-hover: rgba(30, 42, 69, 0.85);
            --border-color: rgba(255, 255, 255, 0.08);
            --border-accent: rgba(99, 102, 241, 0.4);
            --accent-primary: #6366f1;
            --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #ec4899 100%);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --text-dim: #6b7280;
            --badge-bg: rgba(99, 102, 241, 0.15);
            --badge-text: #818cf8;
            --radius-lg: 16px;
            --radius-md: 12px;
            --radius-sm: 8px;
            --shadow-glow: 0 0 25px rgba(99, 102, 241, 0.25);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-main);
            color: var(--text-main);
            min-height: 100vh;
            overflow-x: hidden;
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(99, 102, 241, 0.12) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(236, 72, 153, 0.1) 0%, transparent 40%);
        }

        /* HEADER & NAVBAR */
        header {
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(16px);
            background: rgba(9, 13, 22, 0.8);
            border-bottom: 1px solid var(--border-color);
            padding: 16px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-icon {
            width: 38px;
            height: 38px;
            border-radius: var(--radius-md);
            background: var(--accent-gradient);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 20px;
            box-shadow: var(--shadow-glow);
        }

        .brand-title {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #fff 0%, #cbd5e1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .header-stats {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 999px;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #34d399;
            font-size: 13px;
            font-weight: 500;
        }

        .pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #34d399;
            box-shadow: 0 0 10px #34d399;
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.2); }
            100% { opacity: 1; transform: scale(1); }
        }

        /* MAIN CONTAINER */
        main {
            max-width: 1440px;
            margin: 0 auto;
            padding: 32px;
        }

        /* FILTER & TOOLBAR */
        .toolbar {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 28px;
            background: var(--bg-card);
            padding: 16px 24px;
            border-radius: var(--radius-lg);
            border: 1px solid var(--border-color);
            backdrop-filter: blur(12px);
        }

        .search-box {
            position: relative;
            flex: 1;
            min-width: 260px;
        }

        .search-box input {
            width: 100%;
            padding: 12px 16px 12px 42px;
            border-radius: var(--radius-md);
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color);
            color: #fff;
            font-size: 14px;
            outline: none;
            transition: all 0.2s ease;
        }

        .search-box input:focus {
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
        }

        .search-icon {
            position: absolute;
            left: 14px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-dim);
            pointer-events: none;
        }

        .filters-group {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        select {
            padding: 12px 16px;
            border-radius: var(--radius-md);
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            font-size: 14px;
            outline: none;
            cursor: pointer;
        }

        select:focus {
            border-color: var(--accent-primary);
        }

        /* GALLERY GRID */
        .video-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 24px;
        }

        .video-card {
            background: var(--bg-card);
            border-radius: var(--radius-lg);
            border: 1px solid var(--border-color);
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            cursor: pointer;
            display: flex;
            flex-direction: column;
            position: relative;
        }

        .video-card:hover {
            transform: translateY(-4px);
            border-color: var(--border-accent);
            box-shadow: var(--shadow-glow);
            background: var(--bg-card-hover);
        }

        .thumb-container {
            position: relative;
            width: 100%;
            aspect-ratio: 9/16;
            background: #000;
            overflow: hidden;
        }

        .thumb-img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.4s ease;
        }

        .video-card:hover .thumb-img {
            transform: scale(1.05);
        }

        .play-overlay-icon {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%) scale(0.8);
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: rgba(99, 102, 241, 0.85);
            backdrop-filter: blur(4px);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
            opacity: 0;
            transition: all 0.3s ease;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        }

        .video-card:hover .play-overlay-icon {
            opacity: 1;
            transform: translate(-50%, -50%) scale(1);
        }

        .duration-tag {
            position: absolute;
            bottom: 12px;
            right: 12px;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            padding: 4px 8px;
            border-radius: var(--radius-sm);
            font-size: 12px;
            font-weight: 600;
            color: #fff;
        }

        .niche-tag {
            position: absolute;
            top: 12px;
            left: 12px;
            background: rgba(99, 102, 241, 0.85);
            backdrop-filter: blur(4px);
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #fff;
        }

        .card-details {
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex: none;
        }

        .card-title {
            font-size: 15px;
            font-weight: 600;
            line-height: 1.35;
            color: #fff;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .card-meta {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 2px;
        }

        /* SPOTLIGHT INSPECTOR MODAL */
        .modal-backdrop {
            position: fixed;
            inset: 0;
            z-index: 500;
            background: rgba(5, 8, 15, 0.9);
            backdrop-filter: blur(16px);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.25s ease;
        }

        .modal-backdrop.active {
            opacity: 1;
            pointer-events: auto;
        }

        .inspector-card {
            background: #0d1322;
            border: 1px solid rgba(99, 102, 241, 0.35);
            border-radius: 20px;
            max-width: 860px;
            width: 100%;
            max-height: 88vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 25px 60px -10px rgba(0, 0, 0, 0.8), 0 0 35px rgba(99, 102, 241, 0.2);
            position: relative;
        }

        /* Pinned Top Bar */
        .inspector-top-bar {
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(13, 19, 34, 0.98);
            backdrop-filter: blur(14px);
            border-bottom: 1px solid var(--border-color);
            padding: 12px 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-shrink: 0;
        }

        .modal-close-btn {
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.25);
            color: #fff;
            width: 38px;
            height: 38px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            font-weight: 700;
            transition: all 0.2s ease;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
        }

        .modal-close-btn:hover {
            background: rgba(239, 68, 68, 0.9);
            border-color: rgba(239, 68, 68, 1);
            transform: scale(1.08);
        }

        /* Inspector Content */
        .inspector-content {
            display: flex;
            flex-direction: row;
            overflow-y: auto;
            flex: 1;
        }

        .inspector-preview {
            position: relative;
            flex: 1;
            min-width: 320px;
            max-width: 400px;
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            cursor: pointer;
        }

        .inspector-thumb {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.3s ease;
        }

        .inspector-preview:hover .inspector-thumb {
            transform: scale(1.04);
        }

        .thumb-gradient-overlay {
            position: absolute;
            inset: 0;
            background: radial-gradient(circle at center, transparent 30%, rgba(0, 0, 0, 0.6) 100%);
            pointer-events: none;
        }

        .play-btn-large {
            position: absolute;
            width: 64px;
            height: 64px;
            border-radius: 50%;
            background: var(--accent-gradient);
            border: none;
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            box-shadow: 0 0 30px rgba(99, 102, 241, 0.6);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            z-index: 2;
        }

        .play-btn-large:hover {
            transform: scale(1.1);
            box-shadow: 0 0 40px rgba(99, 102, 241, 0.9);
        }

        .inspector-body {
            flex: 1.2;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            overflow-y: auto;
        }

        .inspector-title {
            font-size: 18px;
            font-weight: 700;
            line-height: 1.35;
            color: #fff;
        }

        .inspector-meta-row {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            font-size: 12.5px;
            color: var(--text-muted);
        }

        .meta-item {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color);
            padding: 4px 8px;
            border-radius: var(--radius-sm);
        }

        .script-section {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .script-header {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-main);
        }

        .script-box {
            background: rgba(10, 15, 28, 0.85);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 12px 14px;
            font-size: 13px;
            line-height: 1.55;
            color: #cbd5e1;
            max-height: 160px;
            overflow-y: auto;
            white-space: pre-wrap;
        }

        .inspector-actions {
            display: flex;
            gap: 10px;
            margin-top: auto;
            flex-wrap: wrap;
        }

        .play-btn {
            flex: 1.4;
            min-width: 140px;
        }

        .download-btn {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
            color: #e2e8f0;
            text-decoration: none;
            justify-content: center;
            flex: 1;
            min-width: 110px;
        }

        .download-btn:hover {
            background: rgba(255, 255, 255, 0.15);
            color: #fff;
        }

        .theater-top-bar {
            position: absolute;
            top: 16px;
            left: 16px;
            right: 16px;
            z-index: 1000;
            display: flex;
            align-items: center;
            justify-content: space-between;
            pointer-events: auto;
        }

        .close-btn {
            background: rgba(0, 0, 0, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.25);
            color: #fff;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            backdrop-filter: blur(8px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
            transition: all 0.2s ease;
        }

        .close-btn:hover {
            background: rgba(239, 68, 68, 0.9);
            border-color: rgba(239, 68, 68, 1);
            transform: scale(1.08);
        }

        /* MOBILE RESPONSIVE STYLES & TOUCH OPTIMIZATION */
        @media (max-width: 768px) {
            header {
                padding: 12px 16px;
                flex-direction: column;
                gap: 12px;
                align-items: flex-start;
            }

            .header-stats {
                width: 100%;
                justify-content: space-between;
            }

            main {
                padding: 16px;
            }

            .toolbar {
                padding: 12px 14px;
                flex-direction: column;
                align-items: stretch;
                gap: 12px;
            }

            .search-box {
                width: 100%;
                min-width: 0;
            }

            .filters-group {
                width: 100%;
                display: flex;
                gap: 8px;
            }

            .filters-group select {
                flex: 1;
                width: 50%;
                font-size: 13px;
                padding: 10px;
            }

            /* Single column per row, ~70% screen height per card */
            .video-grid {
                grid-template-columns: 1fr;
                gap: 24px;
                max-width: 480px;
                margin: 0 auto;
            }

            .video-card {
                height: auto;
                max-height: 70vh;
                max-height: 70dvh;
                display: flex;
                flex-direction: column;
                border-radius: 16px;
            }

            .thumb-container {
                height: 52vh;
                height: 52dvh;
                max-height: 480px;
                width: 100%;
                position: relative;
                overflow: hidden;
                aspect-ratio: auto;
                flex: none;
            }

            .thumb-img {
                width: 100%;
                height: 100%;
                object-fit: cover;
                object-position: center;
            }

            .card-details {
                padding: 12px 16px 14px;
                gap: 5px;
                flex: none;
            }

            .card-title {
                font-size: 15px;
                line-height: 1.35;
            }

            .card-meta {
                font-size: 12px;
                margin-top: 0;
            }

            /* INSPECTOR MODAL ON MOBILE */
            .modal-backdrop {
                padding: 14px 12px;
                align-items: center;
                justify-content: center;
            }

            .inspector-card {
                max-height: 90vh;
                max-height: 90dvh;
                width: 100%;
                max-width: 480px;
                border-radius: 20px;
            }

            .inspector-top-bar {
                padding: 12px 16px;
            }

            .inspector-content {
                flex-direction: column;
            }

            /* Well-proportioned vertical preview */
            .inspector-preview {
                min-width: 0;
                max-width: 100%;
                width: calc(100% - 24px);
                height: 35vh;
                height: 35dvh;
                max-height: 330px;
                min-height: 220px;
                flex: none;
                border-radius: 12px;
                margin: 12px 12px 0;
            }

            .inspector-thumb {
                object-fit: cover;
                object-position: center 25%;
            }

            .play-btn-large {
                width: 52px;
                height: 52px;
            }

            .play-btn-large svg {
                width: 24px;
                height: 24px;
            }

            .inspector-body {
                padding: 14px 16px 18px;
                gap: 12px;
            }

            .inspector-title {
                font-size: 16px;
                line-height: 1.35;
            }

            .inspector-meta-row {
                font-size: 12px;
                gap: 8px;
            }

            .script-section {
                gap: 6px;
            }

            .script-header {
                font-size: 12.5px;
                font-weight: 600;
            }

            .script-box {
                max-height: 140px;
                font-size: 12.5px;
                line-height: 1.55;
                padding: 10px 12px;
            }

            .inspector-actions {
                gap: 10px;
                margin-top: auto;
                padding-top: 4px;
            }

            .action-btn {
                padding: 10px 14px;
                font-size: 13.5px;
                border-radius: 10px;
            }

            .modal-close-btn {
                width: 36px;
                height: 36px;
                font-size: 18px;
            }

            .play-overlay-icon {
                opacity: 1;
                transform: translate(-50%, -50%) scale(0.9);
            }

            .custom-controls {
                opacity: 1;
                padding: 16px 16px 12px;
            }

            .ctrl-btn {
                min-width: 44px;
                min-height: 44px;
                font-size: 20px;
            }

            .toast-banner {
                bottom: 16px;
                right: 16px;
                left: 16px;
                padding: 12px 16px;
            }
        }

        @media (max-width: 480px) {
            .video-grid {
                grid-template-columns: 1fr;
                gap: 20px;
            }
            .video-card {
                height: auto;
                max-height: 70vh;
                max-height: 70dvh;
            }
            .thumb-container {
                height: 50vh;
                height: 50dvh;
                max-height: 440px;
                flex: none;
            }
            .brand-title {
                font-size: 16px;
            }
            .card-details {
                padding: 10px 14px 12px;
                gap: 4px;
                flex: none;
            }
            .card-title {
                font-size: 14px;
            }
            .card-meta {
                font-size: 11px;
                margin-top: 0;
            }
        }

        /* FULLSCREEN VIDEO THEATER PLAYER */
        .fullscreen-player-modal {
            position: fixed;
            inset: 0;
            z-index: 300;
            background: #000;
            display: flex;
            flex-direction: column;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }

        .fullscreen-player-modal.active {
            opacity: 1;
            pointer-events: auto;
        }

        .video-viewport {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
            background: #000;
        }

        video {
            max-width: 100%;
            max-height: 100%;
            width: auto;
            height: auto;
            outline: none;
        }

        .custom-controls {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            background: linear-gradient(to top, rgba(0,0,0,0.95), transparent);
            padding: 24px 32px 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            opacity: 0;
            transition: opacity 0.3s ease;
        }

        .video-viewport:hover .custom-controls {
            opacity: 1;
        }

        .scrub-bar {
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.2);
            border-radius: 3px;
            cursor: pointer;
            position: relative;
        }

        .scrub-progress {
            height: 100%;
            background: var(--accent-gradient);
            border-radius: 3px;
            width: 0%;
        }

        .controls-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: #fff;
        }

        .controls-left, .controls-right {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .ctrl-btn {
            background: none;
            border: none;
            color: #fff;
            cursor: pointer;
            font-size: 18px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 6px;
            border-radius: 6px;
            transition: background 0.2s ease;
        }

        .ctrl-btn:hover {
            background: rgba(255, 255, 255, 0.15);
        }

        .time-display {
            font-size: 13px;
            color: var(--text-muted);
            font-weight: 500;
        }

        /* TOAST NOTIFICATION */
        .toast-banner {
            position: fixed;
            bottom: 32px;
            right: 32px;
            z-index: 400;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--accent-primary);
            box-shadow: var(--shadow-glow);
            border-radius: var(--radius-lg);
            padding: 16px 24px;
            display: flex;
            align-items: center;
            gap: 16px;
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s ease;
        }

        .toast-banner.show {
            transform: translateY(0);
            opacity: 1;
        }

        .action-btn {
            background: var(--accent-gradient);
            color: #fff;
            border: none;
            padding: 10px 20px;
            border-radius: var(--radius-md);
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: opacity 0.2s ease;
        }

        .action-btn:hover {
            opacity: 0.9;
        }
    </style>
</head>
<body>

    <header>
        <div class="brand">
            <div class="brand-icon">🎬</div>
            <div class="brand-title">Rare Studio</div>
        </div>
        <div class="header-stats">
            <div class="status-pill">
                <div class="pulse-dot"></div>
                <span>Server Active</span>
            </div>
            <span id="videoCountBadge" style="font-size: 14px; color: var(--text-muted);">0 Videos</span>
        </div>
    </header>

    <main>
        <div class="toolbar">
            <div class="search-box">
                <span class="search-icon">🔍</span>
                <input type="text" id="searchInput" placeholder="Search by title, topic, or script narration...">
            </div>
            <div class="filters-group">
                <select id="nicheSelect">
                    <option value="all">All Niches</option>
                </select>
                <select id="sortSelect">
                    <option value="newest">Newest First</option>
                    <option value="oldest">Oldest First</option>
                    <option value="title">Title A-Z</option>
                    <option value="duration">Duration</option>
                </select>
            </div>
        </div>

        <div class="video-grid" id="videoGrid"></div>
    </main>

    <!-- SPOTLIGHT INSPECTOR MODAL -->
    <div class="modal-backdrop" id="inspectorModal">
        <div class="inspector-card">
            <!-- Top Bar: Niche and X icon -->
            <div class="inspector-top-bar">
                <span id="inspectorNiche" class="niche-tag" style="position: static; display: inline-block;">NICHE</span>
                <button class="modal-close-btn" id="closeInspectorBtn" title="Close" aria-label="Close">✕</button>
            </div>

            <!-- Inspector Content (Compact thumbnail + metadata + script + actions) -->
            <div class="inspector-content">
                <div class="inspector-preview" id="inspectorPreviewBox" title="Click to play in fullscreen">
                    <img id="inspectorThumbImg" class="inspector-thumb" src="" alt="Thumbnail">
                    <div class="thumb-gradient-overlay"></div>
                    <button class="play-btn-large" id="launchFullscreenPlayBtn" title="Play in fullscreen">
                        <svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M8 5v14l11-7z"/>
                        </svg>
                    </button>
                </div>
                <div class="inspector-body">
                    <h2 id="inspectorTitle" class="inspector-title">Video Title</h2>
                    <div class="inspector-meta-row">
                        <span class="meta-item">⏱️ <strong id="inspectorDuration">--</strong></span>
                        <span class="meta-item">💾 <strong id="inspectorSize">--</strong></span>
                        <span class="meta-item">📅 <strong id="inspectorDate">--</strong></span>
                    </div>
                    <div class="script-section">
                        <div class="script-header">📝 Narrative Script</div>
                        <div id="inspectorScript" class="script-box">No script available.</div>
                    </div>
                    <div class="inspector-actions">
                        <button class="action-btn play-btn" id="inspectorPlayBtn">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                            <span>Play Fullscreen</span>
                        </button>
                        <a id="inspectorDownloadBtn" class="action-btn download-btn" download>
                            <span>⬇ Download</span>
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- FULLSCREEN THEATER PLAYER MODAL -->
    <div class="fullscreen-player-modal" id="theaterModal">
        <div class="video-viewport" id="videoViewport">
            <button class="close-btn" id="closeTheaterBtn" style="position: absolute; top: 16px; right: 16px; z-index: 1000;" title="Close player">✕</button>
            <video id="mainVideoPlayer" playsinline></video>
            <div class="custom-controls">
                <div class="scrub-bar" id="scrubBar">
                    <div class="scrub-progress" id="scrubProgress"></div>
                </div>
                <div class="controls-row">
                    <div class="controls-left">
                        <button class="ctrl-btn" id="playPauseToggle">▶</button>
                        <button class="ctrl-btn" id="muteToggle">🔊</button>
                        <span class="time-display" id="timeDisplay">0:00 / 0:00</span>
                    </div>
                    <div class="controls-right">
                        <select id="speedSelect" style="padding: 4px 8px; font-size: 12px; background: rgba(255,255,255,0.1); border:none; color:#fff; border-radius:4px;">
                            <option value="0.5">0.5x</option>
                            <option value="1.0" selected>1.0x</option>
                            <option value="1.25">1.25x</option>
                            <option value="1.5">1.5x</option>
                            <option value="2.0">2.0x</option>
                        </select>
                        <button class="ctrl-btn" id="fullscreenToggle">⛶</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- TOAST NOTIFICATION -->
    <div class="toast-banner" id="toastBanner">
        <span style="font-size: 24px;">🎬</span>
        <div>
            <div style="font-weight: 700; color: #fff;" id="toastTitle">New AI Video Ready!</div>
            <div style="font-size: 13px; color: var(--text-muted);" id="toastSubtitle">Click to inspect and watch</div>
        </div>
    </div>

    <script>
        let allVideos = [];
        let activeVideo = null;
        let lastSeenMtime = 0;

        async function fetchVideos() {
            try {
                const res = await fetch('/api/videos');
                const data = await res.json();
                allVideos = data.videos || [];
                document.getElementById('videoCountBadge').textContent = `${allVideos.length} Videos`;
                updateNicheOptions();
                renderGallery();
                
                if (allVideos.length > 0 && allVideos[0].mtime > lastSeenMtime) {
                    if (lastSeenMtime > 0) {
                        triggerNewVideoToast(allVideos[0]);
                    }
                    lastSeenMtime = allVideos[0].mtime;
                }
            } catch (err) {
                console.error('Failed to fetch videos:', err);
            }
        }

        function updateNicheOptions() {
            const select = document.getElementById('nicheSelect');
            const currentVal = select.value;
            const niches = setOfNiches();
            
            select.innerHTML = '<option value="all">All Niches</option>';
            niches.forEach(n => {
                const opt = document.createElement('option');
                opt.value = n;
                opt.textContent = n.replace('_', ' ').toUpperCase();
                select.appendChild(opt);
            });
            select.value = currentVal;
        }

        function setOfNiches() {
            const s = new Set();
            allVideos.forEach(v => { if (v.niche) s.add(v.niche); });
            return Array.from(s);
        }

        function renderGallery() {
            const grid = document.getElementById('videoGrid');
            grid.innerHTML = '';

            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const nicheFilter = document.getElementById('nicheSelect').value;
            const sortBy = document.getElementById('sortSelect').value;

            let filtered = allVideos.filter(v => {
                const matchesSearch = v.title.toLowerCase().includes(searchTerm) || 
                                      (v.topic && v.topic.toLowerCase().includes(searchTerm)) ||
                                      (v.script && v.script.toLowerCase().includes(searchTerm));
                const matchesNiche = nicheFilter === 'all' || v.niche === nicheFilter;
                return matchesSearch && matchesNiche;
            });

            filtered.sort((a, b) => {
                if (sortBy === 'newest') return b.mtime - a.mtime;
                if (sortBy === 'oldest') return a.mtime - b.mtime;
                if (sortBy === 'title') return a.title.localeCompare(b.title);
                if (sortBy === 'duration') return b.duration_seconds - a.duration_seconds;
                return 0;
            });

            filtered.forEach(v => {
                const card = document.createElement('div');
                card.className = 'video-card';
                card.onclick = () => openInspector(v);

                card.innerHTML = `
                    <div class="thumb-container">
                        <img class="thumb-img" src="${v.thumbnail_url || ''}" alt="${v.title}">
                        <div class="niche-tag">${v.niche || 'VIDEO'}</div>
                        <div class="play-overlay-icon">
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M8 5v14l11-7z"/>
                            </svg>
                        </div>
                        <div class="duration-tag">${v.duration_seconds ? v.duration_seconds + 's' : 'MP4'}</div>
                    </div>
                    <div class="card-details">
                        <div class="card-title">${v.title}</div>
                        <div class="card-meta">
                            <span>${v.mtime_fmt.split(' ')[0]}</span>
                            <span>${v.size_fmt}</span>
                        </div>
                    </div>
                `;
                grid.appendChild(card);
            });
        }

        function openInspector(video) {
            activeVideo = video;
            document.getElementById('inspectorTitle').textContent = video.title;
            document.getElementById('inspectorNiche').textContent = (video.niche || 'VIDEO').toUpperCase();
            document.getElementById('inspectorDuration').textContent = video.duration_seconds ? `${video.duration_seconds}s` : 'MP4';
            document.getElementById('inspectorSize').textContent = video.size_fmt;
            document.getElementById('inspectorDate').textContent = video.mtime_fmt;
            document.getElementById('inspectorScript').textContent = video.script || video.topic || 'No script details recorded.';
            document.getElementById('inspectorThumbImg').src = video.thumbnail_url || '';
            document.getElementById('inspectorDownloadBtn').href = video.download_url;

            const contentArea = document.querySelector('.inspector-content');
            if (contentArea) contentArea.scrollTop = 0;

            document.getElementById('inspectorModal').classList.add('active');
        }

        function closeInspector() {
            document.getElementById('inspectorModal').classList.remove('active');
        }

        function playFullscreen(video) {
            if (!video) return;
            closeInspector();

            const modal = document.getElementById('theaterModal');
            const player = document.getElementById('mainVideoPlayer');
            player.src = video.stream_url;

            modal.classList.add('active');
            player.play();

            // Request HTML5 browser fullscreen
            const viewport = document.getElementById('videoViewport');
            if (viewport.requestFullscreen) {
                viewport.requestFullscreen().catch(() => {});
            } else if (viewport.webkitRequestFullscreen) {
                viewport.webkitRequestFullscreen();
            }
        }

        function closeTheater() {
            const modal = document.getElementById('theaterModal');
            const player = document.getElementById('mainVideoPlayer');
            player.pause();
            player.src = '';
            modal.classList.remove('active');
            if (document.fullscreenElement) {
                document.exitFullscreen().catch(() => {});
            }
        }

        function triggerNewVideoToast(video) {
            const toast = document.getElementById('toastBanner');
            document.getElementById('toastTitle').textContent = `New AI Video: "${video.title}"`;
            toast.classList.add('show');
            
            // Synth audio alert chime
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(587.33, ctx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.3);
                gain.gain.setValueAtTime(0.2, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.3);
            } catch(e) {}

            setTimeout(() => toast.classList.remove('show'), 5000);
        }

        // EVENT LISTENERS
        document.getElementById('searchInput').addEventListener('input', renderGallery);
        document.getElementById('nicheSelect').addEventListener('change', renderGallery);
        document.getElementById('sortSelect').addEventListener('change', renderGallery);
        
        // Modal Close Buttons
        document.getElementById('closeInspectorBtn').onclick = closeInspector;
        document.getElementById('closeTheaterBtn').onclick = closeTheater;

        // Play triggers
        document.getElementById('inspectorPlayBtn').onclick = () => playFullscreen(activeVideo);
        document.getElementById('launchFullscreenPlayBtn').onclick = (e) => {
            e.stopPropagation();
            playFullscreen(activeVideo);
        };
        if (document.getElementById('inspectorPreviewBox')) {
            document.getElementById('inspectorPreviewBox').onclick = () => playFullscreen(activeVideo);
        }

        // Backdrop click to close modal & return to video list
        document.getElementById('inspectorModal').addEventListener('click', (e) => {
            if (e.target.id === 'inspectorModal') closeInspector();
        });
        document.getElementById('theaterModal').addEventListener('click', (e) => {
            if (e.target.id === 'theaterModal' || e.target.id === 'videoViewport') closeTheater();
        });

        const mainVideo = document.getElementById('mainVideoPlayer');
        const playPauseBtn = document.getElementById('playPauseToggle');
        const muteBtn = document.getElementById('muteToggle');
        const scrubProgress = document.getElementById('scrubProgress');

        playPauseBtn.onclick = () => {
            if (mainVideo.paused) { mainVideo.play(); playPauseBtn.textContent = '⏸'; }
            else { mainVideo.pause(); playPauseBtn.textContent = '▶'; }
        };

        muteBtn.onclick = () => {
            mainVideo.muted = !mainVideo.muted;
            muteBtn.textContent = mainVideo.muted ? '🔇' : '🔊';
        };

        mainVideo.addEventListener('timeupdate', () => {
            if (!mainVideo.duration) return;
            const pct = (mainVideo.currentTime / mainVideo.duration) * 100;
            scrubProgress.style.width = `${pct}%`;
            
            const curM = Math.floor(mainVideo.currentTime / 60);
            const curS = Math.floor(mainVideo.currentTime % 60).toString().padStart(2, '0');
            const durM = Math.floor(mainVideo.duration / 60);
            const durS = Math.floor(mainVideo.duration % 60).toString().padStart(2, '0');
            document.getElementById('timeDisplay').textContent = `${curM}:${curS} / ${durM}:${durS}`;
        });

        document.getElementById('scrubBar').onclick = (e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const pct = (e.clientX - rect.left) / rect.width;
            if (mainVideo.duration) mainVideo.currentTime = pct * mainVideo.duration;
        };

        document.getElementById('speedSelect').onchange = (e) => {
            mainVideo.playbackRate = parseFloat(e.target.value);
        };

        document.getElementById('fullscreenToggle').onclick = () => {
            const viewport = document.getElementById('videoViewport');
            if (!document.fullscreenElement) {
                viewport.requestFullscreen().catch(() => {});
            } else {
                document.exitFullscreen().catch(() => {});
            }
        };

        // KEYBOARD SHORTCUTS
        window.addEventListener('keydown', (e) => {
            if (document.activeElement.tagName === 'INPUT') return;
            if (e.code === 'Space') {
                e.preventDefault();
                playPauseBtn.click();
            } else if (e.code === 'KeyF') {
                document.getElementById('fullscreenToggle').click();
            } else if (e.code === 'Escape') {
                closeTheater();
                closeInspector();
            } else if (e.code === 'ArrowRight') {
                mainVideo.currentTime = Math.min(mainVideo.duration, mainVideo.currentTime + 5);
            } else if (e.code === 'ArrowLeft') {
                mainVideo.currentTime = Math.max(0, mainVideo.currentTime - 5);
            }
        });

        // INIT & POLLING
        fetchVideos();
        setInterval(fetchVideos, 3000);
    </script>
</body>
</html>
"""

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rare Studio Video Gallery & Web Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to run gallery web server on")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to listen on")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")
    args = parser.parse_args()

    port = args.port
    server_address = (args.host, port)
    
    try:
        httpd = ThreadingHTTPServer(server_address, GalleryRequestHandler)
    except OSError as e:
        print(f"[Gallery] Error starting server on port {port}: {e}")
        sys.exit(1)

    ips = get_local_ips()
    print("\n==========================================================")
    print("🎬 MoneyPrinter Studio - Video Gallery & Player")
    print("==========================================================")
    print(f" Local URL:   http://localhost:{port}")
    for ip in ips:
        if ip != "127.0.0.1":
            print(f" Network URL: http://{ip}:{port}")
    print("==========================================================")
    print(" Press Ctrl+C to stop the server\n")

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1), webbrowser.open(f"http://localhost:{port}")), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Gallery] Stopping video gallery server...")
        httpd.server_close()

if __name__ == "__main__":
    main()
