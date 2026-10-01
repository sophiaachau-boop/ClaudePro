import csv
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

# Fill this in with the companies you want to track. Visit each company's
# careers page and check the URL / network requests in devtools:
#   - Greenhouse board pages look like boards.greenhouse.io/<token>
#     or a company's own domain with a Greenhouse-hosted iframe.
#   - Lever board pages look like jobs.lever.co/<token>.
# The token is the only thing this script needs per company.
COMPANY_BOARDS = {
    # "Example Co": {"ats": "greenhouse", "token": "examplecoken"},
    # "Another Co": {"ats": "lever", "token": "anothercotoken"},
}

LOCATION_FILTERS = ["washington", "dc", "remote"]
SENIOR_KEYWORDS = ["senior", "director", "manager", "principal", "vp", "lead", "iii", "head"]
MAX_AGE_DAYS = 14


def fetch_greenhouse_jobs(token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    resp = requests.get(url, params={"content": "true"}, timeout=20)
    resp.raise_for_status()
    jobs = []
    for job in resp.json().get("jobs", []):
        jobs.append({
            "title": job.get("title", ""),
            "location": (job.get("location") or {}).get("name", ""),
            "job_url": job.get("absolute_url", ""),
            "updated_at": job.get("updated_at", ""),
        })
    return jobs


def fetch_lever_jobs(token: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{token}"
    resp = requests.get(url, params={"mode": "json"}, timeout=20)
    resp.raise_for_status()
    jobs = []
    for job in resp.json():
        created_ms = job.get("createdAt")
        updated_at = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
            if created_ms else ""
        )
        jobs.append({
            "title": job.get("text", ""),
            "location": (job.get("categories") or {}).get("location", ""),
            "job_url": job.get("hostedUrl", ""),
            "updated_at": updated_at,
        })
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
}


def is_recent(updated_at: str, max_age_days: int) -> bool:
    if not updated_at:
        return True  # keep jobs with no timestamp rather than drop them
    try:
        posted = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - posted <= timedelta(days=max_age_days)


def is_target_location(location: str) -> bool:
    location = location.lower()
    return any(term in location for term in LOCATION_FILTERS)


def main():
    if not COMPANY_BOARDS:
        print("COMPANY_BOARDS is empty. Add company -> {ats, token} entries and re-run.")
        return

    job_batches = []
    for company, board in COMPANY_BOARDS.items():
        ats = board["ats"]
        token = board["token"]
        fetcher = FETCHERS.get(ats)
        if fetcher is None:
            print(f"Skipping {company}: unknown ATS type '{ats}'")
            continue

        print(f"Fetching {company} ({ats}: {token})...")
        try:
            jobs = fetcher(token)
        except Exception as e:
            print(f"Error fetching {company}: {e}")
            continue

        df = pd.DataFrame(jobs)
        if df.empty:
            continue
        df = df[df["location"].apply(is_target_location)]
        df = df[df["updated_at"].apply(lambda ts: is_recent(ts, MAX_AGE_DAYS))]
        if df.empty:
            continue
        df["company"] = company
        df["search_source"] = f"Company: {company}"
        job_batches.append(df)

    if not job_batches:
        print("\nNo new jobs found. Try widening LOCATION_FILTERS or MAX_AGE_DAYS.")
        return

    all_jobs = pd.concat(job_batches, ignore_index=True)
    all_jobs = all_jobs.drop_duplicates(subset=["job_url"])

    pattern = "|".join(SENIOR_KEYWORDS)
    all_jobs["title"] = all_jobs["title"].fillna("")
    entry_level_jobs = all_jobs[~all_jobs["title"].str.lower().str.contains(pattern)]

    output_file = "ats_daily_jobs.csv"
    entry_level_jobs.to_csv(output_file, index=False, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
    print(f"\nSuccess! Saved {len(entry_level_jobs)} targeted, entry-level roles to {output_file}")


if __name__ == "__main__":
    main()
