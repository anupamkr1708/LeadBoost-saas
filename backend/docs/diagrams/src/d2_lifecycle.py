from svglib import *

LANES = [
    ("User / frontend", LB),
    ("LeadBoost API", LB),
    ("LeadBoost DB + jobs", LB),
    ("Mailer API", MA),
    ("Mailer DB + worker", MA),
    ("SMTP · IMAP", EX),
]
LW, X0 = 178, 10
CX = [X0 + LW * i + LW // 2 for i in range(6)]


def build():
    W = X0 * 2 + LW * 6
    s = Svg(W, 1700, "LeadBoost to Mailer outreach lifecycle",
            "Swimlane sequence across six lanes: user and frontend, LeadBoost API, LeadBoost database and jobs, "
            "Mailer API, Mailer database and worker, and SMTP and IMAP. It covers sender verification and mailbox "
            "synchronization, outreach approval and dispatch, durable acceptance, message generation, SMTP sending, "
            "IMAP reply intake, and the read-only conversation view, with failure and unknown paths.")
    s.text(10, 26, "LeadBoost to Mailer outreach lifecycle", size=16, weight=700)
    s.text(10, 44, "Accepted does not mean sent, and sent does not mean delivered. Dashed arrows are responses or polling.", size=12, fill=MUTED)

    HEAD_Y = 58
    for i, (name, st) in enumerate(LANES):
        x = X0 + LW * i + 4
        s.rect(x, HEAD_Y, LW - 8, 30, st, r=6, fill=st["tint"])
        s.text(x + (LW - 8) / 2, HEAD_Y + 20, name, size=12.5, weight=700, fill=st["ink"], anchor="middle")

    life = []  # lifelines drawn first (inserted before content)
    n = [0]

    def lane_line(y1, y2):
        for c in CX:
            life.append(f'<line x1="{c}" y1="{y1}" x2="{c}" y2="{y2}" stroke="#D1D5DB" stroke-width="1.2" stroke-dasharray="4 5"/>')

    y = [100]
    bands = []

    bottom = [0]

    def phase(title, sub):
        y[0] = max(y[0], bottom[0]) + 20
        bands.append((y[0], title, sub))
        y[0] += 34

    def msg(a, b, lines, style="solid", gap=None, hi=False):
        n[0] += 1
        gap = gap or (36 + 13 * len(lines))
        y[0] += gap
        yy = y[0]
        x1, x2 = CX[a], CX[b]
        d = 1 if x2 > x1 else -1
        pts = [(x1 + 4 * d, yy), (x2 - 4 * d, yy)]
        pending.append(("arrow", pts, style))
        # label above arrow, centered over the span
        mid = (x1 + x2) / 2
        w = max(len(t) for t in lines) * 6.35 + 12
        for k, t in enumerate(lines):
            ly = yy - 12 - 13 * (len(lines) - 1 - k)
            pending.append(("label", mid + (10 if d > 0 else 10), ly, t, w, hi))
        pending.append(("badge", x1 + 4 * d + 14 * d, yy - 0, n[0]))
        return yy

    def act(lane, lines, st=MA, h=None, gap=14):
        n[0] += 0
        h = h or (14 + 14 * len(lines))
        y[0] += gap + 2
        pending.append(("act", lane, y[0], h, lines, st))
        y[0] += h
        bottom[0] = max(bottom[0], y[0])
        return y[0] - h

    def note(lane, y_top, h, title, lines_, st=WN, span=1):
        h = 34 + 13.5 * len(lines_)
        bottom[0] = max(bottom[0], y_top + h)
        pending.append(("note", lane, y_top, h, title, lines_, st, span))

    pending = []

    # ---------------- Phase A
    phase("A · Sender setup", "LeadBoost verifies the mailbox; the Mailer receives the credential only here")
    a0 = y[0]
    msg(0, 1, ["Create sender account", "then verify it"])
    msg(1, 5, ["LeadBoost's own SMTP login test (STARTTLS)"])
    msg(1, 2, ["Save VERIFIED and", "mailer_sync_state = pending"])
    ya = msg(1, 3, ["Provision mailbox: credential", "sent once, only in this call"])
    msg(3, 4, ["Mailbox stored ACTIVE;", "credential encrypted"])
    msg(3, 1, ["Mailbox reference"], style="dash")
    yb = msg(1, 2, ["Save reference;", "mailer_sync_state = synced"])
    note(0, ya - 52, 160, "If the Mailer call fails",
         ["Stays 'pending' with a safe", "error code. Dispatch is", "refused until a retry", "converges (verify again, or", "POST …/mailer-sync)."])

    # ---------------- Phase B
    phase("B · Authorize and dispatch", "LeadBoost authorizes; it sends no credential and no message text")
    msg(0, 1, ["Create outreach action", "(idempotency key), approve"])
    msg(1, 2, ["pending_review -> approved"])
    msg(0, 1, ["Dispatch"], gap=34)
    yd = msg(1, 2, ["approved -> dispatching", "(atomic claim; one winner)"])
    msg(1, 3, ["POST …/outreach-requests: recipient +", "business context, same idempotency key"], gap=48)
    msg(3, 4, ["One commit: dispatch QUEUED", "(durable acceptance)"])
    ye = msg(3, 1, ["202 accepted: NOT sent yet"], style="warn", hi=True)
    yf = msg(1, 2, ["dispatching -> submitted"])
    note(0, yd - 36, 188, "Acceptance unclear",
         ["Timeout, 5xx, 409 or bad", "reply: dispatch_failed with", "a safe code. A retry sends", "the SAME key; the Mailer", "replays the first operation", "and creates no second one."])

    # ---------------- Phase C
    phase("C · Generate and send", "Mailer worker only; never inside an HTTP request")
    yc = act(4, ["Claim dispatch; generate a", "message (LLM + grounding)"], gap=18)
    msg(4, 5, ["SMTP send (STARTTLS)"], gap=36)
    yc2 = act(4, ["Record SENT, FAILED", "or UNKNOWN"], gap=16)
    note(3, yc - 6, 150, "Outcomes",
         ["LLM error or blocked draft:", "FAILED, nothing is sent.", "Ambiguous SMTP result:", "UNKNOWN, never auto-resent.", "Only a confirmed SMTP", "handoff is SENT."], st=WN)

    # ---------------- Phase D
    phase("D · Reply intake", "Per-mailbox IMAP poll; bound to the mailbox's organization")
    msg(5, 4, ["IMAP poll finds reply"], style="dash", gap=34)
    yr = act(4, ["Correlate to the contact,", "store, classify, update", "state (no auto-send)"], gap=16)
    note(3, yr - 6, 96, "Excluded from the view",
         ["Mail with no provable", "mailbox owner is not shown."], st=EX)

    # ---------------- Phase E
    phase("E · Read-only conversation view", "LeadBoost displays the Mailer's record; it never heals or retries from here")
    msg(0, 1, ["Open outreach:", "GET …/mailer-state"])
    msg(1, 3, ["GET …/conversation?limit=20", "(read-only, bounded window)"], gap=48)
    msg(3, 1, ["Dispatch state +", "messages (strict schema)"], style="dash")
    yz = msg(1, 0, ["Product view: availability,", "state, plain-text messages"], style="dash")
    note(4, yz - 90, 96, "Mailer unavailable",
         ["404, timeout, 5xx or schema", "drift: reported as an", "availability flag, HTTP 200.", "Nothing is retried or healed."], span=2)

    # ---------------- footer callout
    y[0] = max(y[0], bottom[0]) + 24
    yy = y[0]
    pending.append(("callout", yy))
    y[0] += 74
    H = y[0] + 22
    s.h = H

    # draw lifelines first
    lane_line(HEAD_Y + 32, yy - 8)
    for l in life:
        s.add(l)
    for (by, t, sub) in bands:
        s.add(f'<rect x="{X0}" y="{by}" width="{W-2*X0}" height="26" rx="5" fill="#F9FAFB" stroke="#D1D5DB"/>')
        s.text(X0 + 10, by + 17, t, size=12.5, weight=700, fill=INK)
        s.text(X0 + 10 + len(t) * 8 + 16, by + 17, sub, size=11.5, fill=MUTED)
    for item in pending:
        k = item[0]
        if k == "arrow":
            _, pts, st = item
            s.arrow(pts, None, st=st)
        elif k == "label":
            _, cx, ly, t, w, hi = item
            col = WN["ink"] if hi else INK
            s.add(f'<rect x="{cx-w/2:.1f}" y="{ly-10:.1f}" width="{w:.1f}" height="14" fill="#FFFFFF" fill-opacity="0.92"/>')
            s.text(round(cx, 1), round(ly + 1, 1), t, size=11.5, weight=700 if hi else 500, fill=col, anchor="middle")
        elif k == "badge":
            _, bx, by, num = item
            s.add(f'<circle cx="{bx}" cy="{by}" r="8.5" fill="#1F2937"/>')
            s.text(bx, by + 4, str(num), size=10, weight=700, fill="#FFFFFF", anchor="middle")
        elif k == "act":
            _, lane, ay, h, lines_, st = item
            x = X0 + LW * lane + 8
            s.rect(x, ay, LW - 16, h, st, r=6)
            s.lines(x + (LW - 16) / 2, ay + 18, lines_, size=11, fill=INK, anchor="middle", gap=14)
        elif k == "note":
            _, lane, ny, h, title, lines_, st, span = item
            x = X0 + LW * lane + 8
            s.rect(x, ny, LW * span - 16, h, st, r=6, dash="5 3", sw=1.4)
            s.text(x + 8, ny + 17, title, size=11.5, weight=700, fill=st["ink"])
            s.lines(x + 8, ny + 33, lines_, size=11, fill=INK, gap=13.5)
        elif k == "callout":
            _, cy = item
            s.rect(X0, cy, W - 2 * X0, 62, WN, r=8, sw=1.8)
            s.text(X0 + 14, cy + 22, "Accepted, sent and delivered are three different facts: durably queued, SMTP handoff succeeded, and received.", size=12.5, weight=700, fill=WN["ink"])
            s.text(X0 + 14, cy + 42, "No step in this flow observes delivery to the recipient's inbox. UNKNOWN means delivery is unconfirmed, not failed; the UI says not to resend.", size=11.5, fill=INK)
    return s.render()
