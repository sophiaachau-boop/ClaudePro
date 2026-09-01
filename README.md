# ClaudePro Job Scraper

Scrapes LinkedIn, Indeed, and Glassdoor for entry-level public health policy /
government affairs roles in Washington, DC.

The scraper runs in two phases:

1. **Targeted company search** — looks for recent postings (last 72 hours)
   from a fixed list of companies and organizations in `TARGET_COMPANIES`.
2. **Broad keyword search** — looks for recent postings (last 24 hours)
   matching general policy/data keywords in `BROAD_KEYWORDS`.

Results from both phases are deduplicated by job URL, filtered to exclude
senior-level titles (senior, director, manager, principal, VP, lead, III,
head), and exported to `master_daily_jobs.csv`.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
python job_scraper.py
```

Output is written to `master_daily_jobs.csv` in the current directory.
