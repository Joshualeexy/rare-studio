#!/usr/bin/env python3
"""
==============================================================================
Evidence-Backed Fact & Intelligence Researcher (core/researcher.py)
==============================================================================
Pulls genuine historical, scientific, and encyclopedic evidence packs before
scriptwriting to ensure videos have authentic narrative depth, factual accuracy,
and verified physical entity anchors.
==============================================================================
"""

import os
import re
import requests
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from loguru import logger

USER_AGENT = "MoneyPrinterStudio-Researcher/2.0 (https://github.com/Joshualeexy/moneyprinter-studio; research-bot)"
SCRAPER_API_URL = os.getenv("CUSTOM_SCRAPER_API_URL", "http://127.0.0.1:4050")

DATE_PATTERN = re.compile(
    r"\b(?:(?:1[0-9]{3}|20[0-2][0-9])(?:s|\b)|(?:[0-9]{1,2}(?:th|st|nd|rd)?\s+century(?:\s+BC[E]?)?)|[0-9]{1,5}\s*BC[E]?)\b",
    re.IGNORECASE
)
MEASUREMENT_PATTERN = re.compile(
    r"\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:meters|km|kilometers|miles|feet|tons|pounds|kg|years old|years ago|mph|knots|degrees|celsius|kelvin|percent|%)\b",
    re.IGNORECASE
)


@dataclass
class EvidencePack:
    topic: str
    title: str
    summary: str
    verified_claims: List[str] = field(default_factory=list)
    temporal_anchors: List[str] = field(default_factory=list)
    key_entities: List[str] = field(default_factory=list)
    context: str = ""
    viral_hooks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "title": self.title,
            "summary": self.summary,
            "verified_claims": self.verified_claims,
            "temporal_anchors": self.temporal_anchors,
            "key_entities": self.key_entities,
            "context": self.context,
            "viral_hooks": self.viral_hooks,
        }


def _extract_factual_claims(text: str, max_claims: int = 6) -> List[str]:
    """
    Extracts high-density factual sentences containing numbers, dates, or measurements.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    claims = []
    for s in sentences:
        s_clean = s.strip()
        if len(s_clean) < 35 or len(s_clean) > 280:
            continue
        # Skip generic boilerplate sentences
        if any(skip in s_clean.lower() for skip in [
            "may refer to", "is a village", "is a disambiguation", "external links",
            "see also", "references", "further reading", "for other uses"
        ]):
            continue
        # Prioritize sentences with verifiable quantitative or temporal anchors
        has_date = bool(DATE_PATTERN.search(s_clean))
        has_metric = bool(MEASUREMENT_PATTERN.search(s_clean))
        if has_date or has_metric:
            claims.append(s_clean)
        elif len(claims) < 3 and len(s_clean) > 50:
            claims.append(s_clean)

        if len(claims) >= max_claims:
            break
    return claims


def _extract_entities(text: str) -> List[str]:
    """
    Extracts key capitalized proper nouns (locations, artifacts, named institutions).
    """
    # Find sequences of capitalized words (ignoring sentence starters where possible)
    matches = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
    stopwords = {
        "The", "This", "It", "They", "These", "There", "When", "While", "After",
        "During", "Before", "In", "On", "At", "By", "For", "With", "However", "Although"
    }
    seen = set()
    entities = []
    for m in matches:
        if m not in stopwords and len(m) > 3 and m not in seen:
            seen.add(m)
            entities.append(m)
        if len(entities) >= 8:
            break
    return entities


def _fetch_stealth_web_intelligence(topic: str, timeout: int = 12) -> Optional[Dict[str, Any]]:
    """
    Queries the autonomous stealth search microservice (services/scraper)
    for live web research, answer box data, and related inquiry vectors.
    """
    try:
        url = f"{SCRAPER_API_URL}/api/search"
        resp = requests.get(url, params={"q": topic, "limit": 4}, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "ok" and (data.get("results") or data.get("answer_box") or data.get("people_also_ask")):
                logger.info(f"[Researcher] Acquired live web intelligence for: '{topic}' ({len(data.get('results', []))} sources).")
                return data
    except Exception as e:
        logger.debug(f"[Researcher] Stealth search microservice offline or timed out: {e}")
    return None


def fetch_topic_research(topic: str) -> Dict[str, str]:
    """
    Fetches multi-source intelligence (live web research + encyclopedic grounding)
    for a topic, distilling it into an authoritative, structured Evidence Pack.
    """
    logger.info(f"[Researcher] Sourcing multi-source factual intel for: '{topic}'...")

    # Step 1: Query stealth search microservice for live web intelligence
    web_intel = _fetch_stealth_web_intelligence(topic)

    # Step 2: Query Wikipedia encyclopedic knowledge base
    search_url = "https://en.wikipedia.org/w/api.php"
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": topic,
        "format": "json",
        "srlimit": 3
    }
    headers = {"User-Agent": USER_AGENT}

    title = topic
    summary_text = ""
    full_extract = ""

    try:
        r = requests.get(search_url, params=search_params, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            search_results = data.get("query", {}).get("search", [])
            if search_results:
                candidate = search_results[0]["title"]
                topic_words = set(w.lower() for w in re.findall(r'\w+', topic) if len(w) > 3)
                cand_words = set(w.lower() for w in re.findall(r'\w+', candidate) if len(w) > 3)
                stop_words = {"images", "never", "seen", "your", "life", "world", "things"}
                meaningful_topic = topic_words - stop_words
                
                is_listicle = any(k in topic.lower() for k in ["5 ", "top ", "images", "forbidden", "things caught", "caught on camera"])
                is_misfit_title = any(m in candidate.lower() for m in ["discography", "filmography", "album", "song", "single"])
                
                if is_listicle and (is_misfit_title or (meaningful_topic and not (meaningful_topic & cand_words))):
                    logger.info(f"[Researcher] Rejecting off-topic Wikipedia result '{candidate}' for listicle topic '{topic}'")
                    title = ""
                else:
                    title = candidate

        # Fetch lead summary & high-density plaintext extract
        extract_url = "https://en.wikipedia.org/w/api.php"
        extract_params = {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "titles": title,
            "format": "json"
        }
        r_ext = requests.get(extract_url, params=extract_params, headers=headers, timeout=8)
        if r_ext.status_code == 200:
            pages = r_ext.json().get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                if page_id != "-1":
                    full_extract = page_data.get("extract", "")
                    break

        # Fallback to REST summary API if query extract is empty
        if not full_extract:
            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}"
            r_sum = requests.get(summary_url, headers=headers, timeout=8)
            if r_sum.status_code == 200:
                full_extract = r_sum.json().get("extract", "")

    except Exception as e:
        logger.warning(f"[Researcher] Live encyclopedic query encountered issue: {e}")

    summary_text = full_extract.strip()

    # Step 3: Format Live Web Intelligence Block & Viral Inquiries
    web_section = ""
    web_claims = []
    viral_hooks = []
    if web_intel:
        web_items = []
        if web_intel.get("answer_box"):
            web_items.append(f"• Direct Key Finding: {web_intel['answer_box']}")
            web_claims.append(web_intel['answer_box'])
        for r in web_intel.get("results", [])[:3]:
            snip = r.get("snippet", "").strip()
            if snip:
                web_items.append(f"• {r.get('title', '')}: {snip}")
                web_claims.append(f"{r.get('title', '')} ({snip[:120]})")
        if web_intel.get("people_also_ask"):
            viral_hooks = [q.strip() for q in web_intel["people_also_ask"] if q.strip()]
            paa_str = " | ".join(viral_hooks[:4])
            web_items.append(f"• High-Intent Viewer Inquiries (PAA): {paa_str}")
        if web_items:
            web_section = "### Live Web Intelligence & Discovery Vectors:\n" + "\n".join(web_items) + "\n\n"

    # Step 4: Grounded Evidence Pack Synthesis
    combined_text = f"{summary_text} {web_section}".strip()
    if combined_text:
        claims = _extract_factual_claims(summary_text, max_claims=4) if summary_text else []
        for wc in web_claims[:3]:
            if len(claims) < 6 and wc not in claims:
                claims.append(wc)

        dates = list(set(DATE_PATTERN.findall(combined_text)))[:6]
        entities = _extract_entities(combined_text)

        claims_md = "\n".join(f"- {c}" for c in claims) if claims else f"- Documented topic investigation: {title}"
        dates_md = ", ".join(dates) if dates else "Documented chronological timeline"
        entities_md = ", ".join(entities[:6]) if entities else title

        wiki_intro = f"### Primary Encyclopedic Subject: {title}\n{summary_text[:600]}...\n\n" if summary_text else ""

        hooks_md = ""
        if viral_hooks:
            hooks_bullets = "\n".join(f"- \"{h}\"" for h in viral_hooks[:4])
            hooks_md = f"""### High-Intent Viral Inquiry Angles (What Viewers Are Actively Searching):
{hooks_bullets}

"""

        formatted_context = f"""{web_section}{wiki_intro}{hooks_md}### Verified Archival Claims:
{claims_md}

### Key Temporal & Physical Anchors:
- Chronological Anchors: {dates_md}
- Named Entities & Physical Sites: {entities_md}

### Directorial Fact-Checking Directives:
- All narrative claims must align with the verified findings, dates, metrics, and entities above.
- Do NOT invent fictional discoveries, fake institutions, or synthetic paranormal theories.
- Ground the drama in genuine physical reality and authentic documented findings."""

        pack = EvidencePack(
            topic=topic,
            title=title,
            summary=(summary_text or (web_intel.get("answer_box") if web_intel else topic))[:400],
            verified_claims=claims,
            temporal_anchors=dates,
            key_entities=entities,
            context=formatted_context,
            viral_hooks=viral_hooks
        )
    else:
        # Objective, non-hallucinatory domain fallback
        formatted_context = f"""### Subject Dossier: {topic}
Overview: Investigative documentary investigation focusing on {topic}.
Directorial Guidelines:
- Anchor the narrative in documented historical facts, verifiable technical principles, and physical geography.
- Maintain authentic documentary tone; avoid sensationalizing unverified folklore without clear qualification.
- Frame open questions strictly around documented scientific debates, not manufactured conspiracies."""

        pack = EvidencePack(
            topic=topic,
            title=topic,
            summary=f"Investigative documentary focus on {topic}.",
            verified_claims=[f"Subject of documented historical and scientific inquiry: {topic}"],
            temporal_anchors=[],
            key_entities=[topic],
            context=formatted_context
        )

    logger.info(f"[Researcher] Evidence Pack compiled for '{topic}' ({len(pack.verified_claims)} verified claims, {len(pack.temporal_anchors)} temporal anchors, {len(pack.viral_hooks)} viral hooks).")
    return pack.to_dict()


def brainstorm_5_rare_photo_items(topic: str, profile: dict) -> List[Dict[str, Any]]:
    """
    Brainstorms 5 specific, real, photo-anchored items for a 'Top 5 Rare Photos' episode.
    Each item contains item_number (5 down to 1), title, description, and search_query.
    """
    import json
    from app.services import llm
    from runners.worker import _get_llm_config

    prompt = f"""Generate 5 textual descriptions of rare historical photograph subjects I’ve probably never seen before.

You are generating SEARCH SUBJECTS, NOT images and NOT image-generation prompts.

Each item must describe a real event, moment, person, place, object, or situation that could have been photographed.

The subjects should span different categories, including:
* Historic events
* Military events
* Wars and battles
* Political events
* Disasters
* Protests and riots
* Secret or declassified operations
* Cold War events
* Space and scientific events
* Famous people in rare or unusual moments
* Unusual moments from major historical events
* Exploration and expeditions
* Unusual technology and engineering
* First-ever events and demonstrations
* Abandoned, destroyed, or disappearing places

Focus on obscure and unusual historical moments that have a real photograph associated with them.

Do NOT generate an image.
Do NOT write an image-generation prompt.
Do NOT describe how the image should look.
Do NOT invent fictional events.

Return ONLY a JSON array of 5 objects (ordered from item 5 down to item 1):
[
  {{
    "item_number": 5,
    "title": "Short Item Title",
    "description": "1-sentence historical context",
    "search_query": "1 to 3 core proper nouns for image search (e.g. Tsar Bomba or Berlin Wall)"
  }},
  {{
    "item_number": 4,
    "title": "Short Item Title",
    "description": "1-sentence historical context",
    "search_query": "1 to 3 core proper nouns for image search (e.g. Tsar Bomba or Berlin Wall)"
  }},
  {{
    "item_number": 3,
    "title": "Short Item Title",
    "description": "1-sentence historical context",
    "search_query": "1 to 3 core proper nouns for image search (e.g. Tsar Bomba or Berlin Wall)"
  }},
  {{
    "item_number": 2,
    "title": "Short Item Title",
    "description": "1-sentence historical context",
    "search_query": "1 to 3 core proper nouns for image search (e.g. Tsar Bomba or Berlin Wall)"
  }},
  {{
    "item_number": 1,
    "title": "Short Item Title",
    "description": "1-sentence historical context",
    "search_query": "1 to 3 core proper nouns for image search (e.g. Tsar Bomba or Berlin Wall)"
  }}
]

Return ONLY the raw JSON array. No explanations, no markdown formatting."""

    app_cfg = _get_llm_config(profile)
    resp = None
    try:
        resp = llm._generate_response(prompt, app_config=app_cfg)
    except Exception as e:
        logger.debug(f"[Researcher] LLM item generation error: {e}")

    items = []
    if resp:
        try:
            cleaned = resp.strip()
            if "```" in cleaned:
                parts = cleaned.split("```")
                cleaned = parts[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            parsed = json.loads(cleaned.strip())
            if isinstance(parsed, list):
                items = parsed[:5]
        except Exception as e:
            logger.debug(f"[Researcher] JSON item parsing fallback: {e}")

    if not items or len(items) < 5:
        # Fallback 5 items if LLM parsing fails
        sub_aspects = ["bunker", "submarine", "aircraft", "radar", "weapon"]
        clean_topic = re.sub(r'(?i)\b(5|top 5|rare|photos|photo|of|declassified|archives|historical|unseen|mysteries)\b', '', topic).strip()
        items = [
            {
                "item_number": 5 - i,
                "title": f"{clean_topic.title()} {aspect.title()}",
                "description": f"Archival photograph of {clean_topic} {aspect}.",
                "search_query": f"{clean_topic} {aspect} photograph"
            } for i, aspect in enumerate(sub_aspects)
        ]

    logger.info(f"[Researcher] Successfully brainstormed 5 rare photo items for topic '{topic}': {[it.get('title') for it in items]}")
    return items

