from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .filters import INDIA_PLACES
from .textutil import has_phrase, norm

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "skills.json"
_CONTACT = re.compile(r"@|https?://|www\.|\+?\d[\d\s-]{8,}\d")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_LABEL = re.compile(r"^\s*([^:,;|•·]{1,40}?)\s*(?::|\s[-–]\s)\s*(.*)$")
_NOT_SKILL_LABELS = {"education", "contact", "address", "email", "phone", "location", "name", "languages",
                     "certifications", "awards", "hobbies", "interests", "references"}
# A line that starts with a role title: up to 4 capitalized words ending in a role word.
_TITLE_LINE = re.compile(r"^\s*((?:[A-Z][\w/&+.-]*\s+){1,4}(?:Engineer|Analyst|Developer|Scientist|Consultant|Specialist|"
                         r"Architect|Designer|Manager|Associate|Executive|Strategist|Writer|Administrator|Recruiter|"
                         r"Generalist|Coordinator|Officer|Partner))\b")
_SENIORITY = re.compile(r"^(?:(?:Senior|Sr\.?|Junior|Jr\.?|Trainee)\s+)+")
# Job history lines: a date range, and the role before the first separator ('|', '–', ' - ', ',', '@', ' at ').
_DATE_RANGE = re.compile(r"\b(?:19|20)\d{2}\s*(?:-|–|—|to)\s*(?:[A-Za-z]{3,9}\.?\s+)?(?:(?:19|20)\d{2}|present|current|"
                         r"now|till date|date)\b", re.I)
_SEPARATOR = re.compile(r"\s*(?:\||–|—|\s-\s|,|@|\bat\b)\s*")
# Degrees and schools are dated too, but they are not roles.
_EDUCATION = re.compile(r"^(?:b|m)\.?\s?(?:tech|e|sc|com|a|s|ed|pharm|arch|des)\b|^(?:mba|bba|bca|mca|ph\.?d|diploma|"
                        r"bachelor|master|class|ssc|hsc|cbse|icse|intermediate|10th|12th|high school|secondary)\b|"
                        r"universit|college|institute|school|academy|certifi", re.I)
_STOP_WORDS = {"and", "or", "the", "a", "an", "of", "to", "in", "on", "for", "with", "at", "by", "from", "as",
               "is", "are", "was", "were", "my", "our", "i", "we", "etc"}


class Vocab:
    """Skills and role titles the agent knows about. Plain data, easy to extend."""

    def __init__(self, data: dict):
        self.skills: Dict[str, List[str]] = data.get("skills", {})
        self.titles: Dict[str, dict] = data.get("titles", {})
        self.exact_case: set = set(data.get("exact_case", []))
        self.deal_breakers: Dict[str, dict] = data.get("deal_breakers", {})
        # An exact-case skill's name is matched separately, in capitals only ('GEO' in a post, not 'geo').
        self._skill_phrases = {
            name: sorted(({norm(name)} - ({norm(name)} if name in self.exact_case else set()))
                         | {norm(a) for a in aliases} - {""})
            for name, aliases in self.skills.items()
        }
        # Compiled once: there are more phrases than Python keeps compiled, and every job post is searched for all.
        self._by_length = [(phrase, re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"), name)
                           for phrase, name in sorted(((phrase, name) for name, phrases in self._skill_phrases.items()
                                                       for phrase in phrases), key=lambda pair: (-len(pair[0]), pair[0]))]
        self._exact = [(name, re.compile(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])")) for name in self.exact_case]

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
        for name, pattern in self._exact:
            found = len(pattern.findall(text or ""))
            if found:
                counts[name] = found
        for phrase, pattern, name in self._by_length:
            if phrase not in text_norm:
                continue
            text_norm, found = pattern.subn(lambda match: " " * len(match.group(0)), text_norm)
            if found:
                counts[name] = counts.get(name, 0) + found
        return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def resume_phrases(self, text: str, limit: int = 40) -> List[str]:
        """Skills listed in a resume that are not in the built-in list, such as 'Spring Boot' or 'HRMS'.
        Only items from list-like lines ('Tools: Jira, Figma, Spring Boot') are taken, so sentences,
        company names and cities are left out. Brackets count as commas: 'AWS (S3, EC2)' gives AWS, S3, EC2.
        Very short ones ('Go', 'R', 'C++') are skipped, because their letters alone would match ordinary
        words in job posts, and so are longer phrases around a known skill ('building ETL pipelines'),
        since the known skill inside is already found."""
        known = {phrase for phrases in self._skill_phrases.values() for phrase in phrases}
        found: List[str] = []
        for line in (text or "").splitlines():
            if _CONTACT.search(line):
                continue                      # a name / email / phone line: never treat it as skills
            if _YEAR.search(line):
                continue                      # "Data Engineer | Acme | 2022 - Present": a job or a degree, not skills
            labelled = _LABEL.match(line)          # "Skills: a, b, c" or "Core skills - a | b | c"
            if labelled:
                if norm(labelled.group(1)) in _NOT_SKILL_LABELS:
                    continue
                line = labelled.group(2)
            items = [item.strip(" .-–•·*\t") for item in re.split(r"[,;|•·()\[\]]", line)]
            items = [item for item in items if item]
            short = [item for item in items if len(item.split()) <= 3 and len(item) <= 30]
            if len(items) < 3 or len(short) < 0.8 * len(items):     # a list, not a sentence with commas
                continue
            for item in short:
                key = norm(item)
                if (len(key) < 3 or not re.search(r"[a-z]", key) or key in known
                        or any(has_phrase(key, place) for place in INDIA_PLACES)
                        or key.split()[0] in _STOP_WORDS
                        or key in {norm(f) for f in found}
                        or (len(key.split()) >= 3 and self.extract_skills(item))):
                    continue
                found.append(item)
        return found[:limit]

    def resume_titles(self, text: str, limit: int = 6) -> List[str]:
        """Role titles named in a resume. First the roles in its job history, whatever the field: in a line such
        as 'Staff Nurse | Apollo Hospitals | 2021 - Present', the part before the first separator. Then built-in
        titles named anywhere (by their name or a longer alias, never a one-word alias such as 'devops', which is
        often a skill), then other title-like lines. Senior, Junior and the like are left off."""
        found: List[str] = []
        covered = set()                       # every spelling of the roles found so far

        def add(role: str) -> None:
            spellings = set(self.synonyms(role))
            if not spellings & covered:
                found.append(role)
                covered.update(spellings)

        for line in (text or "").splitlines():
            if not _DATE_RANGE.search(line):
                continue                      # a job history line has dates: '2021 - Present', 'Jan 2020 - 2023'
            role = _SENIORITY.sub("", _SEPARATOR.split(line.strip(), maxsplit=1)[0]).strip(" .:-")
            if (1 <= len(role.split()) <= 5 and len(role) <= 45 and re.search(r"[A-Za-z]", role)
                    and not re.search(r"\d", role) and not _EDUCATION.search(role)):
                add(role)
        text_norm = norm(text)
        for title in self.titles:
            if any(has_phrase(text_norm, phrase) for phrase in
                   [norm(title)] + [norm(a) for a in self.titles[title].get("aliases", []) if len(a.split()) >= 2]):
                add(title)
        for line in (text or "").splitlines():
            match = _TITLE_LINE.match(line)
            if match and not _EDUCATION.search(line):
                title = _SENIORITY.sub("", match.group(1)).strip()
                if len(title.split()) >= 2 and not self.closest_title(title):
                    add(title)
        return found[:limit]

    def closest_title(self, typed: str) -> Optional[str]:
        """The built-in role a typed role was probably meant to be ('Data Enginner' -> 'Data Engineer').
        None if it is already a built-in role or one of its other spellings, or if nothing is close."""
        spellings = {norm(title): title for title in self.titles}
        spellings.update({norm(alias): title for title, info in self.titles.items() for alias in info.get("aliases", [])})
        key = norm(typed)
        if not key or key in spellings:
            return None
        close = difflib.get_close_matches(key, list(spellings), n=1, cutoff=0.88)
        return spellings[close[0]] if close else None

    def synonyms(self, item: str) -> List[str]:
        """Other spellings of a skill or role you saved, so a job saying 'PySpark' counts for 'Spark' and
        'HRBP' for 'HR Business Partner'. Anything the built-in list does not know is used as you wrote it."""
        key = norm(item)
        found = {key} - {""}
        for name, phrases in self._skill_phrases.items():
            if key == norm(name) or key in phrases:
                found.update(phrases)
        for title, info in self.titles.items():
            if key == norm(title) or key in {norm(a) for a in info.get("aliases", [])}:
                found.update(self.title_phrases(title))
        return sorted(found)

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

    def title_phrases(self, title: str) -> List[str]:
        """Normalized spellings that count as this title (the title itself plus known aliases)."""
        phrases = {norm(title)}
        info = self.titles.get(title)
        if info:
            phrases.update(norm(alias) for alias in info.get("aliases", []))
        return sorted(p for p in phrases if p)
