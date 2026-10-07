"""Automated Job Matcher & Alerting agent.

Pipeline: fetch open listings -> drop already-seen jobs -> score with Claude
-> email an HTML alert for eligible roles scoring >= ALERT_THRESHOLD.
"""
import html
import json
import os
import re
import smtplib
import sqlite3
import ssl
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "jobs_tracker.db"
MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022")
ALERT_THRESHOLD = 80
THROTTLE_SECONDS = 1
MAX_DESC_CHARS = 6000
HTTP_TIMEOUT = 20
HEADERS = {"User-Agent": "job-matcher-agent/1.0 (personal use)"}

# Expandable: add Greenhouse board tokens / Lever company slugs you care about.
GREENHOUSE_BOARDS = ["mathematica", "guidehouse", "gileadsciences"]
LEVER_COMPANIES = []

CANDIDATE_PROFILE = """\
TARGET ROLES: Health Policy Analyst, Healthcare Consultant, Data Analyst, Associate, Coordinator
LOCATIONS: Washington, D.C. Metro Area or Remote (US)
MINIMUM COMPENSATION: $55,000
GOAL COMPENSATION: $70,000
WORK AUTHORIZATION: US Citizen / No sponsorship required
EXPERIENCE LEVEL: Entry-level, 0-2 years, New Graduate
BACKGROUND: Senior at the University of Maryland, graduating December 2026 with degrees in
Public Health Policy and Analytics and Neuroscience. Interest in health policy began behind a
pharmacy counter, seeing patients struggle to afford medications. Analyzed federal health policy
at Grifols, conducted NIH-funded health services research, analyzed public health data in R.
Currently a Consulting Fellow with UMD's Impact Consulting Fellowship (disability policy research,
economic analysis quantifying the value of an employment program for people with disabilities).
Director of Communications for the Maryland Student Legislature (leads a five-person team).
Seeking an entry-level role in health consulting, policy research, or health strategy.
"""

SYSTEM_PROMPT = f"""You are an executive talent screener and recruiter. Compare the candidate
profile against the job description and respond with JSON only.

CANDIDATE PROFILE
{CANDIDATE_PROFILE}
STRICT GATING RULES - set is_eligible to false if ANY apply:
- The job requires a security clearance the candidate does not have.
- The job requires senior/executive-level experience (5+ years) or is a senior/manager/director role.
- The job is located outside Washington, D.C. metro / US-remote with no remote option.
- The job requires visa sponsorship-incompatible terms or non-US work authorization.
- Stated pay is clearly below $55,000/year.

Score match_score from 0 to 100 based on actual skill alignment (policy analysis, data/R skills,
health focus, communication, entry-level fit, pay vs. $70k goal). Be honest; do not inflate.

Return ONLY a JSON object with exactly this schema:
{{"is_eligible": bool, "match_score": int, "detected_salary": str, "detected_location": str,
"qualification_verdict": str (1-2 sentences), "key_strengths": [str], "potential_gaps": [str]}}
Use "Not listed" when salary or location is not stated."""


# ---------------------------------------------------------------- database
def init_db(path: str = DB_PATH) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS seen_jobs (
                id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                url TEXT,
                match_score INTEGER,
                is_eligible INTEGER,
                alerted_at TEXT
            )"""
        )


def is_job_seen(job_id: str, path: str = DB_PATH) -> bool:
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT 1 FROM seen_jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None


def mark_job_seen(job_id, title, company, url, match_score, is_eligible,
                  alerted_at=None, path: str = DB_PATH) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO seen_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, title, company, url, match_score, int(bool(is_eligible)), alerted_at),
        )


# --------------------------------------------------------------- ingestion
def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:MAX_DESC_CHARS]


def _job(id_, title, company, location, salary, description, url) -> dict:
    return {"id": str(id_), "title": title or "", "company": company or "",
            "location": location or "Not listed", "salary": salary or "Not listed",
            "description": _clean(description), "url": url or ""}


def _get(url: str, **kwargs):
    resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


def fetch_remoteok() -> list[dict]:
    data = _get("https://remoteok.com/api").json()
    jobs = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue
        lo, hi = j.get("salary_min"), j.get("salary_max")
        salary = f"${lo:,}-${hi:,}" if lo and hi else None
        jobs.append(_job(f"remoteok-{j.get('id')}", j["position"], j.get("company"),
                         j.get("location") or "Remote", salary, j.get("description"), j.get("url")))
    return jobs


def fetch_jobicy() -> list[dict]:
    data = _get("https://jobicy.com/api/v2/remote-jobs",
                params={"count": 50, "geo": "usa"}).json()
    jobs = []
    for j in data.get("jobs", []):
        lo, hi = j.get("annualSalaryMin"), j.get("annualSalaryMax")
        salary = f"{j.get('salaryCurrency', '')} {lo}-{hi}".strip() if lo and hi else None
        jobs.append(_job(f"jobicy-{j.get('id')}", j.get("jobTitle"), j.get("companyName"),
                         j.get("jobGeo"), salary,
                         j.get("jobDescription") or j.get("jobExcerpt"), j.get("url")))
    return jobs


def fetch_greenhouse(board: str) -> list[dict]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                params={"content": "true"}).json()
    return [_job(f"gh-{board}-{j['id']}", j.get("title"), board.title(),
                 (j.get("location") or {}).get("name"), None,
                 j.get("content"), j.get("absolute_url"))
            for j in data.get("jobs", [])]


def fetch_lever(company: str) -> list[dict]:
    data = _get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"}).json()
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        jobs.append(_job(f"lever-{company}-{j['id']}", j.get("text"), company.title(),
                         cats.get("location"), None,
                         j.get("descriptionPlain") or j.get("description"), j.get("hostedUrl")))
    return jobs


def fetch_all_jobs() -> list[dict]:
    """Pull from every configured source; one failing source never stops the rest."""
    sources = [("RemoteOK", fetch_remoteok), ("Jobicy", fetch_jobicy)]
    sources += [(f"Greenhouse:{b}", lambda b=b: fetch_greenhouse(b)) for b in GREENHOUSE_BOARDS]
    sources += [(f"Lever:{c}", lambda c=c: fetch_lever(c)) for c in LEVER_COMPANIES]
    jobs: list[dict] = []
    for name, fn in sources:
        try:
            batch = fn()
            print(f"[*] Fetched {len(batch)} listings from {name}")
            jobs.extend(batch)
        except Exception as exc:  # network / schema errors are non-fatal
            print(f"[!] {name} failed: {exc}")
    return jobs


def filter_new_jobs(jobs: list[dict]) -> list[dict]:
    """Drop already-seen and duplicate-in-batch jobs BEFORE any LLM call."""
    unique = {j["id"]: j for j in jobs if j["id"] and j["url"]}
    return [j for j in unique.values() if not is_job_seen(j["id"])]


# -------------------------------------------------------------- evaluation
def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in model response")
    return json.loads(match.group(0))


def evaluate_job(client, job: dict) -> dict:
    user_msg = (f"JOB TITLE: {job['title']}\nCOMPANY: {job['company']}\n"
                f"LOCATION: {job['location']}\nSALARY: {job['salary']}\n"
                f"URL: {job['url']}\n\nDESCRIPTION:\n{job['description']}")
    resp = client.messages.create(
        model=MODEL, max_tokens=700, extra_body={"temperature": 0.0}, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg},
                  {"role": "assistant", "content": "{"}],  # prefill forces JSON
    )
    result = _parse_json("{" + resp.content[0].text)
    return {
        "is_eligible": bool(result.get("is_eligible")),
        "match_score": int(result.get("match_score", 0)),
        "detected_salary": str(result.get("detected_salary", "Not listed")),
        "detected_location": str(result.get("detected_location", "Not listed")),
        "qualification_verdict": str(result.get("qualification_verdict", "")),
        "key_strengths": list(result.get("key_strengths", [])),
        "potential_gaps": list(result.get("potential_gaps", [])),
    }


# ------------------------------------------------------------------- email
def build_email_html(job: dict, ev: dict) -> str:
    e = html.escape
    strengths = "".join(f"<li>{e(s)}</li>" for s in ev["key_strengths"])
    gaps = "".join(f"<li>{e(g)}</li>" for g in ev["potential_gaps"])
    return f"""\
<html><body style="font-family:Arial,sans-serif;background:#f4f6f8;padding:20px;">
<div style="max-width:600px;margin:auto;background:#fff;border-radius:8px;padding:24px;">
  <div style="font-size:42px;font-weight:bold;color:#1a7f37;">{ev['match_score']}<span style="font-size:18px;color:#666;">/100 match</span></div>
  <h2 style="margin:8px 0 0;">{e(job['title'])}</h2>
  <div style="color:#555;margin-bottom:16px;">{e(job['company'])}</div>
  <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
    <tr><td style="padding:6px 0;color:#888;width:90px;">Salary</td><td>{e(ev['detected_salary'])}</td></tr>
    <tr><td style="padding:6px 0;color:#888;">Location</td><td>{e(ev['detected_location'])}</td></tr>
  </table>
  <p style="background:#eef6ff;border-left:4px solid #2f81f7;padding:10px 12px;">{e(ev['qualification_verdict'])}</p>
  <h4 style="margin-bottom:4px;">Matched strengths</h4><ul style="margin-top:0;">{strengths}</ul>
  <h4 style="margin-bottom:4px;">Potential gaps</h4><ul style="margin-top:0;">{gaps}</ul>
  <p style="text-align:center;margin-top:24px;">
    <a href="{e(job['url'], quote=True)}" style="background:#1a7f37;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;">Apply Now</a>
  </p>
</div></body></html>"""


def send_alert(job: dict, ev: dict) -> None:
    sender = os.environ["SENDER_EMAIL"]
    password = os.environ["SENDER_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{ev['match_score']}] {job['title']} @ {job['company']}"
    msg["From"], msg["To"] = sender, recipient
    msg.attach(MIMEText(f"{job['title']} at {job['company']} - score {ev['match_score']}\n"
                        f"{ev['qualification_verdict']}\nApply: {job['url']}", "plain"))
    msg.attach(MIMEText(build_email_html(job, ev), "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())


# ---------------------------------------------------------------- pipeline
def main() -> None:
    import anthropic

    init_db()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    print("[*] Fetching job listings...")
    new_jobs = filter_new_jobs(fetch_all_jobs())
    print(f"[*] {len(new_jobs)} new jobs to evaluate")

    alerts = 0
    for job in new_jobs:
        label = f"{job['title']} @ {job['company']}"
        try:
            ev = evaluate_job(client, job)
        except Exception as exc:
            # Not marked seen, so it is retried on the next scheduled run.
            print(f"[!] Evaluation failed for {label}: {exc}")
            time.sleep(THROTTLE_SECONDS)
            continue

        alerted_at = None
        if ev["is_eligible"] and ev["match_score"] >= ALERT_THRESHOLD:
            try:
                send_alert(job, ev)
                alerted_at = datetime.now(timezone.utc).isoformat()
                alerts += 1
                print(f"[✓] Alert sent: {label} (score {ev['match_score']})")
            except Exception as exc:
                print(f"[!] Email failed for {label}: {exc}")
                time.sleep(THROTTLE_SECONDS)
                continue  # leave unseen so the alert is retried next run
        elif not ev["is_eligible"]:
            print(f"[-] Disqualified: {label} - {ev['qualification_verdict']}")
        else:
            print(f"[-] Below threshold: {label} (score {ev['match_score']})")

        mark_job_seen(job["id"], job["title"], job["company"], job["url"],
                      ev["match_score"], ev["is_eligible"], alerted_at)
        time.sleep(THROTTLE_SECONDS)

    print(f"[*] Done. {alerts} alert(s) sent.")


if __name__ == "__main__":
    main()
