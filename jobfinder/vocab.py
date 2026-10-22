from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .textutil import norm

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "skills.json"


class Vocab:
    """Skills and role titles the agent knows about. Plain data, easy to extend."""

    def __init__(self, data: dict):
        self.skills: Dict[str, List[str]] = data.get("skills", {})
        self.titles: Dict[str, dict] = data.get("titles", {})
        self._skill_phrases = {
            name: sorted({norm(name), *[norm(a) for a in aliases]} - {""})
            for name, aliases in self.skills.items()
        }

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Vocab":
        with open(path or DEFAULT_PATH, encoding="utf-8") as handle:
            return cls(json.load(handle))

    def extract_skills(self, text: str) -> List[str]:
        """Skills mentioned in text, most-mentioned first.

        Longer phrases are matched first and their words are then used up, so 'keyword research'
        counts as Keyword Research only, not also as Research (same for 'content strategy' and Strategy).
        """
        text_norm = norm(text)
        counts: Dict[str, int] = {}
        by_length = sorted(((phrase, name) for name, phrases in self._skill_phrases.items() for phrase in phrases),
                           key=lambda pair: (-len(pair[0]), pair[0]))
        for phrase, name in by_length:
            pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
            text_norm, found = re.subn(pattern, lambda match: " " * len(match.group(0)), text_norm)
            if found:
                counts[name] = counts.get(name, 0) + found
        return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def suggest_titles(self, skills: List[str], limit: int = 6) -> List[str]:
        return [title for title, _, _ in self.role_matches(skills, limit)]

    def role_matches(self, skills: List[str], limit: int = 6) -> List[Tuple[str, List[str], List[str]]]:
        """Roles whose needed skills overlap yours (2 or more), best first, as
        (title, your skills it needs, all skills it needs). These are the reasons shown to the user."""
        have = set(skills)
        scored = []
        for title, info in self.titles.items():
            wanted = info.get("skills", [])
            matched = [s for s in wanted if s in have]
            if len(matched) >= 2:
                scored.append((len(matched), len(matched) / max(1, len(wanted)), title, matched, wanted))
        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        return [(title, matched, wanted) for _, _, title, matched, wanted in scored[:limit]]

    def title_phrases(self, title: str) -> List[str]:
        """Normalized spellings that count as this title (the title itself plus known aliases)."""
        phrases = {norm(title)}
        info = self.titles.get(title)
        if info:
            phrases.update(norm(alias) for alias in info.get("aliases", []))
        return sorted(p for p in phrases if p)
