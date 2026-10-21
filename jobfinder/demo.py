"""Built-in FAKE jobs for trying the agent end to end, without the internet.

Everything here is invented. Dates are relative to the day you run it, and each job
is built to trigger one rule (fresh, stale, too senior, experience not stated, and so on).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from .models import Job


def demo_data(now: datetime) -> Tuple[List[dict], Dict[str, List[Job]]]:
    def ago(days: float) -> datetime:
        return now - timedelta(days=days)

    def job(board, company, job_id, title, location, days, text, pay=""):
        return Job(platform="demo", board=board, company=company, job_id=job_id, title=title,
                   location=location, url=f"https://example.com/{board}/jobs/{job_id}",
                   description=text, posted_at=None if days is None else ago(days), pay_text=pay)

    companies = [
        {"name": "Northwind Health (fake)", "platform": "demo", "board": "northwind",
         "email": "careers@northwind.example", "x_handle": "@northwind_founder"},
        {"name": "Kite Analytics (fake)", "platform": "demo", "board": "kite", "email": "", "x_handle": "@kite_ceo"},
        {"name": "Lumen Content (fake)", "platform": "demo", "board": "lumen",
         "email": "hello@lumen.example", "x_handle": ""},
    ]

    jobs = {
        "northwind": [
            job("northwind", companies[0]["name"], "101", "Associate Product Manager", "Hyderabad", 2,
                "0-1 years of experience. You will own a roadmap, run user research, work in agile sprints, "
                "write specs and track metrics with SQL. Stipend: Rs. 25,000 per month."),
            job("northwind", companies[0]["name"], "102", "Product Manager, AI", "Bengaluru", 1,
                "3+ years of experience in product management. Roadmap, strategy, stakeholders."),
            job("northwind", companies[0]["name"], "103", "Senior Product Manager", "Hyderabad", 1,
                "Lead the product team. Roadmap, strategy and metrics."),
            job("northwind", companies[0]["name"], "104", "Associate Product Manager", "Pune", 11,
                "Freshers welcome. Roadmap and user research."),
        ],
        "kite": [
            job("kite", companies[1]["name"], "201", "Founder's Office Associate", "Remote", 3,
                "Work directly with the founder on strategy, operations and zero to one projects. "
                "Stakeholder communication and presentations."),
            job("kite", companies[1]["name"], "202", "Product Analyst", "Hyderabad", 1,
                "Fresher or 0-1 years of experience. SQL, Excel and product analytics. Shift 10 am to 6 pm.",
                pay="INR 4-6 LPA"),
            job("kite", companies[1]["name"], "203", "Office Manager", "Hyderabad", 1,
                "Keep the office running."),
            job("kite", companies[1]["name"], "204", "Business Operations Associate", "Mumbai", 6,
                "2-4 years of experience in operations and project management."),
        ],
        "lumen": [
            job("lumen", companies[2]["name"], "301", "SEO Executive", "Hyderabad", 1,
                "Freshers welcome. Shift 10 am to 6 pm. SEO, keyword research, Ahrefs, Semrush and "
                "Google Search Console.", pay="INR 3.0-4.5 LPA"),
            job("lumen", companies[2]["name"], "302", "Content Strategist", "Bengaluru", 5,
                "1-2 years of experience. Content strategy, SEO, audience research and writing."),
            job("lumen", companies[2]["name"], "303", "GEO Specialist", "Remote", 9,
                "Generative engine optimization and SEO."),
            job("lumen", companies[2]["name"], "304", "Growth Associate", "Hyderabad", None,
                "Cold outreach, social media and metrics. No date on this posting."),
        ],
    }
    return companies, jobs
