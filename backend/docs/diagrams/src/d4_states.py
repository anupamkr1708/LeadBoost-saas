from svglib import *

def node(s, x, y, w, name, st, terminal=False, dash=None, sub=None, h=46):
    s.rect(x, y, w, h, st, r=10, sw=3 if terminal else 1.6, dash=dash, fill="#FFFFFF")
    s.text(x + w / 2, y + (20 if sub else 28), name, size=13, weight=700, fill=st["ink"], anchor="middle")
    if sub:
        s.text(x + w / 2, y + 36, sub, size=10.5, fill=MUTED, anchor="middle")

def build():
    s = Svg(1100, 790, "LeadBoost outreach action state machine and Mailer dispatch state machine",
            "Two independently owned state machines drawn separately. LeadBoost owns authorization and handoff: pending_review, "
            "approved, dispatching, submitted, dispatch_failed, cancelled. The Mailer owns dispatch and delivery: queued, "
            "generating (internal), sending, sent, failed, unknown. LeadBoost reads the Mailer state read-only and never writes it back.")
    s.text(10, 26, "Two state machines, two owners", size=16, weight=700)
    s.text(10, 44, "Never one combined lifecycle. Heavy outline = terminal. Dashed outline = machine-only or internal.", size=12, fill=MUTED)

    # ---------------- LeadBoost panel
    s.zone(10, 60, 1080, 270, "LeadBoost: OutreachAction (authorization and handoff)", LB, "owner: LeadBoost")
    node(s, 40, 110, 150, "pending_review", LB)
    node(s, 260, 110, 130, "approved", LB)
    node(s, 470, 110, 150, "dispatching", LB, dash="5 3", sub="machine-only claim")
    node(s, 700, 110, 130, "submitted", LB, terminal=True)
    node(s, 470, 240, 150, "dispatch_failed", ER, sub="retry-eligible")
    node(s, 200, 240, 150, "cancelled", LB, terminal=True)

    s.arrow([(190, 133), (260, 133)], "approve", loff=(0, -13), lw=54)
    s.arrow([(390, 133), (470, 133)], "dispatch", loff=(0, -13), lw=56)
    s.arrow([(620, 133), (700, 133)], "Mailer 202", loff=(0, -13), lw=68)
    s.arrow([(545, 156), (545, 240)], "not accepted", st="err", loff=(-52, 0), lw=84)
    s.arrow([(585, 240), (585, 156)], "retry, same key", loff=(54, 0), lw=100)
    s.arrow([(115, 156), (115, 263), (200, 263)], "cancel", loff=(-27, 0), lw=48)
    s.arrow([(325, 156), (325, 240)], "cancel", loff=(28, 0), lw=48)
    s.arrow([(470, 263), (350, 263)], "cancel", loff=(0, -13), lw=48)

    s.note(870, 96, 210, 228,
           ["Terminal: submitted, cancelled.", "", "dispatching is not cancellable.", "A crash between the claim and", "the Mailer call can strand it;", "there is no lease reclaim.", "", "Not-accepted reasons are safe", "codes: timeout, unreachable,", "rejected, conflict (409),", "invalid reply, not configured,", "sender not synced."],
           st=LB, title="Rules", size=11)
    s.text(40, 318, "submitted = the Mailer durably accepted the request. It does not mean generated, sent or delivered.", size=11.5, weight=700, fill=LB["ink"])

    # ---------------- observation bridge
    s.rect(180, 340, 740, 40, WN, r=8, dash="5 3", sw=1.6)
    s.text(550, 358, "Read-only observation: LeadBoost shows the Mailer's state next to its own.", size=12, weight=700, fill=WN["ink"], anchor="middle")
    s.text(550, 373, "If LeadBoost says dispatch_failed and the Mailer says sent, both are shown. Nothing is written back or healed.", size=11, fill=INK, anchor="middle")
    s.arrow([(550, 330), (550, 340)], None, st="warn")
    s.arrow([(550, 380), (550, 392)], None, st="warn")

    # ---------------- Mailer panel
    s.zone(10, 392, 1080, 388, "Mailer: ExternalDispatch (generation, delivery, authoritative state)", MA, "owner: Mailer")
    ry = 470
    node(s, 40, ry, 120, "queued", MA)
    node(s, 280, ry, 150, "generating", MA, dash="5 3", sub="internal; reads as queued")
    node(s, 540, ry, 130, "sending", MA)
    node(s, 790, ry, 120, "sent", MA, terminal=True)
    node(s, 790, ry + 90, 120, "unknown", WN, terminal=True)
    node(s, 540, ry + 150, 130, "failed", ER, terminal=True)

    s.arrow([(160, ry + 12), (280, ry + 12)], "claim to generate", loff=(0, -13), lw=104)
    s.arrow([(280, ry + 36), (160, ry + 36)], "message saved", loff=(0, 14), lw=88)
    s.arrow([(100, ry), (100, ry - 24), (605, ry - 24), (605, ry)], "send claim (message exists)", loff=(0, 0), lw=162)
    s.arrow([(670, ry + 23), (790, ry + 23)], "SMTP accepted", loff=(0, -13), lw=88)
    s.arrow([(670, ry + 40), (730, ry + 40), (730, ry + 113), (790, ry + 113)], "ambiguous outcome", loff=(-62, 22), lw=116, st="warn")
    s.arrow([(605, ry + 46), (605, ry + 150)], "definite failure", loff=(-54, 0), lw=96, st="err")
    s.arrow([(355, ry + 46), (355, ry + 173), (540, ry + 173)], "LLM error / draft blocked", loff=(-74, 0), lw=150, st="err")
    s.text(540, ry + 214, "Send-gate refusals: failed.", size=10.5, fill=MUTED)

    s.note(720, 640, 360, 130,
           ["Public vocabulary: queued, sending, sent, failed, unknown.", "",
            "unknown = unconfirmed, not failed; never auto-resent.", "An expired SENDING lease resolves to unknown, not queued.",
            "sent = SMTP handoff succeeded, not inbox delivery.", "failed is terminal here; a retry is a new operation.", "An expired generating lease safely returns to queued."],
           st=MA, title="Rules", size=11)
    return s.render()
