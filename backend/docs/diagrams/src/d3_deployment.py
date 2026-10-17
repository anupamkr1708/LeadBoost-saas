from svglib import *

def build():
    s = Svg(1100, 905, "LeadBoost + Mailer deployment topology",
            "Deployment topology with separate services and separate databases: a Next.js frontend service; one LeadBoost "
            "backend process running the API and the in-process job loop, with its own PostgreSQL; a Mailer API service "
            "and exactly one Mailer worker service sharing the Mailer's own PostgreSQL. Provider-neutral.")
    s.text(10, 26, "Deployment topology: separate services, separate databases", size=16, weight=700)
    s.text(10, 44, "Provider-neutral. Hostnames, regions and the hosting provider are intentionally not recorded in this repository.", size=12, fill=MUTED)

    s.box(10, 70, 160, 40, "User's browser", EX, title_size=12.5)
    s.box(10, 150, 160, 110, "Frontend service", LB, ["Next.js 14 build", "NEXT_PUBLIC_API_URL is", "baked in at build time", "(LeadBoost backend URL)"], sub_size=11)
    s.arrow([(90, 110), (90, 150)], "HTTPS", loff=(34, 0), lw=46)

    # LeadBoost zone
    s.zone(230, 60, 330, 575, "LeadBoost", LB, "one deployable")
    s.box(245, 100, 300, 200, "Backend service (single process)", LB,
          ["uvicorn --workers 1: API and the", "in-process job loop in one process",
           "JOB_WORKER_ENABLED=true (default)", "",
           "ENVIRONMENT, SECRET_KEY, DATABASE_URL,", "ALLOWED_ORIGINS (no * in production)",
           "EMAIL_CREDENTIAL_ENCRYPTION_KEY",
           "MAILING_AGENT_BASE_URL (https)", "MAILING_AGENT_ORG_API_KEYS (secret)"], sub_size=11)
    s.arrow([(170, 205), (245, 205)], "HTTPS", loff=(0, -13), lw=46)
    s.db(245, 360, 300, 80, "LeadBoost PostgreSQL", LB, ["leads · jobs · email accounts · actions"])
    s.arrow([(395, 300), (395, 360)], "SQL", both=True, loff=(24, 0), lw=36)
    s.note(245, 470, 300, 80,
           ["alembic upgrade head runs as its own", "release step, once per deploy, never in", "app startup. Back up and verify first."],
           st=EX, title="Migrations", size=11)
    s.box(245, 575, 300, 48, "External: search, LLM, sites", EX, ["Overpass · Serper · Groq · business websites"], title_size=12, sub_size=11)

    # Mailer zone
    s.zone(660, 60, 430, 575, "Mailer Agent", MA, "separate repository")
    s.box(675, 100, 400, 165, "API service", MA,
          ["gunicorn, 2 uvicorn workers; no scheduler", "RUN_SCHEDULER_IN_PROCESS=false",
           "ORG_KEY_MAP (secret): API key maps to organization",
           "SYSTEM_OPERATOR_API_KEY (secret, differs)",
           "LEADBOOST_INTEGRATION_SENDER_EMAIL",
           "API_DOCS_ENABLED=false (default)"], sub_size=11)
    s.db(675, 300, 400, 80, "Mailer PostgreSQL", MA, ["its own database; never LeadBoost's"])
    s.box(675, 420, 400, 150, "Worker service (exactly one instance)", MA,
          ["python worker.py: APScheduler jobs", "generation · send · lease recovery · IMAP poll",
           "LIVE_SENDING_ENABLED gates real SMTP sends", "AUTO_REPLY_ENABLED (no effect on LeadBoost contacts)",
           "SIGTERM: drain sends, then SENDING -> UNKNOWN"], sub_size=11)
    s.arrow([(875, 265), (875, 300)], "SQL", both=True, loff=(24, 0), lw=36)
    s.arrow([(875, 420), (875, 380)], "SQL", both=True, loff=(24, 0), lw=36)

    # cross-service
    s.arrow([(545, 150), (675, 150)], "HTTPS", loff=(0, -13), lw=46)
    s.arrow([(545, 182), (675, 182)], "X-API-Key", loff=(0, 15), lw=70)

    # Mailer externals
    s.box(660, 665, 130, 50, "LLM provider", EX, ["Mailer's own key"], title_size=12, sub_size=11)
    s.box(805, 665, 130, 50, "SMTP server", EX, ["STARTTLS"], title_size=12, sub_size=11)
    s.box(950, 665, 130, 50, "IMAP mailbox", EX, ["IMAPS poll"], title_size=12, sub_size=11)
    s.arrow([(725, 570), (725, 665)], None)
    s.arrow([(870, 570), (870, 665)], None)
    s.arrow([(1015, 665), (1015, 570)], None, st="dash")

    # agreement callouts
    s.note(10, 745, 530, 118,
           ["MAILING_AGENT_ORG_API_KEYS {org_id: key} and ORG_KEY_MAP {key: org_id}", "must agree for every LeadBoost organization.",
            "Keep stable across restarts and rollbacks:", "SECRET_KEY, EMAIL_CREDENTIAL_ENCRYPTION_KEY, DATABASE_URL and", "the key-map entries. Changing the first two orphans stored data."],
           st=LB, title="Must agree or stay stable (LeadBoost side)", size=11)
    s.note(560, 745, 530, 118,
           ["Identical on API and worker: DATABASE_URL, MAILBOX_ENCRYPTION_KEY.", "The encryption key is not shared with LeadBoost; losing it makes",
            "stored mailbox credentials unreadable (keep an offline backup).", "Keep exactly one worker: it owns the scheduler. Pause sending by",
            "suspending the worker, not by flipping LIVE_SENDING_ENABLED."],
           st=MA, title="Must match or stay single (Mailer side)", size=11)
    s.legend(10, 890, [(LB, "LeadBoost"), (MA, "Mailer"), (EX, "External / operational")])
    return s.render()
