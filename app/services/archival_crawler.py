#!/usr/bin/env python3
"""
==============================================================================
Curated Article Archival Photo Harvester (app/services/archival_crawler.py)
==============================================================================
Harvests authentic, mind-blowing historical photographs & verified backstories
directly from top curated editorial publications (Interesting Engineering,
Rare Historical Photos, History Defined, All That's Interesting, etc.).
==============================================================================
"""

import os
import re
import asyncio
import hashlib
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional
import httpx
from PIL import Image
from loguru import logger
from core.asset_registry import registry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

SCRAPER_API_URL = "http://127.0.0.1:4050"


class ArchivalCrawler:
    """Fast async crawler harvesting candidate photos & backstories from trusted editorial articles."""

    def __init__(self, candidates_dir: str):
        self.candidates_dir = Path(candidates_dir)
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {"User-Agent": USER_AGENT}
        self.seen_urls = set()
        self.seen_hashes = set()

    async def fetch_curated_article_items(self, client: httpx.AsyncClient, topic: str, limit: int = 10) -> List[Dict]:
        """Queries stealth microservice to discover and extract items from trusted un-watermarked articles."""
        logger.info(f"[Crawler/ArticleHarvester] Discovering curated articles for: '{topic}'")
        try:
            url = f"{SCRAPER_API_URL}/api/discover_article"
            resp = await client.get(url, params={"topic": topic, "limit": limit}, timeout=35.0)
            if resp.status_code != 200:
                logger.warning(f"[Crawler/ArticleHarvester] Scraper API status: {resp.status_code}")
                return []

            data = resp.json()
            items = data.get("items", [])
            results = []

            for item in items:
                img_url = item.get("image_url")
                title = item.get("title") or "Archival Photograph"
                if not img_url or img_url in self.seen_urls:
                    continue
                if any(ext in img_url.lower() for ext in [".svg", ".gif", ".pdf", "logo", "avatar", "alamy", "getty", "shutterstock", "istock", "stockphoto"]):
                    continue

                # 3-Layer AssetRegistry Deduplication (URL + Title)
                if registry.score_candidate(img_url) == 0.0 or registry.score_candidate(title) == 0.0:
                    logger.info(f"[Crawler/Deduplication] Skipping previously used item: '{title[:45]}'")
                    continue

                results.append(item)
                self.seen_urls.add(img_url)

            return results
        except Exception as e:
            logger.warning(f"[Crawler/ArticleHarvester] Article discovery error for '{topic}': {e}")
            return []

    async def download_single_candidate(self, client: httpx.AsyncClient, candidate: Dict, idx: int) -> Optional[Dict]:
        """Downloads candidate image, verifies MD5 hash & Pillow dimensions, and returns item metadata."""
        img_url = candidate["image_url"]
        local_filename = f"candidate_{idx:03d}.jpg"
        local_filepath = self.candidates_dir / local_filename

        try:
            resp = await client.get(img_url, timeout=15.0, follow_redirects=True)
            if resp.status_code != 200 or len(resp.content) < 10000:
                return None

            # Layer 2: MD5 Binary Content Hash Deduplication
            img_hash = hashlib.md5(resp.content).hexdigest()
            if img_hash in self.seen_hashes:
                logger.info(f"[Crawler/Deduplication] Skipping duplicate image bytes (MD5: {img_hash[:8]})")
                return None
            self.seen_hashes.add(img_hash)

            with open(local_filepath, "wb") as f:
                f.write(resp.content)

            # Validate via Pillow
            with Image.open(local_filepath) as img:
                w, h = img.size
                if w < 400 or h < 400:
                    local_filepath.unlink(missing_ok=True)
                    return None

                if img.mode not in ("RGB", "L"):
                    rgb = img.convert("RGB")
                    rgb.save(local_filepath, "JPEG", quality=92)

                cand_data = dict(candidate)
                cand_data["candidate_id"] = f"candidate_{idx:03d}"
                cand_data["local_path"] = str(local_filepath)
                cand_data["width"] = w
                cand_data["height"] = h
                cand_data["aspect_ratio"] = round(w / float(h), 2)
                return cand_data

        except Exception as e:
            if local_filepath.exists():
                local_filepath.unlink(missing_ok=True)
            return None

    async def harvest_candidate_pool(self, topic: str, target_count: int = 15, five_items: Optional[List[Dict]] = None) -> List[Dict]:
        """
        Harvests 5 authentic, mind-blowing historical candidate photos & backstories
        directly from top curated un-watermarked editorial publications.
        """
        print(f"\n📸 [Archival Crawler] Starting Curated Article Photo Harvest for '{topic}'...")
        async with httpx.AsyncClient(headers=self.headers, follow_redirects=True) as client:
            valid_candidates = []
            download_counter = 0

            # 1. Primary Sourcing: Discover & Scrape Top Curated Historical Article
            raw_items = await self.fetch_curated_article_items(client, topic, limit=10)

            # If topic discovery returned fewer than 5 items, fallback to general rare photo article discovery
            if len(raw_items) < 5:
                print("   ⚠️ Topic article query yielded under 5 items. Sourcing from general rare historical photo vaults...")
                fb_items = await self.fetch_curated_article_items(client, "rare historical photos you have never seen before", limit=10)
                raw_items.extend(fb_items)

            # Download & verify candidates
            for idx, item in enumerate(raw_items, 1):
                item_num = 5 - len(valid_candidates)
                if item_num <= 0:
                    break

                download_counter += 1
                d_cand = await self.download_single_candidate(client, item, download_counter)
                if d_cand:
                    d_cand["item_number"] = item_num
                    valid_candidates.append(d_cand)
                    print(f"   📥 [Saved Item #{item_num} Photo #{len(valid_candidates)}] '{d_cand.get('title', '')[:55]}...'")
                    print(f"      • URL:   {d_cand.get('image_url')}")
                    print(f"      • Saved: {os.path.basename(d_cand.get('local_path'))} ({d_cand.get('width')}x{d_cand.get('height')})")
                    if len(valid_candidates) >= 5:
                        break

            logger.info(f"[Crawler] Successfully downloaded & verified {len(valid_candidates)} curated candidate photos to '{self.candidates_dir}'")
            return valid_candidates


def harvest_candidates_sync(topic: str, candidates_dir: str, target_count: int = 15, five_items: Optional[List[Dict]] = None) -> List[Dict]:
    """Synchronous wrapper for ArchivalCrawler."""
    crawler = ArchivalCrawler(candidates_dir=candidates_dir)
    return asyncio.run(crawler.harvest_candidate_pool(topic, target_count=target_count, five_items=five_items))
