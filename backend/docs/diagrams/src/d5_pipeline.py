from svglib import *

BW, BH = 184, 106          # box size
X0 = 33                    # first box x
GAP = 49                   # uniform gap for the two LeadBoost rows


def build():
    W = 1180
    s = Svg(W, 716, "LeadBoost end-to-end pipeline",
            "Three-row pipeline in fifteen numbered stages. Row one: query parsing, business search, identity resolution, "
            "validation, deduplication and ranking, and lead creation. Row two, right to left: scraping and normalization, "
            "enrichment, LangGraph qualification, the review gate with the outreach draft, and persistence. Row three: "
            "sender setup, authorization and dispatch in LeadBoost, then message generation, SMTP sending and IMAP reply "
            "intake in the separate Mailer, and LeadBoost's read-only conversation view.")
    s.text(20, 26, "LeadBoost end-to-end pipeline", size=16, weight=700)
    s.text(20, 44, "From a plain-language query to a visible email conversation. Fifteen stages, three rows; row two runs right to left.", size=12, fill=MUTED)

    def xs(i):  # uniform grid for rows A and B
        return X0 + i * (BW + GAP)

    def card(x, y, n, title, lines_, st=LB):
        s.rect(x, y, BW, BH, st, r=9)
        s.text(x + BW / 2 + 6, y + 26, title, size=13, weight=700, fill=st["ink"], anchor="middle")
        s.lines(x + BW / 2, y + 46, lines_, size=11.5, fill=INK, anchor="middle", gap=15)
        s.badge(x + 15, y + 1, n, st)

    # ------------------------------------------------ Row A: discovery
    ya_zone, ya = 60, 100
    s.zone(20, ya_zone, 1140, 164, "LeadBoost: discovery", LB)
    A = [
        ("Query", ["Plain-language request;", "deterministic parser:", "regex + city gazetteer,", "no LLM"]),
        ("Business search", ["Overpass first (4-tier", "location fallback); Serper", "only when Overpass", "finds nothing"]),
        ("Identity resolution", ["Evidence, features,", "verification, confidence;", "official website or", "nothing (never invented)"]),
        ("Validate, dedupe, rank", ["Reachable HTML page, no", "directory or social URL;", "duplicates removed;", "deterministic ranking"]),
        ("Lead creation", ["Organization-scoped and", "quota-aware; inline job", "from discovery, durable", "job for direct creation"]),
    ]
    for i, (t, ls) in enumerate(A):
        card(xs(i), ya, i + 1, t, ls)
        if i:
            s.arrow([(xs(i - 1) + BW, ya + BH / 2), (xs(i), ya + BH / 2)], None)

    # ------------------------------------------------ Row B: qualification (right to left)
    yb_zone, yb = 250, 290
    s.zone(20, yb_zone, 1140, 164, "LeadBoost: qualification workflow (LangGraph)", LB)
    B = [
        ("Scrape + normalize", ["6 escalating tiers: static,", "curl_cffi, Playwright,", "multi-page, fallback;", "pure normalization"], LB),
        ("Enrich", ["3-tier waterfall:", "deterministic first, then", "an external-API tier", "(placeholder), LLM last"], LB),
        ("Qualify", ["Company intelligence,", "scoring, decision,", "confidence evaluation;", "AI steps have fallbacks"], LB),
        ("Review gate + draft", ["0.75+ approved; 0.45 to", "0.75 flagged, both draft;", "below 0.45 human review,", "no draft"], WN),
        ("Persist + analytics", ["Lead, scores and status", "stored with per-stage logs;", "a failed node degrades", "to a partial result"], LB),
    ]
    for k, (t, ls, st) in enumerate(B):
        i = 4 - k                     # stage 6 sits at the right edge
        card(xs(i), yb, 6 + k, t, ls, st)
        if k:
            s.arrow([(xs(i + 1), yb + BH / 2), (xs(i) + BW, yb + BH / 2)], None)
    # snake connector A5 -> B6 (right edge)
    cx = xs(4) + BW / 2
    s.arrow([(cx, ya + BH), (cx, yb)], None)

    # ------------------------------------------------ Row C: outreach
    yc_zone, yc = 440, 502
    s.zone(20, yc_zone, 1140, 190, "Outreach: authorization in LeadBoost, execution in the Mailer", EX)
    gaps = [22, 76, 22, 76]
    cxs = [X0]
    for g in gaps:
        cxs.append(cxs[-1] + BW + g)
    # Mailer sub-zone behind stages 13-14
    mx1, mx2 = cxs[2] - 10, cxs[3] + BW + 10
    s.rect(mx1, yc - 34, mx2 - mx1, BH + 46, MA, r=10, dash="6 4", sw=1.6, fill="#F6FEFA")
    s.text(mx1 + 12, yc - 16, "Mailer Agent: separate service, repository and database", size=11.5, weight=700, fill=MA["ink"])
    C = [
        ("Sender setup", ["Account verified (own", "SMTP login test), then", "synced to a Mailer", "mailbox (pending, synced)"], LB),
        ("Authorize + dispatch", ["Action: pending_review,", "approved, dispatching;", "submitted on Mailer 202", "(idempotency key)"], LB),
        ("Generate + send", ["Durable intake (QUEUED);", "worker writes the message", "and sends over SMTP:", "sent, failed or unknown"], MA),
        ("Reply intake", ["Per-mailbox IMAP poll;", "correlate, classify and", "store (no automatic", "follow-up for these leads)"], MA),
        ("Conversation view", ["LeadBoost reads state and", "messages (read-only,", "bounded) and shows a", "product-safe view"], LB),
    ]
    for i, (t, ls, st) in enumerate(C):
        card(cxs[i], yc, 11 + i, t, ls, st)
    ym = yc + BH / 2
    s.arrow([(cxs[0] + BW, ym), (cxs[1], ym)], None)
    s.arrow([(cxs[1] + BW, ym), (cxs[2], ym)], "HTTPS", loff=(0, -14), lw=44)
    s.arrow([(cxs[2] + BW, ym), (cxs[3], ym)], None)
    s.arrow([(cxs[3] + BW, ym), (cxs[4], ym)], "GET, read-only", loff=(0, -14), lw=92, st="dash")
    # snake connector B10 (left edge) -> C11
    cx = xs(0) + BW / 2
    s.arrow([(cx, yb + BH), (cx, yc)], None)

    # ------------------------------------------------ footer
    s.note(20, 644, 1140, 34,
           ["Accepted (Mailer 202), sent (SMTP handoff) and delivered are three different facts. No stage observes delivery to the recipient's inbox."],
           st=WN, size=12)
    s.legend(20, 704, [(LB, "LeadBoost"), (MA, "Mailer"), (WN, "Decision gate")])
    return s.render()
