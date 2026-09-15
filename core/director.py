"""
==============================================================================
AI Executive Movie Director & Production Supervisor (core/director.py)
==============================================================================
Acts as the creative director on set:
1. Plans the shot list (segmenting script into 3-second visual beats).
2. Classifies capture methods: STOCK_MOTION (real filmable footage) vs AI_GENERATIVE (unfilmable/subterranean/microscopic).
3. "The Dailies" Supervisor: Evaluates candidate stock videos before download, instantly rejecting misfires (e.g. ice fishing or scuba divers).
4. Directs ComfyUI SDXL with photorealistic scene prompts when stock footage fails.
==============================================================================
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from loguru import logger
from app.config import config
from app.services import llm


def _get_llm_config(profile: dict) -> dict:
    app_cfg = dict(config.app)
    llm_cfg = profile.get("llm", {}) if profile else {}
    provider = (llm_cfg.get("provider") or app_cfg.get("llm_provider", "deepseek")).lower()
    model = llm_cfg.get("model") or app_cfg.get(f"{provider}_model_name", "deepseek-chat")
    app_cfg["llm_provider"] = provider
    if provider == "ollama":
        app_cfg["ollama_model_name"] = model
        if not app_cfg.get("ollama_base_url"):
            app_cfg["ollama_base_url"] = "http://127.0.0.1:11434/v1"
    else:
        app_cfg[f"{provider}_model_name"] = model
    return app_cfg



@dataclass
class DirectorShot:
    index: int
    script_segment: str
    visual_description: str
    capture_method: str  # "STOCK_MOTION" | "AI_GENERATIVE"
    search_query: str
    fallback_query: str
    sdxl_prompt: str
    avoid_concepts: List[str] = field(default_factory=list)


class MovieDirector:
    """
    Supervises visual production, cinematography, and quality assurance.
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.niche_name = profile.get("niche", {}).get("name", "Rarely Seen")
        self.visual_cfg = profile.get("visual", {})
        self.profile_negatives = self.visual_cfg.get("negative_keywords", [])

    def plan_shotlist(self, script: str, topic: str, total_shots: int) -> List[DirectorShot]:
        """
        Deconstructs the narrative into a storyboard of exact shots with capture methods.
        """
        logger.info(f"[Movie Director] Planning cinematic storyboard for '{topic}' ({total_shots} shots)...")

        prompt = f"""You are the Executive Video Director for a high-retention "Rarely Seen" curiosity video.
Niche: {self.niche_name}
Subject: {topic}
Target Shot Count: {total_shots} shots (approximately 2.5 to 3.5 seconds each)

Script:
"{script}"

For EACH of the {total_shots} sequential shots in the script, you must direct:
1. "script_segment": The spoken words happening during this shot.
2. "visual_description": What the audience physically sees on screen.
3. "capture_method":
   - "AI_GENERATIVE": MUST be chosen for UNFILMABLE scenes where real video cameras cannot physically film the subject. This includes:
     * ALL extinct prehistoric beasts, dinosaurs, primordial serpents, and megafauna (e.g. Titanoboa, Megalodon, T-Rex, Spinosaurus). Real live specimens do not exist on Earth!
     * ALL mythical/legendary creatures, monsters, cryptids, and extraterrestrial beings.
     * Deep-time ancient events (asteroid impacts, ancient cataclysms, primordial earth).
     * Subglacial lakes, deep space anomalies, alien planets, subterranean micro-processes.
     NEVER choose STOCK_MOTION if the shot depicts an extinct creature, dinosaur, or monster!
   - "STOCK_MOTION": Real filmable world footage ONLY (drone flyovers, weather, landscapes, modern laboratories, excavations, real living nature, modern cities, archival documents).
4. "search_query": Concrete 2 to 3 word physical subject query for the photo (e.g. "baby mammoth museum", "polar bear iceberg greenland", "setenil de las bodegas rock", "rocket launch frog", "pillars of light japan"). NEVER search for abstract words, spoken dialogue, "hands holding photo", or physical album frames!
5. "fallback_query": Simple 1-2 word thematic fallback term (e.g. "mammoth", "iceberg", "rock", "rocket").
6. "sdxl_prompt": A vivid full-screen photorealistic 8k photo prompt describing the direct physical subject (e.g. "full-screen direct realistic photo of baby mammoth preserved in museum glass display, 8k, National Geographic, textless, no hands, no paper, no desk").
7. "avoid_concepts": List of forbidden visual concepts: ["hands", "holding", "album", "paper", "vinyl", "desk", "table", "broll", "actor", "cartoon"].

Return a JSON array of {total_shots} objects. Return ONLY raw valid JSON."""

        app_cfg = dict(_get_llm_config(self.profile))
        provider = app_cfg.get("llm_provider", "deepseek")
        model = app_cfg.get(f"{provider}_model_name", "deepseek-chat")
        logger.info(f"[Movie Director] Planning storyboard via '{model}' on {provider}...")

        response = None
        used_ollama = (provider == "ollama")
        try:
            response = llm._generate_response(prompt, app_config=app_cfg)
        except Exception as e:
            logger.warning(f"[Movie Director] Primary LLM failed: {e}")

        if not response or response.startswith("Error:"):
            logger.info("[Movie Director] Falling back to local Ollama (qwen3:8b)...")
            fallback_cfg = dict(app_cfg)
            fallback_cfg["llm_provider"] = "ollama"
            fallback_cfg["ollama_model_name"] = "qwen3:8b"
            fallback_cfg["ollama_base_url"] = "http://127.0.0.1:11434/v1"
            try:
                response = llm._generate_response(prompt, app_config=fallback_cfg)
                used_ollama = True
            except Exception as e:
                logger.warning(f"[Movie Director] Ollama fallback failed: {e}")

        # Offload Ollama model from memory if Ollama was actually used
        if used_ollama:
            try:
                import requests as _req
                _req.post("http://127.0.0.1:11434/api/generate", json={"model": "qwen3:8b", "keep_alive": 0}, timeout=2)
            except Exception:
                pass

        raw_shots = []
        try:
            cleaned = response.strip()
            if "```" in cleaned:
                parts = cleaned.split("```")
                cleaned = parts[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            parsed = json.loads(cleaned.strip())
            if isinstance(parsed, list):
                raw_shots = parsed
        except Exception as e:
            logger.warning(f"[Movie Director] Fallback storyboard parsing: {e}")

        # If LLM returned fewer or unparseable shots, generate safe defaults
        shots: List[DirectorShot] = []
        topic_clean = topic.strip()
        clean_subject = topic_clean.split(":")[0].strip()

        # Keywords that indicate unfilmable prehistoric / mythical entities
        unfilmable_creatures = {
            "titanoboa", "megalodon", "dunkleosteus", "spinosaurus", "quetzalcoatlus",
            "centipede", "dinosaur", "predator", "monster", "extinct", "creature", "beast",
            "leviathan", "kraken", "alien", "cryptid", "dragon", "mammoth"
        }
        topic_lower = topic_clean.lower()
        is_creature_topic = any(c in topic_lower for c in unfilmable_creatures)

        for idx in range(total_shots):
            item = raw_shots[idx] if idx < len(raw_shots) and isinstance(raw_shots[idx], dict) else {}
            
            cap_method = item.get("capture_method", "STOCK_MOTION").upper()
            if cap_method not in {"STOCK_MOTION", "AI_GENERATIVE"}:
                cap_method = "STOCK_MOTION"

            # Auto-enforce AI_GENERATIVE if the shot depicts an unfilmable creature or action
            shot_text = f"{item.get('visual_description', '')} {item.get('script_segment', '')}".lower()
            if is_creature_topic and any(w in shot_text for w in ["snake", "beast", "monster", "predator", "creature", "titanoboa", "crushing", "hunting", "attack", "extinct", "colossus"]):
                cap_method = "AI_GENERATIVE"

            # Clean search query (concise 2-4 visual physical words; do NOT prepend massive 60-char title)
            raw_q_val = item.get("search_query")
            if not raw_q_val or not isinstance(raw_q_val, str):
                raw_q_val = f"{clean_subject} documentary"
            search_q = re.sub(r'["\']', '', raw_q_val).strip()

            raw_fb_val = item.get("fallback_query")
            if not raw_fb_val or not isinstance(raw_fb_val, str):
                raw_fb_val = clean_subject
            fallback_q = re.sub(r'["\']', '', raw_fb_val).strip()

            # Avoid concepts
            avoid = item.get("avoid_concepts", [])
            if not isinstance(avoid, list):
                avoid = []
            combined_avoid = list(set([str(a).lower().strip() for a in avoid] + self.profile_negatives))

            # SDXL prompt - preserve director's specific prompt, or anchor to clean subject
            raw_sdxl = str(item.get("sdxl_prompt") or "").strip()
            if not raw_sdxl:
                sdxl_p = f"dramatic cinematic photorealistic shot of {clean_subject}, volumetric lighting, 8k, national geographic, textless"
            else:
                sdxl_p = f"{raw_sdxl}, cinematic lighting, 8k, national geographic, textless"

            shot = DirectorShot(
                index=idx + 1,
                script_segment=item.get("script_segment", ""),
                visual_description=item.get("visual_description", search_q),
                capture_method=cap_method,
                search_query=search_q,
                fallback_query=fallback_q,
                sdxl_prompt=sdxl_p,
                avoid_concepts=combined_avoid,
            )
            shots.append(shot)

        generative_count = sum(1 for s in shots if s.capture_method == "AI_GENERATIVE")
        stock_count = len(shots) - generative_count
        logger.info(f"[Movie Director] Storyboard ready: {stock_count} Real Motion Shots | {generative_count} Custom SDXL Shots.")
        return shots

    def judge_stock_clip(self, shot: DirectorShot, video_meta: dict) -> Tuple[bool, str]:
        """
        The Dailies Review: Inspects candidate video title, URL slug, and tags.
        Rejects misfires (gym/fitness, fashion/models, costumes, feet, letters, modern cars, etc.).
        """
        v_url = (video_meta.get("source_page") or video_meta.get("url") or "").lower()
        raw_tags = video_meta.get("tags") or []
        if isinstance(raw_tags, list):
            v_tags = " ".join(str(t).lower() for t in raw_tags)
        else:
            v_tags = str(raw_tags).lower()
        candidate_text = f"{v_url} {v_tags}"

        # 1. Fast Negative Keyword Rejection from shot and profile
        if shot and hasattr(shot, "avoid_concepts"):
            for avoid in shot.avoid_concepts:
                if avoid and avoid.lower() in candidate_text:
                    return False, f"Director rejected: matches forbidden concept '{avoid}' in {v_url}"

        # 2. Strict non-documentary disqualifiers:
        generic_trash = {
            "vlog", "bikini", "swimwear", "resort", "hotel", "cocktail", "party",
            "cooking", "recipe", "food", "meat", "eating", "restaurant",
            "workout", "gym", "fitness", "yoga", "bodybuilder", "muscle", "glutes",
            "underwear", "boxer", "lingerie", "fashion", "model", "catwalk", "glamour",
            "costume", "cosplay", "halloween", "toy", "board-game", "game-board",
            "chess", "letters", "alphabet", "scrabble", "pegboard",
            "feet", "foot", "barefoot", "pedicure", "heel", "toes",
            "modern-car", "automobile", "traffic", "parking", "parking-lot", "suv"
        }
        for trash in generic_trash:
            if trash in candidate_text:
                script_seg = getattr(shot, "script_segment", "").lower() if shot else ""
                if trash in {"feet", "foot", "barefoot"} and ("footprint" in script_seg or "walk" in script_seg):
                    continue
                return False, f"Director rejected: matches non-documentary tag '{trash}' in {v_url}"

        # 3. Reject generic waving flags unless script explicitly mentions a flag
        flag_terms = {"flag", "waving-flag", "national-flag", "capitol", "white-house"}
        script_seg = getattr(shot, "script_segment", "").lower() if shot else ""
        if any(f in candidate_text for f in flag_terms) and "flag" not in script_seg:
            return False, f"Director rejected: generic national flag/capitol misfire in '{v_url}'"

        return True, "Director approved"
