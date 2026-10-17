from svglib import *

def build():
    s = Svg(1140, 830, "LeadBoost + Mailer Agent: system context",
            "Container view. A browser uses the Next.js frontend, which calls the LeadBoost FastAPI backend. "
            "LeadBoost has its own database and an in-process job loop. It calls the separately deployed Mailer API "
            "server-to-server over HTTPS with a per-organization API key. The Mailer has its own PostgreSQL database "
            "and a separate worker that generates messages, sends through SMTP and polls IMAP.")
    s.text(10, 26, "LeadBoost + Mailer Agent: system context", size=16, weight=700)
    s.text(10, 44, "Two independently deployed services with separate databases. Arrows show who initiates each call.", size=12, fill=MUTED)

    # --- zones
    s.zone(200, 60, 310, 650, "LeadBoost (control plane)", LB, "this repository")
    s.zone(620, 60, 310, 650, "Mailer Agent", MA, "separate service + repository")

    # --- client column
    s.box(10, 62, 130, 38, "User's browser", EX, title_size=12.5)
    s.box(10, 132, 130, 80, "Next.js frontend", LB, ["15 page routes", "talks to LeadBoost", "API only"])
    s.arrow([(75, 100), (75, 132)], "HTTPS", loff=(36, 0), lw=46)
    s.arrow([(140, 160), (215, 160)], "HTTPS · JWT", loff=(0, -13), lw=78)

    # --- LeadBoost interior
    s.box(215, 120, 280, 80, "LeadBoost API", LB, ["FastAPI · /api/v2 · org-scoped", "outreach authorization · Mailer client"])
    s.box(215, 240, 280, 70, "Discovery + identity resolution", LB, ["parse -> search -> resolve website", "validate -> dedupe -> rank"])
    s.box(215, 350, 280, 90, "Qualification pipeline", LB, ["LangGraph · scrape -> enrich -> score", "decide -> draft; each AI step has a", "deterministic fallback"])
    s.box(215, 480, 280, 70, "In-process job loop", LB, ["asyncio task inside the API process", "polls jobs table · atomic claim + lease"])
    s.db(215, 600, 280, 85, "LeadBoost database", LB, ["PostgreSQL in production (SQLite dev)", "leads · jobs · email accounts", "outreach actions · policy"])

    s.arrow([(290, 200), (290, 240)], "request", loff=(30, 0))
    s.arrow([(355, 310), (355, 350)], "inline job", loff=(40, 0))
    s.arrow([(440, 480), (440, 440)], "runs jobs", loff=(-38, 0))
    s.arrow([(355, 550), (355, 600)], "SQL", both=True, loff=(24, 0), lw=36)

    # --- LeadBoost external dependencies (left)
    s.box(10, 236, 130, 50, "Search providers", EX, ["OSM Overpass · Serper"], title_size=12.5, sub_size=10.5)
    s.box(10, 296, 130, 110, "Business websites", EX, ["fetched to validate", "identity and to scrape", "company data"], title_size=12.5, sub_size=10.5)
    s.box(10, 416, 130, 60, "LLM provider", EX, ["Groq · LeadBoost's", "own API key"], title_size=12.5, sub_size=10.5)
    s.arrow([(140, 262), (215, 262)], "HTTPS", loff=(0, -13), lw=42)
    s.arrow([(140, 303), (215, 303)], "HTTP(S)", loff=(0, 13), lw=52)
    s.arrow([(140, 395), (215, 395)], "HTTP(S)", loff=(0, -13), lw=52)
    s.arrow([(140, 430), (215, 430)], "HTTPS", loff=(0, -13), lw=42)

    # --- Mailer interior
    s.box(635, 120, 280, 80, "Mailer API", MA, ["FastAPI · gunicorn + uvicorn workers", "tenant resolved from X-API-Key"])
    s.db(635, 250, 280, 100, "Mailer database", MA, ["PostgreSQL · owned by the Mailer", "mailboxes (encrypted credentials)", "dispatches · contacts · messages"])
    s.box(635, 400, 280, 160, "Mailer worker", MA,
          ["APScheduler · exactly one instance", "generate message (LLM + grounding)", "send via SMTP · lease recovery", "poll IMAP · classify replies", "no HTTP server"])
    s.arrow([(775, 200), (775, 250)], "SQL", both=True, loff=(24, 0), lw=36)
    s.arrow([(775, 400), (775, 350)], "SQL", both=True, loff=(24, 0), lw=36)

    # --- Mailer externals (right)
    s.box(1010, 400, 120, 55, "LLM provider", EX, ["Groq · Mailer's", "own API key"], title_size=12.5, sub_size=10.5)
    s.box(1010, 465, 120, 50, "SMTP server", EX, ["sender mailbox"], title_size=12.5, sub_size=10.5)
    s.box(1010, 525, 120, 50, "IMAP mailbox", EX, ["reply intake"], title_size=12.5, sub_size=10.5)
    s.arrow([(915, 420), (1010, 420)], "HTTPS", loff=(0, -13), lw=42)
    s.arrow([(915, 490), (1010, 490)], "STARTTLS", loff=(0, -13), lw=64)
    s.arrow([(915, 550), (1010, 550)], "IMAP poll", st="dash", loff=(-2, -13), lw=62)
    s.text(1010, 592, "Worker initiates every", size=10.5, fill=MUTED)
    s.text(1010, 606, "external call; none run", size=10.5, fill=MUTED)
    s.text(1010, 620, "inside the Mailer API.", size=10.5, fill=MUTED)

    # --- cross-service calls (the only link between the two zones)
    s.arrow([(495, 150), (635, 150)], "dispatch · mailbox", loff=(0, -13), lw=112)
    s.arrow([(495, 182), (635, 182)], "read-only GET state", loff=(0, 15), lw=122)
    s.add('<line x1="565" y1="214" x2="565" y2="700" stroke="#9CA3AF" stroke-width="1.4" stroke-dasharray="3 5"/>')
    s.text(565, 232, "trust", size=10.5, fill=MUTED, anchor="middle")
    s.text(565, 245, "boundary", size=10.5, fill=MUTED, anchor="middle")

    # --- caption
    s.note(200, 728, 730, 56,
           ["Separate repositories, deployments and databases: no shared ORM models, transaction or encryption key.",
            "Credentials that cross: the per-organization API key on every call; an SMTP credential only during mailbox provisioning.",
            "The browser never receives a Mailer URL or key."],
           st=EX, size=11)
    s.legend(200, 808, [(LB, "LeadBoost-owned"), (MA, "Mailer-owned"), (EX, "External service")])
    return s.render()
