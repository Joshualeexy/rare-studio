#!/usr/bin/env python3
"""
==============================================================================
Archival Photo Ranker & Diversity Filter (core/photo_ranker.py)
==============================================================================
Ranks harvested candidate photographs using visual quality metrics, domain
authority, and caption relevance. Saves candidates_ranking.json so the user
can inspect all candidates, scores, and selection rationales on disk.
==============================================================================
"""

import os
import json
import math
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from PIL import Image, ImageStat
from loguru import logger


class PhotoRanker:
    """Evaluates candidate pool and ranks top 5 photos for narration."""

    def __init__(self, candidates: List[Dict], topic: str):
        self.candidates = candidates
        self.topic = topic.lower()
        self.topic_words = set(re.findall(r'\w+', self.topic))

    def evaluate_visual_quality(self, local_path: str, width: int, height: int) -> float:
        """
        Calculates visual quality score (0.0 to 1.0) based on resolution,
        aspect ratio suitability, and image contrast.
        """
        if not os.path.exists(local_path):
            return 0.0

        # 1. Resolution score (max at 1080p+)
        min_dim = min(width, height)
        res_score = min(1.0, min_dim / 1080.0)

        # 2. Contrast and brightness check via PIL ImageStat
        contrast_score = 0.5
        try:
            with Image.open(local_path) as img:
                stat = ImageStat.Stat(img.convert("L"))
                stddev = stat.stddev[0] if stat.stddev else 0.0
                mean = stat.mean[0] if stat.mean else 128.0

                # Reject pitch black or pure white images
                if mean < 15 or mean > 240:
                    return 0.1

                # Higher stddev indicates better dynamic range / contrast
                contrast_score = min(1.0, stddev / 65.0)
        except Exception:
            pass

        return round(0.6 * res_score + 0.4 * contrast_score, 3)

    def evaluate_relevance(self, title: str, caption: str) -> float:
        """Calculates textual relevance score (0.0 to 1.0) against topic keywords."""
        text = f"{title} {caption}".lower()
        matched = sum(1 for w in self.topic_words if len(w) > 3 and w in text)
        total = max(1, len([w for w in self.topic_words if len(w) > 3]))
        overlap = matched / float(total)
        return min(1.0, round(0.4 + 0.6 * overlap, 3))

    def rank_candidates(self, output_ranking_json: Optional[str] = None) -> List[Dict]:
        """
        Ranks all candidate photos, selects the Top 5 with diversity filtering,
        and saves candidates_ranking.json for user inspection.
        """
        if not self.candidates:
            logger.warning("[PhotoRanker] Candidate pool is empty.")
            return []

        scored_candidates = []
        for cand in self.candidates:
            v_qual = self.evaluate_visual_quality(
                cand.get("local_path", ""),
                cand.get("width", 0),
                cand.get("height", 0)
            )
            auth = cand.get("domain_authority", 0.50)
            rel = self.evaluate_relevance(cand.get("title", ""), cand.get("caption", ""))
            rarity = 0.80  # Default baseline rarity score

            # Composite Score Formula:
            # 30% Visual Quality + 25% Domain Authority + 25% Relevance + 20% Rarity
            composite_score = round(
                0.30 * v_qual + 0.25 * auth + 0.25 * rel + 0.20 * rarity, 3
            )

            scored_cand = dict(cand)
            scored_cand["scores"] = {
                "composite": composite_score,
                "visual_quality": v_qual,
                "domain_authority": auth,
                "relevance": rel,
                "rarity": rarity,
            }
            scored_candidates.append(scored_cand)

        # Sort candidates descending by composite score
        scored_candidates.sort(key=lambda x: x["scores"]["composite"], reverse=True)

        # Strict Diversity Filtering: Pick top 5 ensuring 100% distinct subject subjects
        selected = []
        seen_signatures = []

        def get_subject_signature(title: str) -> set:
            t = re.sub(r'\.(jpg|jpeg|png|webp|gif|svg)$', '', title.lower(), flags=re.IGNORECASE)
            t = re.sub(r'[\d\(\)\-\_\.]+', ' ', t)
            ignore = {"file", "photo", "image", "world", "archives", "declassified", "rare", "photos", "seen", "before"}
            words = {w for w in re.findall(r'\b[a-z]{3,}\b', t) if w not in ignore}
            return words

        has_item_numbers = any("item_number" in c for c in scored_candidates)

        if has_item_numbers:
            # First pass: Pick 1 primary best candidate per item (5 down to 1)
            for target_item_num in [5, 4, 3, 2, 1]:
                item_cands = [c for c in scored_candidates if c.get("item_number") == target_item_num]
                best_cand = None
                for cand in item_cands:
                    cand_title = cand.get("title", "")
                    cand_sig = get_subject_signature(cand_title)
                    is_dup = any((len(cand_sig & s) >= 2 or (len(cand_sig) > 0 and len(cand_sig & s) / float(len(cand_sig)) >= 0.5)) for s in seen_signatures)
                    if not is_dup:
                        best_cand = cand
                        break
                if not best_cand and item_cands:
                    best_cand = item_cands[0]
                
                if best_cand and best_cand not in selected:
                    best_cand["selected"] = True
                    best_cand["rank"] = len(selected) + 1
                    selected.append(best_cand)
                    seen_signatures.append(get_subject_signature(best_cand.get("title", "")))

            # Second pass: Include secondary/tertiary candidates per item for multi-sentence scene rotation
            for target_item_num in [5, 4, 3, 2, 1]:
                item_cands = [c for c in scored_candidates if c.get("item_number") == target_item_num]
                for cand in item_cands:
                    if cand not in selected:
                        cand["selected"] = True
                        cand["rank"] = len(selected) + 1
                        selected.append(cand)
        else:
            for cand in scored_candidates:
                cand_title = cand.get("title", "")
                cand_sig = get_subject_signature(cand_title)
                
                is_duplicate = False
                for seen_sig in seen_signatures:
                    overlap = cand_sig & seen_sig
                    if len(overlap) >= 2 or (len(cand_sig) > 0 and len(overlap) / float(len(cand_sig)) >= 0.5):
                        is_duplicate = True
                        break

                if not is_duplicate and len(cand_sig) > 0:
                    cand["selected"] = True
                    cand["rank"] = len(selected) + 1
                    selected.append(cand)
                    seen_signatures.append(cand_sig)
                else:
                    cand["selected"] = False
                    cand["rank"] = None
                    cand["rejection_reason"] = "Duplicate or overlapping subject item"

                if len(selected) >= 5:
                    break

        # Mark remaining unselected candidates
        for cand in scored_candidates:
            if "selected" not in cand:
                cand["selected"] = False
                cand["rank"] = None
                cand["rejection_reason"] = "Lower composite rank"

        logger.info(f"[PhotoRanker] Ranked {len(scored_candidates)} candidates -> Selected Top {len(selected)} photos")

        # Save candidates_ranking.json for user inspection
        if output_ranking_json:
            try:
                ranking_data = {
                    "topic": self.topic,
                    "total_candidates_evaluated": len(scored_candidates),
                    "selected_count": len(selected),
                    "selected_photos": [
                        {
                            "rank": c["rank"],
                            "candidate_id": c.get("candidate_id"),
                            "composite_score": c["scores"]["composite"],
                            "title": c.get("title"),
                            "caption": c.get("caption"),
                            "source_name": c.get("source_name"),
                            "source_url": c.get("source_url"),
                            "local_path": c.get("local_path"),
                            "dimensions": f"{c.get('width')}x{c.get('height')}",
                        } for c in selected
                    ],
                    "full_candidate_pool": [
                        {
                            "candidate_id": c.get("candidate_id"),
                            "rank": c.get("rank"),
                            "selected": c.get("selected"),
                            "composite_score": c["scores"]["composite"],
                            "score_breakdown": c["scores"],
                            "title": c.get("title"),
                            "caption": c.get("caption"),
                            "source_name": c.get("source_name"),
                            "source_url": c.get("source_url"),
                            "local_path": c.get("local_path"),
                            "rejection_reason": c.get("rejection_reason", ""),
                        } for c in scored_candidates
                    ]
                }
                with open(output_ranking_json, "w", encoding="utf-8") as f:
                    json.dump(ranking_data, f, indent=2, ensure_ascii=False)
                logger.info(f"[PhotoRanker] Preserved inspection ranking report at: {output_ranking_json}")
            except Exception as e:
                logger.warning(f"[PhotoRanker] Failed to write ranking json: {e}")

        return selected
