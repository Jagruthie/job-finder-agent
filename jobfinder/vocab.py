from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .filters import INDIA_PLACES
from .textutil import has_phrase, norm

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "skills.json"
_CONTACT = re.compile(r"@|https?://|www\.|\+?\d[\d\s-]{8,}\d")
_LABEL = re.compile(r"^\s*([^:,;|•·]{1,40}?)\s*(?::|\s[-–]\s)\s*(.*)$")
_NOT_SKILL_LABELS = {"education", "contact", "address", "email", "phone", "location", "name", "languages",
                     "certifications", "awards", "hobbies", "interests", "references"}
_STOP_WORDS = {"and", "or", "the", "a", "an", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as",
               "is", "are", "was", "were", "my", "our", "i", "we", "etc"}


class Vocab:
    """Skills and role titles the agent knows about. Plain data, easy to extend."""

    def __init__(self, data: dict):
        self.skills: Dict[str, List[str]] = data.get("skills", {})
        self.titles: Dict[str, dict] = data.get("titles", {})
        self.generic: set = set(data.get("generic_skills", []))
        self.exact_case: set = set(data.get("exact_case", []))
        self.deal_breakers: Dict[str, dict] = data.get("deal_breakers", {})
        # An exact-case skill's name is matched separately, in capitals only ('GEO' in a post, not 'geo').
        self._skill_phrases = {
            name: sorted(({norm(name)} - ({norm(name)} if name in self.exact_case else set()))
                         | {norm(a) for a in aliases} - {""})
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
        for name in self.exact_case:
            found = len(re.findall(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text or ""))
            if found:
                counts[name] = found
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

    def resume_phrases(self, text: str, limit: int = 20) -> List[str]:
        """Skills listed in a resume that are not in the built-in list, such as 'Power BI' or 'Kubernetes'.
        Only items from list-like lines ('Tools: Jira, Figma, Power BI') are taken, so sentences,
        company names and cities are left out. Very short ones ('Go', 'R', 'C++') are skipped, because
        their letters alone would match ordinary words in job posts."""
        known = {phrase for phrases in self._skill_phrases.values() for phrase in phrases}
        found: List[str] = []
        for line in (text or "").splitlines():
            if _CONTACT.search(line):
                continue                      # a name / email / phone line: never treat it as skills
            labelled = _LABEL.match(line)          # "Skills: a, b, c" or "Core skills - a | b | c"
            if labelled:
                if norm(labelled.group(1)) in _NOT_SKILL_LABELS:
                    continue
                line = labelled.group(2)
            items = [item.strip(" .-–•·*\t") for item in re.split(r"[,;|•·]", line)]
            items = [item for item in items if item]
            short = [item for item in items if len(item.split()) <= 3 and len(item) <= 30]
            if len(items) < 3 or len(short) < 0.8 * len(items):     # a list, not a sentence with commas
                continue
            for item in short:
                key = norm(item)
                if (len(key) < 3 or not re.search(r"[a-z]", key) or key in known
                        or any(has_phrase(key, place) for place in INDIA_PLACES)
                        or key.split()[0] in _STOP_WORDS
                        or key in {norm(f) for f in found}):
                    continue
                found.append(item)
        return found[:limit]

    def deal_breaker_terms(self, group: str, text: str) -> List[str]:
        """Distinct terms of a deal-breaker group (such as Coding) found in a job post."""
        info = self.deal_breakers.get(group, {})
        text_norm = norm(text)
        found = {phrase for phrase in info.get("phrases", []) if has_phrase(text_norm, norm(phrase))}
        found |= {term for term in info.get("exact", [])
                  if re.search(rf"(?<![\w.]){re.escape(term)}(?![\w#+])", text or "")}
        return sorted(found)

    def deal_breaker_title(self, group: str, title: str) -> bool:
        """True if the title itself names the work, such as 'Developer' for Coding."""
        title_norm = norm(title)
        return any(has_phrase(title_norm, norm(word)) for word in self.deal_breakers.get(group, {}).get("title_words", []))

    def skills_in_name(self, name: str) -> set:
        """Skills whose words sit inside a company name: Git/GitHub for 'gitlab', Notion for 'Notion'."""
        squashed = norm(name).replace(" ", "")
        return {skill for skill, phrases in self._skill_phrases.items()
                if any(len(p) >= 3 and p.replace(" ", "") in squashed for p in phrases)}

    def category(self, title: str) -> str:
        """The group a role belongs to, such as 'Product & Strategy' or 'SEO & Content'."""
        return self.titles.get(title, {}).get("category", "Other")

    def role_groups(self, skills: List[str]) -> List[Tuple[str, List[Tuple[str, List[str], List[str]]]]]:
        """Every role that needs 2 or more of these skills, grouped by category.
        The group holding the best-matching role comes first."""
        groups: Dict[str, list] = {}
        for match in self.role_matches(skills, limit=None):
            groups.setdefault(self.category(match[0]), []).append(match)
        return list(groups.items())

    def role_matches(self, skills: List[str], limit: Optional[int] = 6) -> List[Tuple[str, List[str], List[str]]]:
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
