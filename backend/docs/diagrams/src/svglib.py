"""Tiny SVG helper shared by the LeadBoost diagram generators.

Keeps every diagram on one visual vocabulary: same palette, type scale,
box/zone/arrow conventions. Output is plain, self-contained SVG (no scripts,
no external fonts, images or stylesheets).
"""
from xml.sax.saxutils import escape

FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

# Ownership palette (fill, stroke, strong text). Contrast checked >= 7:1 on the fill.
LB = dict(fill="#EFF6FF", stroke="#2563EB", ink="#1E3A8A", tint="#DBEAFE")      # LeadBoost
MA = dict(fill="#ECFDF5", stroke="#059669", ink="#065F46", tint="#D1FAE5")      # Mailer
EX = dict(fill="#F3F4F6", stroke="#6B7280", ink="#1F2937", tint="#E5E7EB")      # external / neutral
WN = dict(fill="#FFFBEB", stroke="#B45309", ink="#78350F", tint="#FDE68A")      # caution / unknown
ER = dict(fill="#FEF2F2", stroke="#B91C1C", ink="#7F1D1D", tint="#FECACA")      # failure
INK, MUTED, LINE = "#111827", "#4B5563", "#374151"


_SAFE = {"\u2026": "...", "\u2192": "->", "\u21c4": "<->", "\u2260": "!=", "\u2264": "<=", "\u2265": ">="}


def _safe(s):
    """Replace symbols that need font coverage with plain ASCII so every viewer renders them."""
    for k, v in _SAFE.items():
        s = s.replace(k, v)
    return s


class Svg:
    def __init__(self, w, h, title, desc):
        self.w, self.h = w, h
        self.title, self.desc = title, desc
        self.body = []

    # ---- primitives -------------------------------------------------
    def add(self, s):
        self.body.append(s)

    def text(self, x, y, s, size=13, weight=400, fill=INK, anchor="start", italic=False):
        style = ' font-style="italic"' if italic else ""
        self.add(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{style}>{escape(_safe(s))}</text>'
        )

    def lines(self, x, y, lines, size=12, fill=MUTED, anchor="start", gap=None, weight=400):
        gap = gap or size + 4
        for i, ln in enumerate(lines):
            self.text(x, y + i * gap, ln, size=size, fill=fill, anchor=anchor, weight=weight)

    def rect(self, x, y, w, h, st, r=8, dash=None, sw=1.5, fill=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill or st["fill"]}" stroke="{st["stroke"]}" stroke-width="{sw}"{d}/>'
        )

    # ---- composites -------------------------------------------------
    def zone(self, x, y, w, h, label, st, sub=None):
        """Large ownership container with a header strip."""
        self.rect(x, y, w, h, st, r=12, sw=2, fill="#FFFFFF")
        self.add(
            f'<path d="M{x},{y+12} a12,12 0 0 1 12,-12 h{w-24} a12,12 0 0 1 12,12 v{26-12} h-{w} z" '
            f'fill="{st["tint"]}" stroke="none"/>'
        )
        self.add(f'<line x1="{x}" y1="{y+26}" x2="{x+w}" y2="{y+26}" stroke="{st["stroke"]}" stroke-width="1"/>')
        self.text(x + 12, y + 18, label, size=13, weight=700, fill=st["ink"])
        if sub:
            self.text(x + w - 12, y + 18, sub, size=11.5, fill=st["ink"], anchor="end")

    def box(self, x, y, w, h, title, st, sub=(), r=8, dash=None, title_size=13, sub_size=11.5):
        self.rect(x, y, w, h, st, r=r, dash=dash)
        ty = y + 20
        self.text(x + w / 2, ty, title, size=title_size, weight=700, fill=st["ink"], anchor="middle")
        if sub:
            self.lines(x + w / 2, ty + 17, list(sub), size=sub_size, fill=INK, anchor="middle", gap=sub_size + 3.5)

    def db(self, x, y, w, h, title, st, sub=()):
        ry = 8
        self.add(
            f'<path d="M{x},{y+ry} v{h-2*ry} a{w/2},{ry} 0 0 0 {w},0 v-{h-2*ry} a{w/2},{ry} 0 0 0 -{w},0 z" '
            f'fill="{st["fill"]}" stroke="{st["stroke"]}" stroke-width="1.5"/>'
        )
        self.add(
            f'<ellipse cx="{x+w/2}" cy="{y+ry}" rx="{w/2}" ry="{ry}" fill="{st["tint"]}" '
            f'stroke="{st["stroke"]}" stroke-width="1.5"/>'
        )
        self.text(x + w / 2, y + ry + 22, title, size=13, weight=700, fill=st["ink"], anchor="middle")
        if sub:
            self.lines(x + w / 2, y + ry + 38, list(sub), size=11.5, fill=INK, anchor="middle", gap=15)

    def arrow(self, pts, label=None, st="solid", lpos=0.5, lside=None, both=False, loff=(0, 0), lw=None):
        """Orthogonal polyline arrow. st: solid | dash | warn | err."""
        color = {"solid": LINE, "dash": LINE, "warn": WN["stroke"], "err": ER["stroke"]}[st]
        dash = ' stroke-dasharray="6 4"' if st in ("dash", "warn") else ""
        mk = {"solid": "a", "dash": "a", "warn": "w", "err": "e"}[st]
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        ms = f' marker-start="url(#{mk}s)"' if both else ""
        self.add(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash} '
            f'marker-end="url(#{mk})"{ms}/>'
        )
        if label:
            # label on the longest segment
            segs = list(zip(pts, pts[1:]))
            (x1, y1), (x2, y2) = max(segs, key=lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]))
            lx = x1 + (x2 - x1) * lpos + loff[0]
            ly = y1 + (y2 - y1) * lpos + loff[1]
            self.label(lx, ly, label, color=color if st in ("warn", "err") else INK, w=lw)

    def label(self, cx, cy, s, size=11.5, color=INK, w=None, bold=False):
        w = w or (len(s) * size * 0.56 + 10)
        self.add(f'<rect x="{cx-w/2:.1f}" y="{cy-9:.1f}" width="{w:.1f}" height="18" rx="4" fill="#FFFFFF" fill-opacity="0.94"/>')
        self.text(round(cx, 1), round(cy + 4, 1), s, size=size, fill=color, anchor="middle", weight=700 if bold else 500)

    def badge(self, cx, cy, n, st=None):
        st = st or LB
        self.add(f'<circle cx="{cx}" cy="{cy}" r="10" fill="{st["stroke"]}"/>')
        self.text(cx, cy + 4, str(n), size=11.5, weight=700, fill="#FFFFFF", anchor="middle")

    def note(self, x, y, w, h, lines_, st=WN, title=None, size=11.5):
        self.rect(x, y, w, h, st, r=6, dash="5 3", sw=1.4)
        yy = y + 18
        if title:
            self.text(x + 10, yy, title, size=12, weight=700, fill=st["ink"])
            yy += 17
        self.lines(x + 10, yy, lines_, size=size, fill=INK, gap=size + 4)

    # ---- output -----------------------------------------------------
    def render(self):
        defs = ""
        for mid, col in (("a", LINE), ("w", WN["stroke"]), ("e", ER["stroke"])):
            defs += (
                f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{col}"/></marker>'
                f'<marker id="{mid}s" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{col}"/></marker>'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" '
            f'role="img" aria-labelledby="t d" font-family="{FONT}">\n'
            f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>\n'
            f"<defs>{defs}</defs>\n"
            f'<rect width="{self.w}" height="{self.h}" fill="#FFFFFF"/>\n'
            + "\n".join(self.body)
            + "\n</svg>\n"
        )

    def legend(self, x, y, items):
        """items: list of (style, text) swatches."""
        cx = x
        for st, txt in items:
            self.rect(cx, y - 10, 16, 12, st, r=3, sw=1.4)
            self.text(cx + 22, y, txt, size=11.5, fill=MUTED)
            cx += 22 + len(txt) * 6.4 + 22
