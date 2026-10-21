from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .textutil import has_phrase, norm

# "0-1 years", "1 to 3 yrs", "5+ years"
_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?)\b", re.I)
_FRESHER = re.compile(
    r"\b(freshers?|entry[- ]level|new grads?|recent graduates?|no (?:prior )?experience (?:is )?(?:required|needed))\b", re.I)
_PAY = re.compile(
    r"(?:₹|rs\.?|inr|usd|\$)\s?[\d][\d,.]*(?:\s?(?:k|lpa|lakhs?|per (?:month|year|annum)|/month|/year|pa))?"
    r"(?:\s?(?:-|to|–)\s?(?:₹|rs\.?|inr|usd|\$)?\s?[\d][\d,.]*(?:\s?(?:k|lpa|lakhs?))?)?"
    r"|\b\d+(?:\.\d+)?\s?(?:-|to)\s?\d+(?:\.\d+)?\s?lpa\b"
    r"|\bstipend\b[^.]{0,60}\d",
    re.I)
_SHIFT = re.compile(r"\b10(?::00)?\s?(?:am|a\.m\.)?\s?(?:-|–|to)\s?6(?::00)?\s?(?:pm|p\.m\.)?", re.I)

WITHIN, TOO_SENIOR, NOT_STATED = "within", "too_senior", "not_stated"


def experience_requirement(text: str) -> Tuple[Optional[int], str]:
    """Return (minimum years the job asks for, the matching text). (None, "") if not stated.

    Takes the strictest minimum found. A number above 1 only counts when the word
    'experience' is nearby, so lines like 'founded 10 years ago' are ignored.
    """
    best: Optional[int] = None
    snippet = ""
    for match in _YEARS.finditer(text or ""):
        low = int(match.group(1))
        context = text[max(0, match.start() - 60): match.end() + 60].lower()
        if low > 1 and "experience" not in context and " exp" not in context:
            continue
        if best is None or low > best:
            best, snippet = low, match.group(0).strip()
    if best is not None:
        return best, snippet
    if _FRESHER.search(text or ""):
        return 0, "fresher / entry level"
    return None, ""


def experience_bucket(text: str, max_years: int) -> Tuple[str, str]:
    low, snippet = experience_requirement(text)
    if low is None:
        return NOT_STATED, ""
    return (WITHIN if low <= max_years else TOO_SENIOR), snippet


def title_matches(job_title: str, phrases: List[str]) -> bool:
    title_norm = norm(job_title)
    return any(has_phrase(title_norm, phrase) for phrase in phrases)


def has_excluded_word(job_title: str, words: List[str]) -> bool:
    title_norm = norm(job_title)
    return any(has_phrase(title_norm, norm(word)) for word in words)


def location_matches(location: str, wanted: List[str]) -> bool:
    location_norm = norm(location)
    return any(norm(place) and norm(place) in location_norm for place in wanted)


def shift_mentioned(text: str) -> bool:
    return bool(_SHIFT.search(text or ""))


_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|l|lpa|lakhs?|lacs?|cr|crores?|m)?(?![a-z0-9])", re.I)
_MULTIPLIER = {"k": 1e3, "l": 1e5, "lpa": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
               "cr": 1e7, "crore": 1e7, "crores": 1e7, "m": 1e6}


def parse_annual_pay(text: str, currency: str) -> Optional[Tuple[float, float]]:
    """Read a pay snippet as (low, high) per YEAR in `currency`, or None when it cannot be done safely.

    Only INR and USD are recognised, and no currency is ever converted: a snippet in a different
    currency than the one asked for returns None. Hourly or weekly pay, and bare numbers with no
    sign of period, also return None. Monthly pay is multiplied by 12.
    """
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    has_inr = bool(re.search(r"₹|\brs\b|\binr\b|\blpa\b|lakh|\blacs?\b", lowered))
    has_usd = bool(re.search(r"\$|\busd\b", lowered))
    detected = "INR" if has_inr else ("USD" if has_usd else None)
    if detected is None or detected != (currency or "").upper():
        return None
    if re.search(r"hour|/hr|\bweek", lowered):
        return None
    monthly = bool(re.search(r"month|/mo\b|per-month", lowered))
    yearly = bool(re.search(r"year|annum|annual|\bpa\b|lpa|per-year", lowered))
    bulk_unit = bool(re.search(r"\blpa\b|lakh|\blacs?\b", lowered))

    values = []
    for match in _NUMBER.finditer(lowered):
        number = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        if suffix:
            number *= _MULTIPLIER[suffix]
        elif bulk_unit and number < 1000:
            number *= 1e5
        values.append(number)
        if len(values) == 2:
            break
    if not values:
        return None
    if monthly and not yearly:
        values = [v * 12 for v in values]
    elif not monthly and not yearly and not any(match.group(2) for match in _NUMBER.finditer(lowered)):
        if min(values) < (100000 if detected == "INR" else 10000):
            return None  # a bare number with no period: could be monthly, so do not guess
    return (min(values), max(values))


_MIN_WORDS = re.compile(r"more than|greater than|above|over|at least|minimum|\bmin\b|upwards|or more|and above|>|\+")
_MAX_WORDS = re.compile(r"less than|below|under|up to|upto|at most|maximum|\bmax\b|or less|<")


def parse_pay_preference(text: str, default_currency: str = "INR") -> Tuple[str, Optional[float], Optional[float]]:
    """Read pay typed the way people say it. Returns (currency, minimum, maximum) per year.

    Examples: '8 LPA', 'more than 8 lakhs per annum', '>8 lakhs', '6-12 LPA', 'up to 10 LPA',
    '$80k', '50k per month'. Raises ValueError with a helpful message when it cannot be read.
    """
    lowered = (text or "").lower().replace(",", "")
    has_inr = bool(re.search(r"\u20b9|\brs\b|\binr\b|\blpa\b|lakh|\blacs?\b|rupee", lowered))
    has_usd = bool(re.search(r"\$|\busd\b|dollar", lowered))
    currency = "INR" if has_inr else ("USD" if has_usd else default_currency.upper())
    monthly = bool(re.search(r"month|/mo\b", lowered))
    numbers = []
    for match in _NUMBER.finditer(lowered):
        number = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        if suffix:
            number *= _MULTIPLIER[suffix]
        elif currency == "INR" and number < 1000:
            number *= 1e5          # a bare small number is read as lakhs per year
        elif currency == "USD" and number < 1000:
            raise ValueError("For dollars please add a k, for example $80k")
        numbers.append(number)
    if not numbers:
        raise ValueError("I could not find a number")
    numbers = [n * 12 for n in numbers] if monthly else numbers
    has_direction = bool(_MIN_WORDS.search(lowered) or _MAX_WORDS.search(lowered))
    if len(numbers) >= 2 and not has_direction:
        return currency, min(numbers[:2]), max(numbers[:2])
    if _MAX_WORDS.search(lowered) and not _MIN_WORDS.search(lowered):
        return currency, None, numbers[0]
    return currency, numbers[0], None


def pay_from_text(text: str) -> str:
    match = _PAY.search(text or "")
    return match.group(0).strip() if match else ""
