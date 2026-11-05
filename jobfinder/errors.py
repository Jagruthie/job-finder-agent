"""Problems the core reports by raising. The message is always readable as it is, so any screen can show it."""
from __future__ import annotations


class FreshlistError(Exception):
    """Something the person can fix. str(error) says what and how."""


class ValidationError(FreshlistError):
    """A value or a rule was not met, such as marking an entry applied without an explanation."""


class NotFoundError(FreshlistError):
    """No company or tracker entry matches."""


class ConfigError(FreshlistError):
    """settings.json cannot be used."""


class StorageError(FreshlistError):
    """A data file cannot be read as it is."""


class ProfileError(FreshlistError):
    """A profile cannot be used as it is."""


class ResumeError(FreshlistError):
    """A resume cannot be read."""
