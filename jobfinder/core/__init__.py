"""The core library: all of Freshlist's logic, with no printing, no questions and no exiting.

Functions take plain arguments and return plain data, so a terminal or a web page can show the same results.
See ARCHITECTURE.md.
"""
from . import discovery, profiles, review, runner, today, tracker, watchlist  # noqa: F401
