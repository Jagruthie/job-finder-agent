"""How well a job fits you: the skills and roles you saved from your resume, ranked against each day's jobs
the way a search engine ranks pages (BM25).

A word or phrase found in few of the day's jobs ('airflow', 'hrbp') counts for a lot; one found in most of
them ('communication', 'equal opportunity') counts for almost nothing. That is learned from the jobs
themselves on every run, so it works for any field without a list of skills to keep up to date.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Tuple

from .textutil import has_phrase, norm
from .vocab import Vocab

TITLE_WEIGHT = 2.0       # your roles say what the job should be
PRIORITY_WEIGHT = 1.5    # skills you said the job should mainly be about
SKILL_WEIGHT = 1.0
TITLE_REPEAT = 3         # a job's own title counts three times: what the job IS matters more than one line in it
K1, B = 1.2, 0.75        # the usual BM25 constants: how fast repeats stop adding, how much long posts are evened out
RARE_IDF = math.log(2)   # a term in fewer than about half of today's jobs; more common ones never count as reasons
RARE_MIN_JOBS = 20       # ...when there are enough jobs to tell

_STOP = set("a an the and or of to in on for with at by from as is are be will you your we our us this that it its "
            "who what work working team teams role about can have has into across more all their they them other "
            "within new using use etc".split())


def tokens(text: str) -> List[str]:
    """Words and pairs of neighbouring words ('talent acquisition'), without filler words and numbers."""
    words = [w for w in norm(text).split() if w not in _STOP and len(w) > 1 and not w.isdigit()]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


class JobTerms:
    """One job as search terms: its title alone, and its whole text (title counted three times)."""

    def __init__(self, title: str, description: str):
        self.title = Counter(tokens(title))
        self.text = Counter(tokens(" ".join([title] * TITLE_REPEAT + [description])))
        self.title_norm = norm(title)
        self.text_norm = norm(f"{title} {description}")


class Corpus:
    """The day's jobs: how rare each term is among them, and their average length (titles and whole texts apart)."""

    def __init__(self, jobs: List[JobTerms]):
        count = max(1, len(jobs))
        self.count = len(jobs)

        def stats(docs):
            frequency = Counter(term for doc in docs for term in doc)
            idf = {term: math.log(1 + (count - n + 0.5) / (n + 0.5)) for term, n in frequency.items()}
            return idf, (sum(sum(doc.values()) for doc in docs) / count if docs else 1.0)
        self.title_idf, self.title_average = stats([job.title for job in jobs])
        self.text_idf, self.text_average = stats([job.text for job in jobs])


class Query:
    """What one profile searches for: its roles (compared with job titles only, since a word such as
    'recruiter' in a job's text is often just 'our recruiter will call you') and its skills (compared with
    the whole job). Each saved item brings its other spellings from the built-in list."""

    def __init__(self, titles: List[str], skills: List[str], priority: List[str], vocab: Vocab):
        self.spellings: Dict[str, List[str]] = {}
        self.sources: Dict[str, List[str]] = {}
        self.titles = self._terms([(t, TITLE_WEIGHT) for t in titles], vocab)
        self.skills = self._terms([(s, PRIORITY_WEIGHT if s in priority else SKILL_WEIGHT) for s in skills], vocab)
        self.title_items, self.skill_items = list(titles), list(skills)

    def _terms(self, items, vocab: Vocab) -> Dict[str, float]:
        weights: Dict[str, float] = {}
        for item, weight in items:
            self.spellings[item] = vocab.synonyms(item)
            for phrase in self.spellings[item]:
                for term in tokens(phrase):
                    weights[term] = max(weights.get(term, 0.0), weight)
                    if item not in self.sources.setdefault(term, []):
                        self.sources[term].append(item)
        return weights


def _bm25(weights: Dict[str, float], doc: Counter, idf: Dict[str, float], average: float) -> Dict[str, float]:
    length = sum(doc.values())
    parts = {}
    for term, weight in weights.items():
        tf = doc.get(term, 0)
        if tf:
            parts[term] = weight * idf.get(term, 0.0) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
    return parts


def score(query: Query, job: JobTerms, corpus: Corpus) -> Tuple[float, List[str], List[str]]:
    """(how well the job fits, your roles its title names, your skills its text names). The two lists are
    the reasons shown for the match, strongest first."""
    title_parts = _bm25(query.titles, job.title, corpus.title_idf, corpus.title_average)
    skill_parts = _bm25(query.skills, job.text, corpus.text_idf, corpus.text_average)

    def named(parts, items, text_norm, idf=None):
        strength: Dict[str, float] = {}
        rare: Dict[str, bool] = {}
        for term, part in parts.items():
            for item in query.sources.get(term, []):
                if item in items:
                    strength[item] = strength.get(item, 0.0) + part
                    rare[item] = (rare.get(item, False) or idf is None or corpus.count < RARE_MIN_JOBS
                                  or idf.get(term, 0.0) >= RARE_IDF)
        # A reason must be named in the job. A skill must also not be something most of today's jobs say
        # ('communication'); a role always counts, since it is what you asked for.
        found = [item for item in strength if rare[item] and any(has_phrase(text_norm, p) for p in query.spellings[item])]
        return sorted(found, key=lambda item: -strength[item])
    return (sum(title_parts.values()) + sum(skill_parts.values()),
            named(title_parts, query.title_items, job.title_norm),
            named(skill_parts, query.skill_items, job.text_norm, corpus.text_idf))
