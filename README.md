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

Note: LinkedIn, Indeed, and Glassdoor actively block scraping, so
`job_scraper.py` may get rate-limited or blocked depending on your network.
See `ats_scraper.py` below for a more reliable alternative against a subset
of companies.

## Alternative: `ats_scraper.py` (Greenhouse / Lever)

Many companies host their job listings on Greenhouse or Lever, both of which
expose stable, unauthenticated JSON APIs with no anti-bot protection:

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/<token>/jobs`
- Lever: `https://api.lever.co/v0/postings/<token>`

This is more reliable than scraping LinkedIn/Indeed/Glassdoor, but only
covers companies that use one of these two ATS platforms (not Workday,
iCIMS, SuccessFactors, etc.), and it can only list a given company's jobs —
there's no cross-company keyword search like Phase 2 of `job_scraper.py`.

To use it:

1. Open each company's careers page and find its Greenhouse or Lever board
   token. The board URL usually looks like `boards.greenhouse.io/<token>` or
   `jobs.lever.co/<token>`; if the page doesn't show it directly, check your
   browser's devtools Network tab for the API call.
2. Fill in `COMPANY_BOARDS` in `ats_scraper.py`:
   ```python
   COMPANY_BOARDS = {
       "Example Co": {"ats": "greenhouse", "token": "examplecoken"},
       "Another Co": {"ats": "lever", "token": "anothercotoken"},
   }
   ```
3. Run it:
   ```bash
   python ats_scraper.py
   ```

Results are filtered to roles in Washington, DC or Remote, posted within
`MAX_AGE_DAYS` (default 14), deduplicated, filtered to exclude senior-level
titles, and exported to `ats_daily_jobs.csv`.
