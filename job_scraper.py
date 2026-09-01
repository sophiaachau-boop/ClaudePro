import csv
import pandas as pd
from jobspy import scrape_jobs

# 1. Target Configurations based on your priority list
TARGET_COMPANIES = [
    "Plasma Protein Therapeutics Association", "Global Health Strategies", "Pfizer",
    "PhRMA", "FINN Partners", "Novartis", "AHIP", "Guidehouse", "Bristol Myers Squibb",
    "BIO", "Rabin Martin", "Merck & Co.", "AdvaMed", "Mathematica", "AbbVie",
    "Association for Accessible Medicines", "Clearview Healthcare Partners", "Gilead Sciences",
    "Alliance for Regenerative Medicine", "Dalberg Global Development Advisors", "Genentech",
    "Healthcare Distribution Alliance", "Brownstein Hyatt Farber Schreck", "Sanofi",
    "ACHP", "Amgen", "American Heart Association", "GSK", "American Cancer Society",
    "Biogen", "Cigna"
]

BROAD_KEYWORDS = [
    "public health policy", "government affairs", "rotational program",
    "health economics", "regulatory affairs", "data curation"
]


def main():
    job_batches = []

    # Phase 1: Search specifically for your target companies
    print("Starting targeted company search...")
    for company in TARGET_COMPANIES:
        print(f"Scraping recent roles for: {company}")
        try:
            # We use the company name as the search term to force boards to return their listings
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "glassdoor"],
                search_term=f'"{company}"',
                location="Washington, DC",  # Will also catch remote/flexible roles tied to DC hubs
                results_wanted=10,
                hours_old=72,  # Looking back 3 days to catch weekend postings
                country_indeed="USA"
            )
            if not jobs.empty:
                jobs['search_source'] = f"Company: {company}"
                job_batches.append(jobs)
        except Exception as e:
            print(f"Error scraping {company}: {e}")

    # Phase 2: Search for broad entry-level policy/data roles in DC
    print("\nStarting broad keyword search in DC...")
    for keyword in BROAD_KEYWORDS:
        print(f"Scraping roles for: {keyword}")
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "glassdoor"],
                search_term=keyword,
                location="Washington, DC",
                results_wanted=15,
                hours_old=24,  # Only daily updates for broad keywords to avoid bloat
                country_indeed="USA"
            )
            if not jobs.empty:
                jobs['search_source'] = f"Keyword: {keyword}"
                job_batches.append(jobs)
        except Exception as e:
            print(f"Error scraping {keyword}: {e}")

    # Phase 3: Process, clean, and export the data
    if job_batches:
        print("\nProcessing results...")
        all_jobs = pd.concat(job_batches, ignore_index=True)

        # Deduplicate identical job URLs found across different search phases
        all_jobs = all_jobs.drop_duplicates(subset=["job_url"])

        # Filter out senior roles
        senior_keywords = ["senior", "director", "manager", "principal", "vp", "lead", "iii", "head"]
        pattern = "|".join(senior_keywords)

        # Ensure title column is treated as text to prevent filtering errors
        all_jobs["title"] = all_jobs["title"].fillna("")
        entry_level_jobs = all_jobs[~all_jobs["title"].str.lower().str.contains(pattern)]

        # Export to CSV for Claude Code
        output_file = "master_daily_jobs.csv"
        entry_level_jobs.to_csv(output_file, index=False, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\")
        print(f"\nSuccess! Saved {len(entry_level_jobs)} targeted, entry-level roles to {output_file}")
    else:
        print("\nNo new jobs found today. Try expanding the search radius or checking back tomorrow.")


if __name__ == "__main__":
    main()
