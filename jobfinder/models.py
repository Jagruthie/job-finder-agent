from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Job:
    platform: str          # greenhouse | lever | ashby
    board: str             # the company's board token / site name
    company: str
    job_id: str
    title: str
    location: str
    url: str               # the exact page where the person applies
    description: str = ""
    posted_at: Optional[datetime] = None   # None when the platform gives no date
    pay_text: str = ""

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.board}:{self.job_id}"
