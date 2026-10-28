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
LOGO = os.path.join(os.path.dirname(__file__), "assets", "logo.png")  # optional: drop your logo here

CSS = f"""
<style>
.block-container {{ padding-top: 2.2rem; padding-bottom: 1rem; max-width: 1440px; }}
#MainMenu, footer {{ visibility: hidden; }}
@media (max-width: 640px) {{
  .block-container {{ padding-top: 3.4rem; }}
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
</div>""", unsafe_allow_html=True)


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
    right = "Data: STUMPS scorecards"
    if updated is not None and pd.notna(updated):
        right += f" · Updated {updated:%d %b %Y}"
    st.markdown(f'<div class="qh-foot"><span>{esc(ORG)} · Performance Analysis</span>'
                f'<span>{esc(right)}</span></div>', unsafe_allow_html=True)


DECIMALS = ("Avg", "SR", "Econ", "Bat avg", "Bowl avg", "Strike rate", "Economy")


def styled(df, top=0):
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
    if top:
        sty = sty.apply(lambda r: [f"background-color: {TINT}; color: {DEEP}"] * len(r)
                        if r.name < top else [""] * len(r), axis=1)
    return sty
