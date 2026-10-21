from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .textutil import count_phrase, norm

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
        """Skills mentioned in text, most-mentioned first."""
        text_norm = norm(text)
        counts = {}
        for name, phrases in self._skill_phrases.items():
            total = sum(count_phrase(text_norm, phrase) for phrase in phrases)
            if total:
                counts[name] = total
        return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def suggest_titles(self, skills: List[str], limit: int = 6) -> List[str]:
        have = set(skills)
        scored = []
        for title, info in self.titles.items():
            wanted = info.get("skills", [])
            overlap = len(have & set(wanted))
            if overlap >= 2:
                scored.append((overlap, overlap / max(1, len(wanted)), title))
        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        return [title for _, _, title in scored[:limit]]

    def title_phrases(self, title: str) -> List[str]:
        """Normalized spellings that count as this title (the title itself plus known aliases)."""
        phrases = {norm(title)}
        info = self.titles.get(title)
        if info:
            phrases.update(norm(alias) for alias in info.get("aliases", []))
        return sorted(p for p in phrases if p)
