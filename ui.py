"""Look and feel: UOL QHPC house colours, masthead, highlight cards and styled tables."""
import base64
import html
import os

import pandas as pd
import streamlit as st

DEEP, MID, BRIGHT, TINT, PALE = "#0F3D2E", "#17694E", "#2E9E6B", "#E6F2EC", "#F4FAF7"
INK, MUTED, LINE = "#1C2B25", "#5E6E66", "#D3E6DB"

ORG = "UOL · Qalandars High Performance Centre"
APP = "UOL QHPC Cricket Stats"
STATS_FROM = "28 October 2026"   # shown at the top of every page
LOGO = os.path.join(os.path.dirname(__file__), "assets", "logo.png")  # optional: drop your logo here

CSS = f"""
<style>
.block-container {{ padding-top: 4.2rem; padding-bottom: 1rem; max-width: 1440px; }}
#MainMenu, footer {{ visibility: hidden; }}
@media (max-width: 640px) {{
  .block-container {{ padding-top: 4.4rem; }}
  .qh-title {{ font-size: 1.7rem; }}
  .qh-meta {{ text-align: left; }}
}}

/* masthead */
.qh-mast {{ display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap;
           gap: 12px 24px; border-bottom: 2px solid {DEEP}; padding-bottom: 12px; margin-bottom: 4px; }}
.qh-mast-left {{ display: flex; align-items: center; gap: 16px; }}
.qh-mast img {{ height: 58px; width: auto; }}
.qh-eyebrow {{ color: {BRIGHT}; font-size: .72rem; font-weight: 700; letter-spacing: .2em;
              text-transform: uppercase; }}
.qh-title {{ font-family: "Source Serif Pro", "Source Serif 4", Georgia, serif; color: {DEEP};
            font-size: 2.15rem; font-weight: 700; line-height: 1.15; margin: 3px 0 2px; }}
.qh-sub {{ color: {MUTED}; font-size: .9rem; }}
.qh-meta {{ text-align: right; color: {MUTED}; font-size: .82rem; line-height: 1.55; }}
.qh-meta b {{ color: {DEEP}; font-weight: 700; }}

/* coverage notice */
.qh-scope {{ display: flex; align-items: center; gap: 10px; background: {TINT}; border-left: 4px solid {BRIGHT};
            border-radius: 6px; padding: 9px 14px; margin: 10px 0 6px; color: {DEEP}; font-size: .9rem; }}
.qh-scope b {{ font-weight: 700; }}

/* tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 600; padding: 10px 14px; }}
.stTabs [aria-selected="true"] {{ color: {DEEP}; }}

/* highlight cards */
.qh-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); gap: 12px;
            margin: 6px 0 16px; }}
.qh-card {{ background: {PALE}; border: 1px solid {LINE}; border-left: 4px solid {BRIGHT};
           border-radius: 8px; padding: 11px 14px; min-width: 0; }}
.qh-card .lbl {{ font-size: .68rem; letter-spacing: .14em; text-transform: uppercase; color: {MID};
                font-weight: 700; }}
.qh-card .val {{ font-size: 1.55rem; font-weight: 700; color: {DEEP}; line-height: 1.2; margin-top: 3px; }}
.qh-card .who {{ font-size: .83rem; color: #44544C; white-space: nowrap; overflow: hidden;
                text-overflow: ellipsis; }}

/* section heading with number badge */
.qh-h {{ display: flex; align-items: center; gap: 10px; margin: 18px 0 6px; }}
.qh-h .n {{ background: {DEEP}; color: #fff; border-radius: 4px; font-size: .72rem; font-weight: 700;
           padding: 2px 8px; letter-spacing: .06em; }}
.qh-h .t {{ font-family: "Source Serif Pro", Georgia, serif; font-size: 1.25rem; font-weight: 700;
           color: {DEEP}; }}

/* filter note */
.qh-note {{ display: inline-block; background: {TINT}; color: {MID}; border-radius: 999px;
           font-size: .8rem; font-weight: 600; padding: 3px 12px; margin: 2px 0 8px; }}

/* sidebar brand block */
.qh-brand {{ border: 1px solid rgba(255,255,255,.16); border-radius: 10px; padding: 12px 14px;
            margin: 0 0 6px; background: rgba(255,255,255,.04); }}
.qh-brand .t1 {{ font-size: .62rem; letter-spacing: .2em; text-transform: uppercase; color: #8FD4B0;
                font-weight: 700; }}
.qh-brand .t2 {{ font-family: "Source Serif Pro", Georgia, serif; font-size: 1.4rem; font-weight: 700;
                color: #fff; line-height: 1.2; margin-top: 2px; }}
.qh-brand .t3 {{ font-size: .78rem; color: #BFE3CF; }}

/* profile header */
.qh-chip {{ display: inline-block; background: {TINT}; color: {MID}; border-radius: 999px;
           font-size: .76rem; font-weight: 600; padding: 2px 10px; margin: 0 6px 6px 0; }}

/* top performers */
.qh-top {{ border: 1px solid {LINE}; border-radius: 10px; overflow: hidden; margin-bottom: 18px; background: #fff; }}
.qh-top-h {{ background: {DEEP}; color: #fff; padding: 10px 16px; display: flex; justify-content: space-between;
            align-items: baseline; gap: 10px; }}
.qh-top-h .t {{ font-family: "Source Serif Pro", Georgia, serif; font-size: 1.15rem; font-weight: 700; }}
.qh-top-h .s {{ font-size: .72rem; letter-spacing: .14em; text-transform: uppercase; color: #8FD4B0; font-weight: 700; }}
.qh-hero {{ display: flex; align-items: center; gap: 14px; padding: 16px; background: {TINT};
           border-bottom: 1px solid {LINE}; }}
.qh-hero .rk {{ flex: none; width: 34px; height: 34px; border-radius: 50%; background: {BRIGHT}; color: #fff;
               font-weight: 800; display: flex; align-items: center; justify-content: center; }}
.qh-hero .who {{ flex: 1; min-width: 0; }}
.qh-hero .nm {{ font-size: 1.12rem; font-weight: 700; color: {DEEP}; white-space: nowrap; overflow: hidden;
               text-overflow: ellipsis; }}
.qh-hero .dt {{ font-size: .8rem; color: #4A5A52; }}
.qh-hero .vl {{ flex: none; font-size: 2.3rem; font-weight: 800; color: {DEEP}; line-height: 1; }}
.qh-row {{ display: flex; align-items: center; gap: 12px; padding: 9px 16px; border-bottom: 1px solid #EEF5F1; }}
.qh-row:nth-child(even) {{ background: {PALE}; }}
.qh-row:last-child {{ border-bottom: none; }}
.qh-row .rk {{ flex: none; width: 24px; color: {MID}; font-weight: 700; text-align: center; }}
.qh-row .who {{ flex: 1; min-width: 0; }}
.qh-row .nm {{ font-weight: 600; color: {INK}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.qh-row .dt {{ font-size: .76rem; color: {MUTED}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.qh-row .vl {{ flex: none; font-weight: 700; color: {DEEP}; font-size: 1.05rem; }}
.qh-empty {{ padding: 18px 16px; color: {MUTED}; font-size: .9rem; }}

/* recent form */
.qh-form {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 16px; }}
.qh-form span {{ min-width: 30px; height: 30px; padding: 0 6px; border-radius: 6px; display: flex;
                align-items: center; justify-content: center; font-weight: 800; color: #fff; font-size: .82rem; }}
.qh-form .W {{ background: {BRIGHT}; }}
.qh-form .L {{ background: #C0392B; }}
.qh-form .T, .qh-form .D {{ background: #8E9C95; }}
.qh-form .NR {{ background: #C9D3CE; color: #33413A; }}

/* footer */
.qh-foot {{ border-top: 1px solid {LINE}; margin-top: 32px; padding-top: 10px; color: #7A8A82;
           font-size: .75rem; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 6px; }}
</style>
"""


def esc(x):
    return html.escape(str(x))


def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)
    if os.path.exists(LOGO):
        st.logo(LOGO, size="large")


def _logo_tag():
    if not os.path.exists(LOGO):
        return ""
    data = base64.b64encode(open(LOGO, "rb").read()).decode()
    return f'<img src="data:image/png;base64,{data}" alt="UOL QHPC">'


def masthead(n_matches, n_players, last_date=None):
    meta = f"<b>{n_matches}</b> matches · <b>{n_players}</b> players"
    if last_date is not None and pd.notna(last_date):
        meta += f"<br>Last match {last_date:%d %b %Y}"
    st.markdown(f"""
<div class="qh-mast">
  <div class="qh-mast-left">{_logo_tag()}
    <div>
      <div class="qh-eyebrow">{esc(ORG)}</div>
      <div class="qh-title">{esc(APP)}</div>
      <div class="qh-sub">Match and player performance database · Performance Analysis</div>
    </div>
  </div>
  <div class="qh-meta">{meta}</div>
</div>
<div class="qh-scope">📅 <span>This site carries stats of matches played from <b>{esc(STATS_FROM)}</b> onwards.</span></div>""",
                unsafe_allow_html=True)


def sidebar_brand():
    st.markdown(f"""
<div class="qh-brand">
  <div class="t1">UOL · Qalandars HPC</div>
  <div class="t2">UOL QHPC</div>
  <div class="t3">Cricket Stats</div>
</div>""", unsafe_allow_html=True)


def cards(items):
    """items: list of (label, value, who)."""
    body = "".join(
        f'<div class="qh-card"><div class="lbl">{esc(l)}</div><div class="val">{esc(v)}</div>'
        f'<div class="who">{esc(w) if w else "&nbsp;"}</div></div>'
        for l, v, w in items)
    st.markdown(f'<div class="qh-cards">{body}</div>', unsafe_allow_html=True)


def section(num, title):
    st.markdown(f'<div class="qh-h"><span class="n">{esc(num)}</span><span class="t">{esc(title)}</span></div>',
                unsafe_allow_html=True)


def note(text):
    st.markdown(f'<span class="qh-note">{esc(text)}</span>', unsafe_allow_html=True)


def chips(values):
    if values:
        st.markdown("".join(f'<span class="qh-chip">{esc(v)}</span>' for v in values), unsafe_allow_html=True)


def footer(updated=None):
    right = f"Matches from {STATS_FROM} onwards · Data: STUMPS scorecards"
    if updated is not None and pd.notna(updated):
        right += f" · Updated {updated:%d %b %Y}"
    st.markdown(f'<div class="qh-foot"><span>{esc(ORG)} · Performance Analysis</span>'
                f'<span>{esc(right)}</span></div>', unsafe_allow_html=True)


def top_list(title, subtitle, rows):
    """A ranked card: #1 large, the rest as a list. rows: list of (name, value, detail)."""
    if not rows:
        body = '<div class="qh-empty">No data in the current filters.</div>'
    else:
        n, v, d = rows[0]
        body = (f'<div class="qh-hero"><div class="rk">1</div><div class="who"><div class="nm">{esc(n)}</div>'
                f'<div class="dt">{esc(d)}</div></div><div class="vl">{esc(v)}</div></div>')
        body += "".join(
            f'<div class="qh-row"><span class="rk">{i}</span><span class="who"><div class="nm">{esc(n)}</div>'
            f'<div class="dt">{esc(d)}</div></span><span class="vl">{esc(v)}</span></div>'
            for i, (n, v, d) in enumerate(rows[1:], start=2))
    st.markdown(f'<div class="qh-top"><div class="qh-top-h"><span class="t">{esc(title)}</span>'
                f'<span class="s">{esc(subtitle)}</span></div>{body}</div>', unsafe_allow_html=True)


FORM_CODE = {"Won": "W", "Lost": "L", "Tied": "T", "Drawn": "D", "No result": "NR"}
RESULT_COLOURS = {"Won": f"background-color: #DDF1E6; color: {DEEP}",
                  "Lost": "background-color: #FBE3E0; color: #8E2A20",
                  "Tied": "background-color: #EEF1EF", "Drawn": "background-color: #EEF1EF",
                  "No result": "background-color: #EEF1EF"}


def form_strip(results):
    """results: list of (outcome, tooltip), oldest first."""
    chips_html = "".join(
        f'<span class="{FORM_CODE.get(o, "NR")}" title="{esc(t)}">{FORM_CODE.get(o, "?")}</span>'
        for o, t in results)
    st.markdown(f'<div class="qh-form">{chips_html}</div>', unsafe_allow_html=True)


DECIMALS = ("Avg", "SR", "Econ", "Bat avg", "Bowl avg", "Strike rate", "Economy")


def styled(df, top=0, result_col=None):
    """Table with 2-decimal stats, '—' for blanks and the top `top` rows tinted.
    Blank numbers are stored as -inf (shown as '—') so the column still sorts as numbers,
    with blanks last when sorting high to low."""
    df = df.reset_index(drop=True).copy()
    fmt = {}
    for c in df.columns:
        if pd.api.types.is_bool_dtype(df[c]):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            if df[c].isna().any():
                df[c] = df[c].astype(float).fillna(float("-inf"))
            dec = c in DECIMALS
            fmt[c] = (lambda v, dec=dec: "—" if v == float("-inf")
                      else (f"{v:.2f}" if dec else (f"{int(v)}" if float(v).is_integer() else f"{v:g}")))
        else:
            df[c] = df[c].where(df[c].notna(), "—")
    sty = df.style.format(fmt)
    if result_col and result_col in df.columns:
        sty = sty.map(lambda v: RESULT_COLOURS.get(v, ""), subset=[result_col])
    if top:
        sty = sty.apply(lambda r: [f"background-color: {TINT}; color: {DEEP}"] * len(r)
                        if r.name < top else [""] * len(r), axis=1)
    return sty



# ---------------- shareable player card (PNG) ----------------
FONT_DIRS = [os.path.join(os.path.dirname(__file__), "assets", "fonts"),
             "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/liberation2",
             "/usr/share/fonts/truetype/dejavu"]
FONT_FILES = {
    "serif": ["LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf"],
    "bold": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "regular": ["LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
}


def _font(kind, size):
    from PIL import ImageFont
    for name in FONT_FILES[kind]:
        for d in FONT_DIRS:
            path = os.path.join(d, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:          # very old Pillow
        return ImageFont.load_default()


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _spaced(draw, xy, text, font, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def _fit(draw, text, kind, size, max_w, min_size=40):
    f = _font(kind, size)
    while draw.textlength(text, font=f) > max_w and size > min_size:
        size -= 4
        f = _font(kind, size)
    return f


def share_card_png(card):
    """card: dict(name, line, mvp, sections=[(title, [(label, value) × 6]) × 2], extra, scope,
    footer_right). Returns PNG bytes, 1080×1350 (fits WhatsApp and Instagram)."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    W, H, P = 1080, 1350, 72
    deep, mid, bright, tint, pale = map(_hex, (DEEP, MID, BRIGHT, TINT, PALE))
    mint, white, ink, muted = _hex("#8FD4B0"), (255, 255, 255), _hex(INK), _hex(MUTED)
    img = Image.new("RGB", (W, H), white)
    d = ImageDraw.Draw(img)

    # header band
    band = 400
    d.rectangle([0, 0, W, band], fill=deep)
    d.rectangle([0, 0, W, 14], fill=bright)
    if os.path.exists(LOGO):
        try:
            logo = Image.open(LOGO).convert("RGBA")
            logo.thumbnail((120, 120))
            img.paste(logo, (W - P - logo.width, 52), logo)
        except Exception:
            pass
    _spaced(d, (P, 56), "UOL · QALANDARS HIGH PERFORMANCE CENTRE", _font("bold", 22), mint, 3.2)
    d.text((P, 92), "PLAYER CARD", font=_font("bold", 22), fill=(190, 227, 207))
    name_font = _fit(d, card["name"], "serif", 96, W - 2 * P)
    d.text((P, 128), card["name"], font=name_font, fill=white)
    line_font = _fit(d, card.get("line", ""), "regular", 30, W - 2 * P, 20)
    d.text((P, 258), card.get("line", ""), font=line_font, fill=(205, 233, 217))
    if card.get("mvp"):
        f = _font("bold", 30)
        w = d.textlength(card["mvp"], font=f) + 44
        d.rounded_rectangle([P, 314, P + w, 370], radius=28, fill=bright)
        d.text((P + 22, 324), card["mvp"], font=f, fill=white)

    # stat sections
    def section(y, title, stats):
        _spaced(d, (P, y), title, _font("bold", 26), mid, 3)
        d.rectangle([P, y + 40, W - P, y + 43], fill=bright)
        cols, gap = 3, 18
        tw = (W - 2 * P - gap * (cols - 1)) / cols
        th = 122
        for i, (lbl, val) in enumerate(stats):
            r, c = divmod(i, cols)
            x0, y0 = P + c * (tw + gap), y + 66 + r * (th + gap)
            d.rounded_rectangle([x0, y0, x0 + tw, y0 + th], radius=14, fill=pale, outline=tint, width=2)
            d.rectangle([x0, y0 + 12, x0 + 6, y0 + th - 12], fill=bright)
            _spaced(d, (x0 + 24, y0 + 18), lbl.upper(), _font("bold", 20), mid, 2)
            vf = _fit(d, str(val), "bold", 56, tw - 40, 28)
            d.text((x0 + 24, y0 + 48), str(val), font=vf, fill=deep)
        rows = (len(stats) + cols - 1) // cols
        return y + 62 + rows * (th + gap)

    (t1, s1), (t2, s2) = card["sections"]
    y = section(band + 36, t1, s1)
    y = section(y + 8, t2, s2)
    if card.get("extra"):
        ef = _fit(d, card["extra"], "regular", 28, W - 2 * P, 18)
        d.text((P, y + 6), card["extra"], font=ef, fill=muted)

    # footer
    d.rectangle([0, H - 112, W, H], fill=deep)
    d.text((P, H - 86), "UOL QHPC Cricket Stats", font=_font("serif", 34), fill=white)
    d.text((P, H - 44), card.get("scope", ""), font=_font("regular", 22), fill=mint)
    fr = card.get("footer_right", "")
    f = _font("regular", 24)
    d.text((W - P - d.textlength(fr, font=f), H - 80), fr, font=f, fill=(205, 233, 217))
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
