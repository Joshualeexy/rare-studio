#!/usr/bin/env python3
"""
==============================================================================
Rare-Studio - Unified Main Entry Point
==============================================================================
Single CLI interface for Rare-Studio photo-first video generation:
  python main.py                                                      # Run single episode for Rare Photos niche
  python main.py run --topic "5 Rare Photos of WWII Archives"         # Run explicit topic
  python main.py series --count 4                                     # Batch generate 4 episodes
  python main.py gallery [--port 5050]                                # Start Web Studio Gallery UI
  python main.py reburn                                               # Re-burn karaoke subtitles on output videos
==============================================================================
"""

import os
import sys

# Guarantee rare-studio project root takes highest import priority
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import argparse

# Auto-reexec using project .venv python
def _ensure_venv():
    venv_py = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    if os.path.exists(venv_py) and os.path.abspath(sys.executable) != os.path.abspath(venv_py):
        os.execv(venv_py, [venv_py] + sys.argv)

_ensure_venv()

from loguru import logger


def main():
    parser = argparse.ArgumentParser(
        description="📸 Rare-Studio - Autonomous AI Short Video Generator for 'Top 5 Rare Photos You've Never Seen Before'",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands & Usage Examples:
  python main.py                                                      # Default single video episode
  python main.py run --topic "5 Rare Photos of WWII Archives"         # Custom explicit topic
  python main.py series --count 5                                     # Multi-episode batch run
  python main.py gallery --port 5050                                  # Web studio gallery player
  python main.py reburn                                               # Re-burn Whisper karaoke subtitles
"""
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand mode to run")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Generate a single video episode")
    run_parser.add_argument("--profile", default="rarely_seen", help="Niche profile slug or path (default: rarely_seen)")
    run_parser.add_argument("--topic", default=None, help="Explicit topic override")
    run_parser.add_argument("--episode", type=int, default=None, help="Episode number in series")
    run_parser.add_argument("--clear-state", action="store_true", help="Clear saved state")

    # Command: series
    series_parser = subparsers.add_parser("series", help="Generate a multi-episode batch")
    series_parser.add_argument("--profile", default="rarely_seen", help="Niche profile slug or path (default: rarely_seen)")
    series_parser.add_argument("--count", type=int, default=4, help="Number of episodes to generate (default: 4)")

    # Command: infinite
    infinite_parser = subparsers.add_parser("infinite", help="Run 24/7 continuous autonomous generator loop")
    infinite_parser.add_argument("--port", type=int, default=5050, help="Web gallery port (default: 5050)")
    infinite_parser.add_argument("--no-gallery", action="store_true", help="Disable auto-starting web gallery")

    # Command: worker
    worker_parser = subparsers.add_parser("worker", help="Run task worker execution engine")
    worker_parser.add_argument("--profile", default="rarely_seen", help="Niche profile slug or path (default: rarely_seen)")
    worker_parser.add_argument("--topic", default=None, help="Explicit topic override")
    worker_parser.add_argument("--episode", type=int, default=None, help="Episode number")
    worker_parser.add_argument("--clear-state", action="store_true", help="Clear saved state")

    # Command: gallery / server
    gallery_parser = subparsers.add_parser("gallery", help="Start Web Studio Video Gallery UI")
    gallery_parser.add_argument("--port", type=int, default=5050, help="Port to listen on (default: 5050)")
    gallery_parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")

    # Command: reburn
    reburn_parser = subparsers.add_parser("reburn", help="Re-burn Whisper karaoke subtitles onto output videos")
    reburn_parser.add_argument("--dir", default=None, help="Target directory (default: output/ and date folders)")
    reburn_parser.add_argument("--force", action="store_true", help="Force re-burn even if already burned")

    # Fallback / root flags for direct execution
    parser.add_argument("--profile", default="rarely_seen", help="Niche profile slug or path (default: rarely_seen)")
    parser.add_argument("--topic", default=None, help="Explicit topic override")
    parser.add_argument("--episode", type=int, default=None, help="Episode number")
    parser.add_argument("--clear-state", action="store_true", help="Clear saved state")
    parser.add_argument("--gallery", "--server", action="store_true", help="Start web gallery")

    args = parser.parse_args()

    cmd = args.command

    # Handle Web Gallery server
    if getattr(args, "gallery", False) or cmd in ("gallery", "server"):
        from runners.gallery import main as gallery_main
        port = getattr(args, "port", 5050)
        host = getattr(args, "host", "0.0.0.0")
        sys.argv = [sys.argv[0], "--port", str(port), "--host", host]
        gallery_main()
        return

    # Handle Series batch
    if cmd == "series":
        from runners.series import run_series
        run_series(args.profile, count=args.count)
        return

    # Handle Infinite generator
    if cmd == "infinite":
        port = getattr(args, "port", 5050)
        if not getattr(args, "no_gallery", False):
            from runners.gallery import ensure_gallery_server_running
            ensure_gallery_server_running(port=port)
        from runners.infinite import run_infinite_loop
        run_infinite_loop(gallery_port=port)
        return

    # Handle Re-burn subtitles
    if cmd == "reburn":
        from runners.reburn import main as reburn_main
        reburn_main()
        return

    # Handle Worker / Run single video (default mode)
    from runners.worker import run_worker_pipeline
    prof = getattr(args, "profile", "rarely_seen") or "rarely_seen"
    top = getattr(args, "topic", None)
    ep = getattr(args, "episode", None)
    clr = getattr(args, "clear_state", False)

    run_worker_pipeline(
        profile_path=prof,
        topic_override=top,
        clear_state=clr,
        episode_num=ep
    )


if __name__ == "__main__":
    main()
