"""Profiles: one file per search track, such as pm or seo."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .. import storage
from ..config import file_path
from ..pipeline import is_backup, profile_files
from ..resume import read_resume_text
from ..vocab import Vocab


def find_paths(cfg: dict) -> List[str]:
    """Every profile in the profiles folder (example.json and backup copies are left out)."""
    return [str(path) for path in profile_files(str(file_path(cfg, "profiles_dir")))]


def load(paths: List[str]) -> Tuple[List[dict], List[str]]:
    """(the profiles, notes about files left out)."""
    profiles, notes = [], []
    for path in paths:
        if is_backup(path):
            notes.append(f"skipping {path}: it is a backup copy, not a profile")
            continue
        profile = storage.read_json(path, {})
        profile.setdefault("name", Path(path).stem)
        profiles.append(profile)
    return profiles, notes


def from_resume(resume: str, name: str, out_dir: str, vocab: Optional[Vocab] = None) -> dict:
    """Write a profile of the skills and roles found in a resume, read on this computer. Returns the profile."""
    vocab = vocab or Vocab.load()
    text = read_resume_text(resume)
    profile = {
        "_note": "From your resume. Delete skills that are wrong, add ones the agent missed in your own words, and fix the titles.",
        "name": name,
        "skills": vocab.extract_skills(text) + vocab.resume_phrases(text),
        "titles": vocab.resume_titles(text),
    }
    storage.write_json(Path(out_dir) / f"{name}.json", profile)
    return profile
