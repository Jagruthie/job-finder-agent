from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .textutil import has_phrase, norm

# "0-1 years", "1 to 3 yrs", "5+ years", "3-4 or more years", "four years"
_NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}
_NUM = r"(?:\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_YEARS = re.compile(
    rf"(?<![\w.])({_NUM})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*({_NUM})\s*\+?)?\s*(?:or (?:more|above))?\s*"
    r"(?:years?|yrs?)\b", re.I)
# "over multiple years": at least two, but only when 'experience' is nearby.
_VAGUE_YEARS = re.compile(r"\b(?:multiple|several)\s+years\b", re.I)
# Words right after the years that say what the years were spent on: "3-5 years in support", "2 to 4 years designing".
_YEARS_OF_WORK = re.compile(
    r"\s*(?:\(|of\b|in\b|as\b|at\b|with\b|on\b|working|designing|building|managing|leading|selling|supporting|"
    r"running|owning|doing|hands|post|relevant|professional|industry|prior|proven|full|track|direct|corporate|"
    r"previous|practical)", re.I)
# ...unless they are the company's own story: "11 years old", "over 50 years of innovation".
_COMPANY_YEARS = re.compile(r"\s*(?:old\b|ago\b|of (?:history|innovation|heritage|excellence|trust|operations?\b"
                            r"|service|serving|success|growth|business\b))", re.I)

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
_CLAUSE_END = re.compile(r"[.;•\n]\s")


def _years_value(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBER_WORDS[word.lower()]


def _clause(text: str, start: int, end: int) -> str:
    """Up to 60 characters around a match, cut at the sentence or bullet it sits in."""
    before = _CLAUSE_END.split(text[max(0, start - 60): start])[-1]
    after = _CLAUSE_END.split(text[end: end + 60])[0]
    return f"{before}{text[start:end]}{after}".lower()


def experience_requirement(text: str) -> Tuple[Optional[int], str]:
    """Return (minimum years the job asks for, the matching text). (None, "") if not stated.

    Takes the strictest minimum found. A number above 1 counts when the word 'experience' is nearby,
    or when the words after it say what the years were spent on ('6-8 years in ads strategy'), so
    lines like 'founded 10 years ago' or '11 years old' are ignored.
    """
    text = text or ""
    best: Optional[int] = None
    snippet = ""
    for match in _YEARS.finditer(text):
        low = _years_value(match.group(1))
        after = text[match.end(): match.end() + 40]
        context = _clause(text, match.start(), match.end())
        if _COMPANY_YEARS.match(after):
            continue
        if low > 1 and "experience" not in context and " exp" not in context and not _YEARS_OF_WORK.match(after):
            continue
        if best is None or low > best:
            best, snippet = low, match.group(0).strip()
    for match in _VAGUE_YEARS.finditer(text):
        context = _clause(text, match.start(), match.end())
        if "experience" in context and (best is None or best < 2):
            best, snippet = 2, match.group(0).strip()
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


# Words in a title that say "this is a beginner job": Associate, Junior, Trainee, Graduate, "Product Manager I".
_ENTRY_WORDS = {"associate", "junior", "jr", "trainee", "graduate", "grad", "fresher", "freshers", "entry",
                "apprentice", "intern"}
# "Product Manager I" or "Product Manager 1, Growth", but not "IBM i" or "L1/L2 support".
_ENTRY_LEVEL_MARK = re.compile(r"(?<![\w/])(?:I|1|L1)(?=\s*(?:$|[,(|–-]))")
# Level marks for jobs above entry level, such as "Product Manager II", and the years each one usually asks for.
_HIGHER_LEVELS = {"ii": 2, "l2": 2, "iii": 5, "l3": 5, "iv": 8, "l4": 8}


def entry_level_title(job_title: str) -> bool:
    return bool(set(norm(job_title).split()) & _ENTRY_WORDS or _ENTRY_LEVEL_MARK.search(job_title or ""))


def higher_level_title(job_title: str, max_years: int = 1) -> bool:
    """True if the title has a level mark above your experience: II or L2 is fine from 2 years, III from 5."""
    return any(_HIGHER_LEVELS[mark] > max_years for mark in set(norm(job_title).split()) & _HIGHER_LEVELS.keys())


def has_excluded_word(job_title: str, words: List[str], role_phrases: List[str] = ()) -> bool:
    """True if the title has a word such as 'senior' or 'staff'. Words inside the role you asked for
    do not count, so 'Chief of Staff' is kept while 'Staff Engineer' and 'Senior Chief of Staff' are not."""
    title_norm = f" {norm(job_title)} "
    for phrase in sorted(role_phrases, key=len, reverse=True):
        title_norm = title_norm.replace(f" {phrase} ", " | ")
    return any(has_phrase(title_norm.strip(), norm(word)) for word in words)


# Titles that are senior by what the job is, even with no 'Senior' in them and no years stated: people managers
# and quota-carrying sales. Used below 3 years of experience, like 'senior'; a role you asked for still counts.
SENIOR_ROLE_PHRASES = ["engineering manager", "team manager", "people manager", "general manager", "country manager",
                       "regional manager", "account executive", "strategic account", "enterprise account",
                       "account director"]


INDIA_PLACES = ["india", "bengaluru", "bangalore", "hyderabad", "mumbai", "delhi", "gurgaon", "gurugram", "noida",
                "pune", "chennai", "kolkata", "ahmedabad", "telangana", "karnataka", "maharashtra",
                "mohali", "chandigarh", "jaipur", "kochi", "coimbatore", "thiruvananthapuram", "trivandrum", "indore",
                "bhubaneswar", "visakhapatnam", "vadodara", "nagpur", "lucknow", "mysuru", "mysore", "mangaluru",
                "tamil nadu", "kerala", "gujarat", "haryana", "west bengal", "uttar pradesh", "ind"]
_ANYWHERE = {"remote", "work", "from", "home", "anywhere", "worldwide", "global", "fully", "first"}


def remote_in(location_norm: str, country: str) -> bool:
    """A remote job counts only if it is remote in your country: it names the country or one of its
    cities, or it just says 'Remote' or 'Anywhere'. 'Remote, United States' does not count for India."""
    if not country:
        return True
    places = INDIA_PLACES if norm(country) == "india" else [norm(country)]
    if any(has_phrase(location_norm, place) for place in places):
        return True
    return set(location_norm.split()) <= _ANYWHERE


# A job that only says 'Remote' often limits who can apply in its text or title instead.
_ELSEWHERE = (r"(?:US|U\.S\.|USA|United States|North America|(?:the )?Americas|Europe|EU|EMEA|UK|United Kingdom|"
              r"Canada|LATAM|Latin America|Australia|Brazil|Mexico)")
_RESTRICTED = [re.compile(pattern, re.I if case else 0) for pattern, case in [
    (rf"\b{_ELSEWHERE}\b[^.;\n]{{0,30}}\btime ?zones?\b", False),
    (r"\b(?:EST|PST|CST|MST|CET|EET)\b[^.;\n]{0,20}\b(?:hours|time ?zones?|working)\b", False),
    (r"\b(?:US |U\.S\. )?(?:Eastern|Pacific|Central|Mountain) (?:Standard )?(?:time ?zones?|time\b)", True),
    (rf"\b(?:based|located|reside|residing|live|living)\b[^.;\n]{{0,25}}\b(?:in|within|on)\b[^.;\n]{{0,25}}"
     rf"\b{_ELSEWHERE}\b", False),
    (rf"\banywhere in (?:the )?{_ELSEWHERE}\b", False),
    (rf"\b(?:authori[sz]ed|eligible|right) to work in (?:the )?{_ELSEWHERE}\b", False),
]]
_OPEN_HERE = {"india": re.compile(r"\b(?:India|IST|APAC|Asia|anywhere in the world|work from anywhere)\b", re.I)}
_COUNTRIES = ["united states", "usa", "canada", "mexico", "brazil", "argentina", "united kingdom", "uk", "ireland",
              "france", "germany", "spain", "italy", "netherlands", "poland", "portugal", "sweden", "denmark",
              "norway", "finland", "switzerland", "austria", "belgium", "czech republic", "romania", "israel",
              "australia", "new zealand", "japan", "singapore", "philippines", "indonesia", "vietnam", "korea",
              "uae", "saudi arabia", "south africa", "nigeria", "kenya", "egypt", "emea", "latam", "europe",
              "north america", "americas", "dach", "nordics", "benelux"]


def remote_elsewhere(title: str, description: str, country: str = "India") -> str:
    """For a job whose location is only 'Remote': the line that limits it to somewhere else, such as
    'US Eastern timezones is required' or a title ending in ', Poland'. Empty if nothing says so, or if
    your country (or IST, APAC, Asia) is named too."""
    if not country:
        return ""
    title_norm = norm(title)
    own = INDIA_PLACES if norm(country) == "india" else [norm(country)]
    if not any(has_phrase(title_norm, place) for place in own):
        for place in _COUNTRIES:
            if has_phrase(title_norm, place):
                return f"title names {place.title() if len(place) > 4 else place.upper()}"
    open_here = _OPEN_HERE.get(norm(country), re.compile(rf"\b{re.escape(country)}\b", re.I))
    if open_here.search(description or ""):
        return ""
    for pattern in _RESTRICTED:
        found = pattern.search(description or "")
        if found:
            return found.group(0).strip()
    return ""


def in_country(location: str, country: str = "India") -> bool:
    """True if a job is in your country (any city of it) or remote and open to it. A job with no location
    given is kept, since nothing says it is elsewhere."""
    location_norm = norm(location)
    if not country or not location_norm:
        return True
    places = INDIA_PLACES if norm(country) == "india" else [norm(country)]
    return (any(has_phrase(location_norm, place) for place in places)
            or (has_phrase(location_norm, "remote") and remote_in(location_norm, country)))


# Cities with two common spellings: a job in "Bangalore" is in your "Bengaluru".
_SAME_CITY = [{"bengaluru", "bangalore"}, {"gurugram", "gurgaon"}, {"mumbai", "bombay"}, {"chennai", "madras"},
              {"kolkata", "calcutta"}, {"delhi", "new delhi", "ncr", "delhi ncr"}, {"mysuru", "mysore"},
              {"thiruvananthapuram", "trivandrum"}, {"kochi", "cochin"}]


def location_matches(location: str, wanted: List[str], remote_country: str = "India") -> bool:
    location_norm = norm(location)
    for place in wanted:
        place_norm = norm(place)
        if place_norm == "remote":
            if has_phrase(location_norm, "remote") and remote_in(location_norm, remote_country):
                return True
            continue
        spellings = next((group for group in _SAME_CITY if place_norm in group), {place_norm})
        if any(spelling and spelling in location_norm for spelling in spellings):
            return True
    return False


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
