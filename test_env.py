"""Sanity checks: .env populated, Claude reachable, SQLite initializes."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()
REQUIRED = ["ANTHROPIC_API_KEY", "SENDER_EMAIL", "SENDER_APP_PASSWORD", "RECIPIENT_EMAIL"]
ok = True

print("[1] .env variables")
for key in REQUIRED:
    filled = bool(os.getenv(key, "").strip())
    ok &= filled
    print(f"    {'[✓]' if filled else '[x]'} {key}")

print("[2] Claude ping")
if os.getenv("ANTHROPIC_API_KEY", "").strip():
    try:
        import anthropic
        from job_agent import MODEL
        resp = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=5, temperature=0.0,
            messages=[{"role": "user", "content": "Say hi"}])
        print(f"    [✓] {MODEL} replied: {resp.content[0].text!r}")
    except Exception as exc:
        ok = False
        print(f"    [x] {exc}")
else:
    ok = False
    print("    [x] skipped (no API key)")

print("[3] SQLite")
try:
    import sqlite3
    from job_agent import DB_PATH, init_db
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(seen_jobs)")]
    print(f"    [✓] {DB_PATH} ready, columns: {cols}")
except Exception as exc:
    ok = False
    print(f"    [x] {exc}")

sys.exit(0 if ok else 1)
