import io
import re
import time
import hmac
import hashlib
import datetime as dt
from itertools import combinations

import numpy as np
import pandas as pd
import streamlit as st

import db
import ui
from parser import parse_stumps_pdf, parse_summary_image
from stats import (NO_FORMAT, NOT_SET, BALLS, VENUES, OUTCOMES, INTRA, FORMAT_CHOICES, guess_ball,
                   TOSSES, INNINGS, guess_toss, guess_innings, POINTS_RULES, match_points,
                   players_of_match, mvp_table, dismissal_breakdown, HOW_OUT_LABELS,
                   resolve_name, clean_scorecard_name, GUEST,
                   overs_from_scorecard,
                   guess_outcome, closest_name, name_clusters, team_record, record_table,
                   guess_type, career_batting, career_bowling, batting_by_position, player_overview,
                   enrich, filter_rows, suggest_name_groups, search_names, team_matches, default_side,
                   team_key, duplicate_evidence, match_proof, batting_proof, find_duplicate_matches,
                   match_source, DUP_LABELS)

st.set_page_config(page_title="UOL QHPC Cricket Stats", page_icon="🏏", layout="wide")
ui.apply_theme()

def _secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


if "our_team" not in st.session_state:
    st.session_state.our_team = _secret("team_names", "UOL QHPC")

# ---------------- admin password ----------------
# Students only see Leaderboards and Player search. Adding matches, Matches and Manage
# need the password set as `admin_password` in the app's secrets.
ADMIN_PASSWORD = _secret("admin_password")


def _check_password():
    if st.session_state.get("is_admin"):
        return
    typed = st.session_state.get("admin_pw", "")
    if ADMIN_PASSWORD and hmac.compare_digest(str(typed), str(ADMIN_PASSWORD)):
        st.session_state.is_admin = True
        st.session_state.pop("login_error", None)
    else:
        st.session_state["login_error"] = True
    st.session_state["admin_pw"] = ""


def _logout():
    st.session_state.is_admin = False


# On your own computer (no Google Sheets, no password set) everything stays open.
if not ADMIN_PASSWORD and not db.use_sheets():
    st.session_state.is_admin = True
IS_ADMIN = bool(st.session_state.get("is_admin"))


# ---------------- phone or computer? ----------------
def _is_mobile():
    try:
        ua = st.context.headers.get("User-Agent", "") or ""
    except Exception:
        ua = ""
    return bool(re.search(r"Mobi|Android|iPhone|iPod", ua))


IS_MOBILE = _is_mobile()


# ---------------- helpers ----------------
def flash(key, balloons=False):
    """Show a success message saved before the last rerun."""
    msg = st.session_state.pop(key, None)
    if msg:
        st.success(msg)
        if balloons:
            st.balloons()


def short_hash(text):
    return hashlib.md5(text.encode()).hexdigest()[:10]


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fmt_date(series):
    return series.dt.strftime("%d %b %Y").fillna("—")


def dash(v):
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA else v


@st.cache_data(show_spinner=False)
def prepared(m_df, bat_df, bowl_df, field_df):
    return enrich(m_df, bat_df, bowl_df, field_df)


@st.cache_data(show_spinner=False)
def name_groups(names, ignored, threshold):
    return suggest_name_groups(list(names), threshold, ignore=ignored)


@st.cache_data(show_spinner=False)
def dup_pairs(matches, bat_all, ignored):
    return find_duplicate_matches(matches, bat_all, ignore=ignored)


def align_rows(rows, other):
    """Rows of a parsed file limited to the team(s) that appear in `other`, so a file
    (both teams) can be compared fairly with a saved match (your team only)."""
    df, o = pd.DataFrame(rows), pd.DataFrame(other)
    if df.empty or o.empty or "team" not in o or "team" not in df:
        return df
    teams = {team_key(t) for t in o["team"].astype(str)}
    keep = df["team"].astype(str).map(team_key).isin(teams)
    return df[keep] if keep.any() else df


def show_proof(a, b, label_a, label_b, bat_a, bat_b, reasons, bowl_a=None, bowl_b=None):
    st.markdown("\n".join(f"- {r}" for r in reasons))
    st.dataframe(match_proof(a, b, label_a, label_b, bat_a, bat_b, bowl_a, bowl_b),
                 hide_index=True, width="stretch")
    bp = batting_proof(bat_a, bat_b, label_a, label_b)
    if not bp.empty:
        st.markdown("**Batter by batter** — runs (balls)")
        st.dataframe(bp, hide_index=True, width="stretch")


TYPE_OTHER = "✏️ Type another name…"
FMT_OTHER = "Other…"


def match_details(fk, mt, side, labels, known_opps, known_formats):
    """Opponent / venue / result, then format / overs / ball for one uploaded match.
    Returns a dict; `missing` lists what still has to be picked."""
    d = {"opponent": "", "venue": "", "outcome": "", "toss_result": "", "innings": ""}
    normal = side in ("A", "B")
    if normal:
        cols = st.columns(4)
        file_opp = labels["B" if side == "A" else "A"]
        suggested = closest_name(file_opp, known_opps)
        opts = list(dict.fromkeys([x for x in (suggested, file_opp) if x] + known_opps)) + [TYPE_OTHER]
        pick = cols[0].selectbox("Opponent", opts, key=f"opp_{fk}_{side}",
                                 help="Use the same name every time so the head-to-head record stays together.")
        if pick == TYPE_OTHER:
            pick = cols[0].text_input("Opponent name", key=f"oppt_{fk}", placeholder="Type the name",
                                      label_visibility="collapsed").strip()
        elif suggested and suggested != file_opp and pick == suggested:
            cols[0].caption(f"File says “{file_opp}”")
        d["opponent"] = pick
        d["venue"] = cols[1].radio("Venue", list(VENUES), index=None, horizontal=True, key=f"venue_{fk}")
        g = guess_outcome(mt.get("result"), mt["team_a"], mt["team_b"], side)
        d["outcome"] = cols[2].selectbox("Result for us", list(OUTCOMES), key=f"out_{fk}_{side}",
                                         index=OUTCOMES.index(g) if g else None, placeholder="Pick…")
        if g and d["outcome"] == g:
            cols[2].caption("From the scorecard")
        gt = guess_toss(mt.get("toss"), mt["team_a"], mt["team_b"], side)
        d["toss_result"] = cols[3].radio("Toss", list(TOSSES), horizontal=True, key=f"toss_{fk}_{side}",
                                         index=TOSSES.index(gt) if gt else None,
                                         format_func=lambda t: f"{t} toss")
        if gt and d["toss_result"] == gt:
            cols[3].caption("From the scorecard")

    cols = st.columns(4 if normal else 3)
    file_fmt = (mt.get("format") or "").strip()
    fmt_opts = list(dict.fromkeys(FORMAT_CHOICES + known_formats)) + [FMT_OTHER]
    fmt = cols[0].selectbox("Match format", fmt_opts, index=None, key=f"fmt_{fk}", placeholder="Pick…")
    if fmt == FMT_OTHER:
        fmt = cols[0].text_input("Format", key=f"fmtt_{fk}", placeholder="Type your own format",
                                 label_visibility="collapsed").strip()
    elif not fmt and file_fmt:
        cols[0].caption(f"Scorecard says “{file_fmt}”")
    d["format"] = fmt or ""
    sc_overs = overs_from_scorecard(mt)
    overs = cols[1].number_input("Overs per side", min_value=1, max_value=200, step=1, value=sc_overs,
                                 key=f"ov_{fk}", placeholder="e.g. 20")
    if sc_overs and overs == sc_overs:
        cols[1].caption("From the scorecard")
    d["overs"] = str(int(overs)) if overs else ""
    guess = guess_ball(file_fmt, d["overs"]) or guess_ball(d["format"], d["overs"])
    d["ball"] = cols[2].selectbox("Ball", list(BALLS), key=f"ball_{fk}_{short_hash(d['format'] + d['overs'])}",
                                  index=BALLS.index(guess) if guess else None, placeholder="Pick…")
    if normal:
        gi = guess_innings(mt.get("result"), mt["team_a"], mt["team_b"], side, mt.get("bat_first", ""))
        d["innings"] = cols[3].radio("Our innings", list(INNINGS), horizontal=True, key=f"inn_{fk}_{side}",
                                     index=INNINGS.index(gi) if gi else None)
        if gi and d["innings"] == gi:
            cols[3].caption("From the scorecard")
    need = (["opponent", "venue", "outcome", "toss_result", "innings"] if normal else []) + ["format", "overs", "ball"]
    d["missing"] = [k for k in need if not d.get(k)]
    return d


NEW_PLAYER = "✏️ New player — type their full name…"


def pick_player(raw, opts, idx, key, col):
    """A 'who is this?' picker. Returns (kind, full name): kind is 'squad', 'new' (typed — to be
    added to the squad tab), 'guest', or None while nothing is picked."""
    opts = list(dict.fromkeys(opts)) + [NEW_PLAYER, GUEST]
    pick = col.selectbox(f"Who is {raw}?", opts, index=idx, key=key, placeholder="Pick the player…",
                         label_visibility="collapsed")
    if pick == NEW_PLAYER:
        typed = col.text_input(f"Full name for {raw}", value=clean_scorecard_name(raw), key=f"{key}_new",
                               placeholder="Type the full name", label_visibility="collapsed",
                               help="Saved with this name and added to your squad list.")
        typed = " ".join(typed.split())
        if not typed:
            return None, None
        col.caption("Will be added to your squad list")
        return "new", typed
    if pick == GUEST:
        return "guest", clean_scorecard_name(raw)
    return ("squad", pick) if pick else (None, None)


def _who_label(raw, roles):
    return f"**{clean_scorecard_name(raw)}** · " + " · ".join(sorted(roles))


def name_review(to_save):
    """Match every one of our players' scorecard names to the squad list before saving.
    Returns (names {scorecard: full}, remember {scorecard: (full, in squad)}, unanswered names,
    new full names to add to the squad tab)."""
    roles = {}
    for p, sides in to_save:
        _m, b, w, f = db.rows_for(p, sides)
        for kind, rows in (("batting", b), ("bowling", w), ("fielding", f)):
            for r in rows:
                roles.setdefault(r["player"], set()).add(kind)
    if not roles:
        return {}, {}, [], []
    squad = db.squad_names()
    with st.container(border=True):
        h1, h2 = st.columns([4, 1], vertical_alignment="center")
        h1.markdown(f"**Player names** — matched to your squad list ({len(squad)} players)")
        if h2.button("🔄  Refresh", key="squad_refresh", help="Re-read the squad tab now (e.g. after adding "
                                                              "a new admission)."):
            db.clear_cache()
            st.rerun()
        if not squad:
            st.caption("Your squad list is empty, so names are saved as the scorer wrote them. Add full names "
                       "to the **squad** tab of your Google Sheet (column **Full name**) and press Refresh.")
            return {}, {}, [], []
        sp = db.spellings()
        res = {n: resolve_name(n, squad, sp) for n in roles}
        ask = [n for n in roles if res[n]["status"] == "ask"]
        auto = [n for n in roles if res[n]["status"] != "ask"]
        sig = short_hash("|".join(squad))
        names, remember, new_squad = {}, {}, []
        known = {x.lower(): x for x in squad}
        if ask:
            st.markdown(f"**Who is this?** — {len(ask)} name(s) need you")
            for n in ask:
                r = res[n]
                opts = list(dict.fromkeys(r["options"] + squad))
                idx = opts.index(r["default"]) if r.get("default") in opts else None
                c1, c2 = st.columns([2, 3], vertical_alignment="center")
                c1.markdown(_who_label(n, roles[n]))
                c1.caption(r["reason"] + (" · last time you picked this" if idx is not None else ""))
                kind, full = pick_player(n, opts, idx, f"who_{short_hash(n)}_{sig}", c2)
                if kind == "guest":
                    names[n] = full
                    remember[n] = (full, False)
                elif kind in ("squad", "new"):
                    if kind == "new":
                        if full.lower() in known:
                            full = known[full.lower()]
                        elif full not in new_squad:
                            new_squad.append(full)
                    names[n] = full
                    remember[n] = (full, True)
        if auto:
            with st.expander(f"✅  {len(auto)} name(s) matched automatically — tap to check or change"):
                df = pd.DataFrame({"Scorecard name": [clean_scorecard_name(n) for n in auto],
                                   "Full name": [res[n]["full"] for n in auto],
                                   "Why": [res[n]["reason"] for n in auto]})
                opts = list(dict.fromkeys(squad + [f for f in df["Full name"] if f not in squad])) + [GUEST]
                ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Scorecard name", "Why"],
                                    key=f"auto_names_{sig}_{short_hash('|'.join(auto))}",
                                    column_config={"Full name": st.column_config.SelectboxColumn(
                                        "Full name", options=opts, required=True)})
                for n, full in zip(auto, ed["Full name"]):
                    if full == GUEST:
                        names[n] = clean_scorecard_name(n)
                        remember[n] = (names[n], False)
                        continue
                    names[n] = full
                    if full != res[n]["full"] or res[n]["reason"] == "now in the squad list":
                        remember[n] = (full, True)     # you corrected it, or a guest has joined the squad
        missing = [n for n in ask if n not in names]
        if missing:
            st.warning(f"Pick who {len(missing)} name(s) are: " + ", ".join(clean_scorecard_name(n) for n in missing)
                       + ". Not in the list? Choose “New player” to type their full name, or “Not in squad "
                         "list” for a guest or trialist.")
    return names, remember, missing, new_squad


def name_details(bat_all, bowl_all):
    """name -> (entries, short description) for the merge screens."""
    bat_n = bat_all["player"].value_counts()
    bowl_n = bowl_all["player"].value_counts()
    rows = pd.concat([bat_all[["player", "team", "date_dt"]], bowl_all[["player", "team", "date_dt"]]])
    out = {}
    for name, g in rows.groupby("player"):
        teams = ", ".join(sorted({t for t in g["team"] if t}))
        last = g["date_dt"].max()
        parts = [f"{int(bat_n.get(name, 0))} bat / {int(bowl_n.get(name, 0))} bowl"]
        if teams:
            parts.append(teams)
        if pd.notna(last):
            parts.append(f"last played {last:%d %b %Y}")
        out[name] = (int(bat_n.get(name, 0)) + int(bowl_n.get(name, 0)), " · ".join(parts))
    return out


# ---------------- data ----------------
m_df = db.load("matches")
bat_raw, bowl_raw, field_raw = db.load("batting"), db.load("bowling"), db.load("fielding")
matches, bat_all, bowl_all, field_all = prepared(m_df, bat_raw, bowl_raw, field_raw)
players = sorted(set(bat_all["player"]) | set(bowl_all["player"]))
known_opps = sorted({o for o in matches["opponent_label"] if o}, key=str.lower)
known_formats = sorted({f for f in matches["format_label"] if f != NO_FORMAT}, key=str.lower)
types = db.bowling_types()
bowl_all["bowl_type"] = bowl_all["player"].map(types).fillna(NOT_SET)

# ---------------- Sidebar: store status + filters ----------------
PERIODS = ["All time", "Last 7 days", "Last 30 days", "Last 3 months", "This year", "Custom range"]
FILTER_KEYS = ["f_period", "f_range", "f_undated", "f_ball", "f_venue", "f_inn", "f_formats", "f_pos", "f_btype"]


def reset_filters():
    for k in FILTER_KEYS:
        st.session_state.pop(k, None)


with st.sidebar:
    ui.sidebar_brand()
    our_names = [n.strip() for n in st.session_state.our_team.split(",") if n.strip()]
    all_teams = sorted(set(bat_all["team"]) | set(bowl_all["team"]))
    other_teams = [t for t in all_teams if not team_matches(t, our_names)]
    if IS_ADMIN:
        st.session_state.our_team = st.text_input(
            "Your team name(s)", st.session_state.our_team,
            help="Only this team's players are saved. If the scorer writes it differently, "
                 "add each spelling separated by commas, e.g. UOL QHPC, UOL Qalandars")
        our_names = [n.strip() for n in st.session_state.our_team.split(",") if n.strip()]
        other_teams = [t for t in all_teams if not team_matches(t, our_names)]
        online = db.use_sheets()
        st.caption(("🟢 " if online else "🟡 ") + "Data store: " + db.backend_label())
        if not online:
            st.caption("Add Google Sheets secrets to make saved data permanent online (see README).")
    c1, c2 = st.columns(2)
    c1.metric("Matches", len(matches))
    c2.metric("Players", len(players))
    if IS_ADMIN and other_teams:
        st.caption(f"⚠️ Your data still has stats for {len(other_teams)} other team(s). "
                   "Remove them in 🛠 Manage.")

    st.divider()
    st.subheader("Filters")

    # date
    today = dt.date.today()
    period = st.selectbox("Date", PERIODS, key="f_period")
    date_range = None
    if period == "Custom range":
        dated = matches["date_dt"].dropna()
        lo = dated.min().date() if not dated.empty else today - dt.timedelta(days=30)
        hi = dated.max().date() if not dated.empty else today
        picked = st.date_input("From – to", value=(lo, hi), key="f_range", format="DD/MM/YYYY")
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            date_range = (picked[0], picked[1])
        elif isinstance(picked, (tuple, list)) and len(picked) == 1:
            date_range = (picked[0], picked[0])   # second date not picked yet
    elif period != "All time":
        start = {"Last 7 days": today - dt.timedelta(days=6),
                 "Last 30 days": today - dt.timedelta(days=29),
                 "Last 3 months": today - dt.timedelta(days=91),
                 "This year": dt.date(today.year, 1, 1)}[period]
        date_range = (start, today)
    include_undated = True
    n_undated = int(matches["date_dt"].isna().sum())
    if date_range and n_undated:
        include_undated = st.checkbox(f"Also include {n_undated} undated match(es)", value=False,
                                      key="f_undated", help="Screenshot uploads don't carry a date.")

    # white ball / red ball
    ball_opts = list(BALLS) + ([NOT_SET] if (matches["ball_label"] == NOT_SET).any() else [])
    if "f_ball" in st.session_state:
        st.session_state["f_ball"] = [b for b in st.session_state["f_ball"] if b in ball_opts]
    ball_sel = st.multiselect("Ball", ball_opts, key="f_ball", placeholder="White and red ball")

    # home / away
    venue_opts = list(VENUES) + ([NOT_SET] if (matches["venue_label"] == NOT_SET).any() else [])
    if "f_venue" in st.session_state:
        st.session_state["f_venue"] = [v for v in st.session_state["f_venue"] if v in venue_opts]
    venue_sel = st.multiselect("Venue", venue_opts, key="f_venue", placeholder="Home and away")

    # batting first / chasing
    inn_opts = list(INNINGS) + ([NOT_SET] if (matches["innings_label"] == NOT_SET).any() else [])
    if "f_inn" in st.session_state:
        st.session_state["f_inn"] = [v for v in st.session_state["f_inn"] if v in inn_opts]
    inn_sel = st.multiselect("Innings", inn_opts, key="f_inn", placeholder="Batting first and chasing",
                             help="Our innings: matches where we batted first, or chased.")

    # match format
    formats = sorted(matches["format_label"].unique(), key=lambda f: (f == NO_FORMAT, f.lower()))
    if "f_formats" in st.session_state:
        st.session_state["f_formats"] = [f for f in st.session_state["f_formats"] if f in formats]
    fmt_sel = st.multiselect("Match format", formats, key="f_formats", placeholder="All formats")

    # batting position
    top = int(bat_all["position"].max()) if bat_all["position"].notna().any() else 11
    pos_opts = list(range(1, max(11, top) + 1))
    if "f_pos" in st.session_state:
        st.session_state["f_pos"] = [p for p in st.session_state["f_pos"] if p in pos_opts]
    pos_sel = st.multiselect("Batting position", pos_opts, key="f_pos",
                             format_func=lambda p: f"No. {p}", placeholder="All positions",
                             help="Pick one or more. Applies to batting stats only.")

    # bowling type
    btype_opts = ["Pace", "Spin"] + ([NOT_SET] if (bowl_all["bowl_type"] == NOT_SET).any() else [])
    if "f_btype" in st.session_state:
        st.session_state["f_btype"] = [t for t in st.session_state["f_btype"] if t in btype_opts]
    btype_sel = st.multiselect("Bowling type", btype_opts, key="f_btype", placeholder="Pace and spin",
                               help="Applies to bowling stats only.")

    m_view = filter_rows(matches, date_range, include_undated, fmt_sel, balls=ball_sel, venues=venue_sel,
                         innings=inn_sel)
    if date_range or ball_sel or venue_sel or inn_sel or fmt_sel or pos_sel or btype_sel:
        st.caption(f"Showing **{len(m_view)}** of {len(matches)} matches")
        st.button("↺  Reset filters", on_click=reset_filters)

    # admin login
    st.divider()
    if IS_ADMIN:
        if ADMIN_PASSWORD:
            st.caption("🔓 Admin — all tabs unlocked")
            st.button("Log out", on_click=_logout)
    else:
        with st.expander("🔒 Admin login"):
            if not ADMIN_PASSWORD:
                st.caption("No admin password is set yet. Add `admin_password` to the app's secrets.")
            else:
                st.text_input("Password", type="password", key="admin_pw", on_change=_check_password)
                st.button("Log in", on_click=_check_password)
                if st.session_state.pop("login_error", False):
                    st.error("Wrong password.")

# rows after filters (fielding isn't filtered by batting position: a catch has nothing
# to do with where the catcher batted)
common = dict(date_range=date_range, include_undated=include_undated, formats=fmt_sel, balls=ball_sel,
              venues=venue_sel, innings=inn_sel)
bat_base = filter_rows(bat_all, **common)
bat_f = filter_rows(bat_base, positions=pos_sel)
bowl_base = filter_rows(bowl_all, **common)
bowl_f = filter_rows(bowl_base, bowl_types=btype_sel)
field_f = filter_rows(field_all, **common)

note_parts = []
if date_range:
    note_parts.append(f"{date_range[0]:%d %b %Y} – {date_range[1]:%d %b %Y}")
if ball_sel:
    note_parts.append(" & ".join(ball_sel))
if venue_sel:
    note_parts.append(" & ".join(venue_sel) + (" games" if NOT_SET not in venue_sel else ""))
if inn_sel:
    note_parts.append(" & ".join(inn_sel))
if fmt_sel:
    note_parts.append(", ".join(fmt_sel))
if pos_sel:
    note_parts.append("batting at " + ", ".join(f"No. {p}" for p in pos_sel))
if btype_sel:
    note_parts.append(" & ".join(btype_sel) + " bowlers")
FILTER_NOTE = ("Filtered: " + " · ".join(note_parts)) if note_parts else ""
unknown_pos = int(bat_base["position"].isna().sum()) if pos_sel else 0


@st.cache_data(show_spinner=False)
def points_for(bat, bowl, field):
    return match_points(bat, bowl, field)


def mvp_points():
    """MVP counts every innings: the batting-position and bowling-type filters are for the
    batting and bowling tables only."""
    return points_for(bat_base, bowl_base, field_f)


@st.cache_data(show_spinner=False)
def card_png(card_json):
    import json
    return ui.share_card_png(json.loads(card_json))


def build_card(name, pb, pw, field_f, mp, teams, n_matches, fmts):
    """Numbers for the shareable PNG card, using the same filters as the page."""
    table = mvp_table(mp)
    potm = int(players_of_match(mp)["player"].eq(name).sum()) if not mp.empty else 0
    mvp_line = ""
    if name in set(table["Player"]):
        row = table[table["Player"] == name].iloc[0]
        rank = int(table.index[table["Player"] == name][0]) + 1
        mvp_line = f"MVP {int(row['Total'])} pts · Rank {rank} of {len(table)}"
    fd = field_f.loc[field_f["player"] == name, "dismissal"]
    catches = int(fd.isin(["caught", "caught & bowled"]).sum())
    more = None
    if not pb.empty:
        b = career_batting(pb).iloc[0]
        top = pb.sort_values(["runs", "balls"], ascending=[False, True])
        top = top.assign(_no=top["how_out"] == "not out").sort_values(["runs", "_no"], ascending=False).iloc[0]
        bat = ("BATTING", [("Runs", int(b["Runs"])), ("Innings", int(b["Inns"])),
                           ("Highest", f"{int(top['runs'])}{'*' if top['how_out'] == 'not out' else ''}"),
                           ("Average", dash(b["Avg"]) if pd.isna(b["Avg"]) else f"{b['Avg']:.2f}"),
                           ("Strike rate", dash(b["SR"]) if pd.isna(b["SR"]) else f"{b['SR']:.2f}"),
                           ("50s / 100s", f"{int(b['50s'])} / {int(b['100s'])}")])
        more = ("MORE", [("Fours", int(pb["fours"].sum())), ("Sixes", int(pb["sixes"].sum())),
                         ("Not outs", int((pb["how_out"] == "not out").sum())), ("Catches", catches),
                         ("Player of Match", potm), ("Matches", n_matches)])
    if not pw.empty:
        w = career_bowling(pw).iloc[0]
        bowl = ("BOWLING", [("Wickets", int(w["Wkts"])), ("Best", w["Best"]),
                            ("Average", "—" if pd.isna(w["Avg"]) else f"{w['Avg']:.2f}"),
                            ("Economy", "—" if pd.isna(w["Econ"]) else f"{w['Econ']:.2f}"),
                            ("Overs", f"{w['Overs']:g}"), ("Type", w.get("Type", "—"))])
        if more is None:
            more = ("MORE", [("Maidens", int(w["Mdns"])), ("3W+", int(w["3W+"])), ("Catches", catches),
                             ("Player of Match", potm), ("Matches", n_matches), ("Innings", int(w["Inns"]))])
    sections = [s_ for s_ in ((bat if not pb.empty else more), (bowl if not pw.empty else more))]
    extra = " · ".join(x for x in [f"Fielding: {catches} catch{'es' if catches != 1 else ''}",
                                   f"Player of the Match {potm}×" if potm else ""] if x)
    fmt_txt = fmts[0] if len(fmts) == 1 else (f"{len(fmts)} formats" if fmts else "")
    return {"name": name, "line": " · ".join(x for x in [", ".join(teams), f"{n_matches} match(es)", fmt_txt] if x),
            "mvp": mvp_line, "sections": sections, "extra": extra,
            "scope": FILTER_NOTE or f"All matches from {ui.STATS_FROM}",
            "footer_right": f"As of {dt.date.today():%d %b %Y}"}


@st.cache_data(show_spinner=False)
def list_png_cached(payload):
    import json
    a = json.loads(payload)
    return ui.list_png(a["title"], a["subtitle"], [tuple(r) for r in a["rows"]], a["scope"], a["footer_right"])


def graphic_download(title, subtitle, rows, key, label="⬇️  Download graphic (PNG)"):
    """A download button for a branded PNG of a ranked list (same filters as the page)."""
    import json
    payload = json.dumps({"v": ui.GRAPHICS_VERSION, "title": title, "subtitle": subtitle,
                          "rows": [[str(n), str(v), str(d)] for n, v, d in rows],
                          "scope": FILTER_NOTE or f"All matches from {ui.STATS_FROM}",
                          "footer_right": f"As of {dt.date.today():%d %b %Y}"})
    slug = "-".join(title.lower().split())
    st.download_button(label, list_png_cached(payload), file_name=f"UOL-QHPC-{slug}.png", mime="image/png",
                       key=key, disabled=not rows, width="stretch")


@st.cache_data(show_spinner=False)
def table_png_cached(payload):
    import json
    a = json.loads(payload)
    return ui.table_png(a["title"], a["subtitle"], [tuple(c) for c in a["columns"]], a["rows"],
                        a["scope"], a["footer_right"])


def table_download(title, subtitle, columns, rows, key, label):
    """A download button for a branded PNG of a leaderboard table (same filters as the page)."""
    import json
    payload = json.dumps({"v": ui.GRAPHICS_VERSION, "title": title, "subtitle": subtitle, "columns": columns,
                          "rows": [[str(v) for v in r] for r in rows],
                          "scope": FILTER_NOTE or f"All matches from {ui.STATS_FROM}",
                          "footer_right": f"As of {dt.date.today():%d %b %Y}"})
    slug = "-".join(title.lower().split())
    st.download_button(label, table_png_cached(payload), file_name=f"UOL-QHPC-{slug}.png", mime="image/png",
                       key=key, disabled=not rows, width="stretch")


REC_MOBILE = ["P", "W", "L", "Win %"]


def show_table(df, key, mobile_cols=None, top=0, result_col=None, max_height=None, column_config=None):
    """A styled table. On phones, only the key columns show at first (a toggle shows the rest),
    and the player / opponent column stays pinned while scrolling sideways."""
    df = df.reset_index(drop=True)
    if IS_MOBILE and mobile_cols:
        keep = [c for c in mobile_cols if c in df.columns]
        if 0 < len(keep) < len(df.columns) and not st.toggle("Show all columns", key=f"cols_{key}"):
            df = df[keep]
    cfg = dict(column_config or {})
    for name in ("Player", "Opponent"):
        if name in df.columns and name not in cfg:
            cfg[name] = st.column_config.Column(name, pinned=True)
            break
    if "#" in df.columns and "#" not in cfg:
        cfg["#"] = st.column_config.NumberColumn("#", width="small", pinned=True)
    kw = {"height": min(38 + 35 * len(df), max_height)} if max_height else {}
    st.dataframe(ui.styled(df, top=top, result_col=result_col), hide_index=True, width="stretch",
                 column_config=cfg, **kw)


def show_filter_note(batting=True):
    if FILTER_NOTE:
        ui.note(FILTER_NOTE)
    if batting and unknown_pos:
        st.caption(f"{unknown_pos} innings from screenshot uploads have no batting position, "
                   "so they're left out while a position is picked.")


ui.masthead(len(matches), len(players), matches["date_dt"].max() if not matches.empty else None)
if IS_MOBILE:
    ui.note("Filters: tap » at the top left" + (f" · {FILTER_NOTE}" if FILTER_NOTE else ""))
# ---------------- Data health (admin) ----------------
def data_health():
    """Every check returns (title, kind, where_to_fix, rows). kind: 'fix' (wrong or missing)
    or 'check' (worth a look). rows is a DataFrame; empty means the check passed."""
    checks = []
    date_col = lambda df: fmt_date(df["date_dt"])
    normal = matches[matches["our_side"].isin(["A", "B"])]
    need = {"opponent_label": "opponent", "venue_label": "venue", "outcome_label": "result",
            "toss_label": "toss", "innings_label": "innings", "format_label": "format", "ball_label": "ball"}
    miss = []
    for _, r in matches.iterrows():
        keys = need if r["our_side"] in ("A", "B") else {"format_label": "format", "ball_label": "ball"}
        gaps = [lbl for col, lbl in keys.items() if r[col] in (NOT_SET, NO_FORMAT, "")]
        if pd.isna(pd.to_numeric(r.get("overs"), errors="coerce")):
            gaps.append("overs")
        if gaps:
            miss.append({"Date": r["date_dt"], "Match": f"{r['team_a']} vs {r['team_b']}",
                         "Missing": ", ".join(gaps), "ID": r["match_id"]})
    miss = pd.DataFrame(miss, columns=["Date", "Match", "Missing", "ID"])
    if not miss.empty:
        miss["Date"] = fmt_date(miss["Date"])
    checks.append(("Matches with missing details", "fix", "📋 Matches tab — edit the cells and Save", miss))

    std = set(FORMAT_CHOICES)
    odd = matches[~matches["format_label"].isin(std) & (matches["format_label"] != NO_FORMAT)]
    odd = (odd.groupby("format_label").size().reset_index(name="Matches")
              .rename(columns={"format_label": "Format"}))
    checks.append(("Formats not from your standard list", "check",
                   "📋 Matches tab — pick a format from the list (fine to keep if it's one you typed on purpose)", odd))

    start = pd.to_datetime(ui.STATS_FROM, dayfirst=True, errors="coerce")
    early = matches[(matches["date_dt"] < start) | matches["date_dt"].isna()]
    early = pd.DataFrame({"Date": date_col(early), "Match": early["team_a"] + " vs " + early["team_b"],
                          "Problem": np.where(early["date_dt"].isna(), "no date",
                                              f"before {ui.STATS_FROM}"), "ID": early["match_id"]})
    checks.append((f"Matches dated before {ui.STATS_FROM} or with no date", "check",
                   "🛠 Manage → Delete a match, if it shouldn't be here", early))

    b = bat_all
    bad_bat = []
    for _, r in b.iterrows():
        runs, balls, fours, sixes = int(r["runs"]), int(r["balls"]), int(r["fours"]), int(r["sixes"])
        why = []
        if runs > 0 and balls == 0:
            why.append("runs with 0 balls")
        if fours * 4 + sixes * 6 > runs:
            why.append("more boundary runs than runs")
        if balls and runs > balls * 6:
            why.append("more than 6 runs a ball")
        if why:
            bad_bat.append({"Date": r["date_dt"], "Player": r["player"], "R": runs, "B": balls,
                            "4s": fours, "6s": sixes, "Problem": ", ".join(why), "ID": r["match_id"]})
    bad_bat = pd.DataFrame(bad_bat, columns=["Date", "Player", "R", "B", "4s", "6s", "Problem", "ID"])
    if not bad_bat.empty:
        bad_bat["Date"] = fmt_date(bad_bat["Date"])
    checks.append(("Batting numbers that can't be right", "fix",
                   "Re-upload the match (Add matches → Replace the saved copy)", bad_bat))

    bad_bowl = []
    for _, r in bowl_all.iterrows():
        o = float(r["overs"] or 0)
        why = []
        if round((o - int(o)) * 10) > 5:
            why.append(f"overs {o:g} (balls part above 5)")
        if int(r["wickets"]) > 10:
            why.append("more than 10 wickets")
        if int(r["maidens"]) > int(o):
            why.append("more maidens than overs")
        if o == 0 and int(r["runs"]) > 0:
            why.append("no overs recorded (screenshot upload)")
        if why:
            bad_bowl.append({"Date": r["date_dt"], "Player": r["player"], "O": o, "M": int(r["maidens"]),
                             "R": int(r["runs"]), "W": int(r["wickets"]), "Problem": ", ".join(why),
                             "ID": r["match_id"]})
    bad_bowl = pd.DataFrame(bad_bowl, columns=["Date", "Player", "O", "M", "R", "W", "Problem", "ID"])
    if not bad_bowl.empty:
        bad_bowl["Date"] = fmt_date(bad_bowl["Date"])
    checks.append(("Bowling numbers to check", "fix",
                   "Re-upload the match from its PDF (Add matches → Replace the saved copy)", bad_bowl))

    squad = db.squad_names()
    if not squad:
        checks.append(("Squad list is empty", "check", "Google Sheet → tab squad → column Full name",
                       pd.DataFrame({"What to do": ["Add your players' full names so scorecard names can be "
                                                    "matched automatically"]})))
    else:
        sp = db.spellings()
        off = []
        for p in players:
            if p in squad:
                continue
            r = resolve_name(p, squad, sp)
            if r["status"] == "remembered" and r["full"] == p:
                continue
            off.append({"Saved name": p, "Suggestion": r["full"] or ", ".join(r["options"][:2]) or "—"})
        checks.append(("Player names not from the squad list", "fix", "🛠 Manage → Squad list — full names",
                       pd.DataFrame(off, columns=["Saved name", "Suggestion"])))
        checks.append(("Names typed twice in the squad list", "check", "Google Sheet → tab squad",
                       pd.DataFrame({"Full name": db.squad_duplicates()})))

    if not ui.fonts_installed():
        checks.append(("Fonts for the download graphics aren't installed", "fix",
                       "GitHub → packages.txt → add a new line: fonts-liberation → then Reboot the app",
                       pd.DataFrame({"What happens now": ["Graphics use a plain backup font (no bold or serif titles)"]})))

    untyped = sorted(set(bowl_all["player"]) - set(types))
    checks.append(("Bowlers without pace or spin", "fix", "🛠 Manage → Bowling types",
                   pd.DataFrame({"Bowler": untyped})))

    ignored = tuple(sorted(tuple(sorted(p)) for p in db.ignored_pairs()))
    groups = name_groups(tuple(players), ignored, 0.85) if len(players) > 1 else []
    checks.append(("Player names that look alike", "check", "🛠 Manage → Similar names",
                   pd.DataFrame({"Possibly the same player": [", ".join(g) for g in groups]})))

    dups = dup_pairs(matches, bat_all, tuple(sorted(tuple(sorted(p)) for p in db.dup_ignored()))) \
        if len(matches) > 1 else []
    checks.append(("Possible duplicate matches", "fix", "🛠 Manage → Duplicate matches",
                   pd.DataFrame({"Match": [f"{d['a']['team_a']} vs {d['a']['team_b']} — IDs "
                                           f"{d['a']['match_id']} & {d['b']['match_id']}" for d in dups]})))

    opp_groups = name_clusters(sorted({o for o in normal["opponent_label"] if o}))
    checks.append(("Opposition names that look alike", "check", "🛠 Manage → Opposition names",
                   pd.DataFrame({"Possibly the same team": [", ".join(g) for g in opp_groups]})))

    others = [t for t in sorted(set(bat_all["team"]) | set(bowl_all["team"])) if not team_matches(t, our_names)]
    checks.append(("Other teams' player stats still saved", "fix", "🛠 Manage → Remove other teams' stats",
                   pd.DataFrame({"Team": others})))
    return checks


HEALTH = data_health() if IS_ADMIN else []
n_issues = sum(1 for c in HEALTH if not c[3].empty)

TAB_LABELS = {"top": "🏆  Top performers", "mvp": "🏅  MVP", "team": "📈  Team analysis",
              "board": "📊  Leaderboards", "search": "🔎  Player search"}
if IS_ADMIN:
    TAB_LABELS.update({"add": "➕  Add matches", "matches": "📋  Matches", "manage": "🛠  Manage",
                       "health": f"🩺  Data health ({n_issues})" if n_issues else "🩺  Data health ✓"})
TABS = dict(zip(TAB_LABELS, st.tabs(list(TAB_LABELS.values()))))

# ---------------- Add matches ----------------
if IS_ADMIN:
    with TABS["add"]:
        flash("add_done", balloons=True)
        up_key = f"uploads_{st.session_state.get('up_n', 0)}"
        ups = st.file_uploader(
            "Drop in one or more files — STUMPS PDFs and/or summary screenshots",
            type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key=up_key)

        files = sorted(ups or [], key=lambda u: not u.name.lower().endswith(".pdf"))  # PDFs first
        if files:
            parsed = st.session_state.setdefault("parsed", {})
            items = []
            for u in files:
                key = f"{u.name}-{u.size}"
                is_pdf = u.name.lower().endswith(".pdf")
                if key not in parsed:
                    try:
                        with st.spinner(f"Reading {u.name}…"):
                            parsed[key] = (parse_stumps_pdf(io.BytesIO(u.getvalue())) if is_pdf
                                           else parse_summary_image(io.BytesIO(u.getvalue())))
                    except Exception as e:
                        parsed[key] = {"error": str(e)}
                items.append((u.name, is_pdf, u, parsed[key]))

            # ---- sort every file into: new / duplicate of a saved match / duplicate of another file here
            rank = {"certain": 0, "likely": 1, "possible": 2}
            saved = matches.to_dict("records")
            saved_by_id = {str(r["match_id"]): r for r in saved}
            saved_bat = {mid: g for mid, g in bat_all.groupby("match_id")}
            empty_bat = pd.DataFrame(columns=["team", "player", "runs", "balls"])
            errors, cards, kept = [], [], []
            for name, is_pdf, u, p in items:
                kind = "PDF" if is_pdf else "Screenshot"
                if "error" in p:
                    errors.append({"File": name, "Type": kind, "Problem": str(p["error"])[:80]})
                    continue
                mt = p["meta"]
                card = {"name": name, "kind": kind, "u": u, "p": p, "status": "new"}
                for n2, k2, p2 in kept:
                    level, reasons = duplicate_evidence(mt, p2["meta"], p["batting"], p2["batting"])
                    if level:
                        card.update(status="upload_dup", level=level, reasons=reasons, twin=(n2, k2, p2))
                        break
                if card["status"] == "new":
                    mid = str(mt["match_id"])
                    best = None
                    for r in ([saved_by_id[mid]] if mid in saved_by_id else saved):
                        level, reasons = duplicate_evidence(mt, r, p["batting"],
                                                            saved_bat.get(str(r["match_id"]), empty_bat))
                        if level and (best is None or rank[level] < rank[best[0]]):
                            best = (level, reasons, r)
                    if best:
                        card.update(status="saved_dup", level=best[0], reasons=best[1], saved=best[2])
                    kept.append((name, kind, p))
                cards.append(card)

            n_new = sum(c["status"] == "new" for c in cards)
            n_sd = sum(c["status"] == "saved_dup" for c in cards)
            n_ud = sum(c["status"] == "upload_dup" for c in cards)
            st.markdown(f"**{len(items)} file(s):** {n_new} new · {n_sd} look already saved · "
                        f"{n_ud} duplicate another file here" + (f" · {len(errors)} couldn't be read" if errors else ""))
            if errors:
                st.dataframe(pd.DataFrame(errors), hide_index=True, width="stretch")
            if n_sd or n_ud:
                st.caption("Duplicates are skipped unless you choose otherwise. Open **Proof** on each one to check.")

            ACT_SAVED = {"skip": "Skip — it's a duplicate", "replace": "Replace the saved copy with this file",
                         "new": "Not a duplicate — save as a new match"}
            ACT_UPLOAD = {"skip": "Skip — duplicate of another file here", "new": "Not a duplicate — save it too"}
            choices, unknown_names = [], set()
            names_sig = "|".join(our_names)
            upload_opps = []   # names picked on earlier cards, offered on later ones
            for c in cards:
                name, kind, u, p = c["name"], c["kind"], c["u"], c["p"]
                mt = p["meta"]
                fk = short_hash(f"{name}-{u.size}")
                labels = {"A": mt["team_a"] or "Team A", "B": mt["team_b"] or "Team B",
                          "AB": "Both sides — intra-squad game", "skip": "Skip this match"}
                with st.container(border=True):
                    info, ctrl = st.columns([3, 2])
                    info.markdown(f'**{labels["A"]}** vs **{labels["B"]}**')
                    info.caption(" · ".join(x for x in [name, kind, mt.get("format") or "", mt.get("date") or ""] if x))
                    action = "new"
                    if c["status"] != "new":
                        if c["status"] == "saved_dup":
                            other, other_label = c["saved"], f'Saved · ID {c["saved"]["match_id"]}'
                            other_bat = saved_bat.get(str(other["match_id"]), empty_bat)
                            opts = dict(ACT_SAVED)
                            info.markdown(f'{DUP_LABELS[c["level"]]} of a saved match (ID {other["match_id"]})')
                        else:
                            n2, k2, p2 = c["twin"]
                            other, other_label, other_bat = p2["meta"], n2, p2["batting"]
                            opts = dict(ACT_UPLOAD)
                            info.markdown(f'{DUP_LABELS[c["level"]]} of **{n2}** in this upload')
                        if c["level"] == "certain":
                            opts.pop("new", None)   # same match ID can't be saved twice
                        action = ctrl.selectbox(f"What to do with {name}", list(opts), format_func=opts.get,
                                                key=f"act_{fk}", label_visibility="collapsed")
                        with st.expander("🔍 Proof — compare side by side"):
                            show_proof(mt, other, "This file", other_label,
                                       align_rows(p["batting"], other_bat), other_bat, c["reasons"])
                    side, details = None, {}
                    if action in ("new", "replace"):
                        default = default_side(mt["team_a"], mt["team_b"], our_names)
                        side = ctrl.selectbox(
                            f"Your team in {name}", list(labels), format_func=labels.get,
                            index=list(labels).index(default) if default else None,
                            key=f"side_{fk}_{short_hash(names_sig)}",
                            placeholder="Pick your side", label_visibility="collapsed")
                        if side in ("A", "B", "AB"):
                            opp_pool = list(dict.fromkeys(known_opps + upload_opps))
                            details = match_details(fk, mt, side, labels, opp_pool, known_formats)
                            if details.get("opponent"):
                                upload_opps.append(details["opponent"])
                            _, b, w, _f = db.rows_for(p, set(side))
                            ctrl.caption(("⚠️ Still to pick: " + ", ".join(details["missing"]) + " · "
                                          if details["missing"] else "")
                                         + f"Saves {len(b)} batting · {len(w)} bowling"
                                         + (" · replaces the saved copy" if action == "replace" else ""))
                            if side != "AB" and not team_matches(labels[side], our_names):
                                unknown_names.add(labels[side])
                        elif side == "skip":
                            ctrl.caption("Won't be saved")
                        else:
                            ctrl.caption("⚠️ Pick your side")
                    else:
                        ctrl.caption("Won't be saved")
                choices.append((c, action, side, details))
            if unknown_names:
                st.caption(f"Tip: add {', '.join(sorted(unknown_names))} to “Your team name(s)” in the "
                           "sidebar and it'll be picked automatically.")

            # screenshots: show image next to what OCR read, so you can eyeball before saving
            for name, is_pdf, u, p in items:
                if not is_pdf and "error" not in p and p["batting"]:
                    with st.expander(f"Check what was read from {name}"):
                        ic, dc = st.columns([1, 1])
                        ic.image(u.getvalue(), width="stretch")
                        dc.dataframe(pd.DataFrame(p["batting"])[["team", "player", "runs", "balls"]],
                                     hide_index=True, width="stretch")
                        dc.dataframe(pd.DataFrame(p["bowling"])[["team", "player", "wickets", "runs"]],
                                     hide_index=True, width="stretch")

            live = [(c, action, side, d) for c, action, side, d in choices if action in ("new", "replace")]
            pending = sum(1 for _, _, side, _d in live if side is None)
            no_ball = sum(1 for _, _, side, d in live if side in ("A", "B", "AB") and d.get("missing"))
            to_save = []
            for c, _, side, d in live:
                if side in ("A", "B", "AB"):
                    c["p"]["meta"].update({k: d.get(k, "") for k in
                                           ("ball", "venue", "opponent", "outcome", "format", "overs",
                                            "toss_result", "innings")})
                    to_save.append((c["p"], set(side)))
            to_replace = sorted({str(c["saved"]["match_id"]) for c, action, side, _d in live
                                 if action == "replace" and side in ("A", "B", "AB")})
            if pending:
                st.warning(f"Pick your side for {pending} match(es) — or choose “Skip this match”.")
            if no_ball:
                st.warning(f"Fill in the match details (opponent, venue, result, toss, innings, format, overs, "
                           f"ball) for {no_ball} match(es).")

            # ---- scorecard names -> full names from the squad list
            name_map, name_memory, names_missing, new_squad = name_review(to_save) if to_save else ({}, {}, [], [])

            # ---- your bowlers with no bowling type yet: ask once per player, pace or spin
            new_bowlers = sorted({name_map.get(r["player"], r["player"])
                                  for p, sides in to_save for r in db.rows_for(p, sides)[2]
                                  if r["player"] not in names_missing      # asked once you've said who it is
                                  and name_map.get(r["player"], r["player"]) not in types})
            bowl_choice = {}
            if new_bowlers:
                with st.container(border=True):
                    st.markdown(f"**New bowlers — pace or spin?** ({len(new_bowlers)})")
                    st.caption("Asked once per player. Change it any time in 🛠 Manage → Bowling types.")
                    grid = st.columns(3)
                    for i, name in enumerate(new_bowlers):
                        guess, like = guess_type(name, types)
                        with grid[i % 3]:
                            bowl_choice[name] = st.radio(
                                name, ["Pace", "Spin"], horizontal=True, key=f"bt_{short_hash(name)}",
                                index=["Pace", "Spin"].index(guess) if guess else None)
                            if guess:
                                st.caption(f"Pre-filled from {like}")
            missing_types = [n for n, v in bowl_choice.items() if v is None]
            if missing_types:
                st.warning(f"Pick pace or spin for {len(missing_types)} bowler(s): {', '.join(missing_types)}.")

            label = f"✅  Save {len(to_save)} match(es)" + (f" — replacing {len(to_replace)}" if to_replace else "")
            if cards and st.button(label, type="primary",
                                   disabled=bool(pending) or bool(no_ball) or bool(missing_types)
                                   or bool(names_missing) or not to_save):
                with st.spinner(f"Saving {len(to_save)} match(es) to your sheet…"):
                    if bowl_choice:
                        db.set_bowling_types(bowl_choice)
                    for mid in to_replace:
                        db.delete_match(mid)
                    db.add_to_squad(new_squad)
                    nb, nw = db.append_many(to_save, names=name_map)
                    db.save_spellings(name_memory)
                skipped = len(cards) - len(to_save)
                st.session_state["add_done"] = (
                    f"Saved {len(to_save)} match(es): {nb} batting and {nw} bowling entries for your team."
                    + (f" Replaced {len(to_replace)} saved copy(ies)." if to_replace else "")
                    + (f" Added {len(new_squad)} new player(s) to the squad list." if new_squad else "")
                    + (f" Skipped {skipped}." if skipped else ""))
                st.session_state.parsed = {}
                st.session_state["up_n"] = st.session_state.get("up_n", 0) + 1  # empties the uploader
                st.rerun()
            elif cards and not to_save and not pending:
                st.caption("Nothing to save — every file is skipped.")

        with st.expander("Add a match by hand (fallback if a screenshot won't read)"):
            st.caption("Enter your team's players only. Leave blanks as 0. Enter batters in batting order. "
                       "Names that clearly match the squad list are saved with the full name.")
            mc1, mc2 = st.columns(2)
            ta = mc1.text_input("Your team", value=our_names[0] if our_names else "", key="m_ta")
            sa = mc2.text_input("Your score", key="m_sa", placeholder="e.g. 190-6 in 25.0 overs")
            if known_opps:
                tb = mc1.selectbox("Opponent", known_opps + [TYPE_OTHER], index=None, key="m_tb_pick",
                                   placeholder="Pick a team, or type a new one")
                if tb == TYPE_OTHER:
                    tb = mc1.text_input("New opponent name", key="m_tb")
            else:
                tb = mc1.text_input("Opponent", key="m_tb")
            tb = tb or ""
            sb = mc2.text_input("Opponent score", key="m_sb")
            res = st.text_input("Result", key="m_res", placeholder="e.g. UOL QHPC won by 15 runs")
            mc3, mc4, mc5 = st.columns(3)
            m_date = mc3.date_input("Date", value=today, key="m_date", format="DD/MM/YYYY")
            m_format = mc4.selectbox("Match format", list(dict.fromkeys(FORMAT_CHOICES + known_formats)) + [FMT_OTHER],
                                     key="m_format", index=None, placeholder="Pick…")
            if m_format == FMT_OTHER:
                m_format = mc4.text_input("Type the format", key="m_format_typed")
            m_overs = mc5.number_input("Overs per side", min_value=1, max_value=200, step=1, value=None,
                                       key="m_overs", placeholder="e.g. 20")
            mc6, mc7, mc8 = st.columns(3)
            m_venue = mc6.radio("Venue", list(VENUES), index=None, horizontal=True, key="m_venue")
            m_outcome = mc7.selectbox("Result for us", list(OUTCOMES), index=None, key="m_outcome",
                                      placeholder="Pick…")
            m_ball = mc8.radio("Ball", list(BALLS), index=None, horizontal=True, key="m_ball")
            mc9, mc10, _ = st.columns(3)
            m_toss = mc9.radio("Toss", list(TOSSES), index=None, horizontal=True, key="m_toss",
                               format_func=lambda t: f"{t} toss")
            m_inn = mc10.radio("Our innings", list(INNINGS), index=None, horizontal=True, key="m_inn")

            st.markdown("**Your batting** (one row per batter, in batting order)")
            bat_tmpl = pd.DataFrame([{"player": "", "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
                                      "not_out": False}])
            bat_edit = st.data_editor(bat_tmpl, num_rows="dynamic", hide_index=True,
                                      width="stretch", key="m_bat")
            st.markdown("**Your bowling** (one row per bowler)")
            st.caption("Type is needed only for bowlers the app hasn't seen before.")
            bowl_tmpl = pd.DataFrame([{"player": "", "type": None, "overs": 0.0, "maidens": 0, "runs": 0,
                                       "wickets": 0, "wides": 0, "no_balls": 0}])
            bowl_edit = st.data_editor(
                bowl_tmpl, num_rows="dynamic", hide_index=True, width="stretch", key="m_bowl",
                column_config={"type": st.column_config.SelectboxColumn("type", options=["Pace", "Spin"])})

            if st.button("✅  Save manual entry", disabled=not ta.strip()):
                team = ta.strip()
                mid = f"manual-{int(time.time())}"
                p = {"meta": {"match_id": mid, "date": str(m_date), "format": (m_format or "").strip(),
                              "overs": str(int(m_overs)) if m_overs else "", "result": res, "toss": "",
                              "team_a": team,
                              "ball": m_ball or "", "venue": m_venue or "", "outcome": m_outcome or "",
                              "toss_result": m_toss or "", "innings": m_inn or "",
                              "opponent": tb.strip(),
                              "team_b": tb.strip(), "score_a": sa, "score_b": sb}, "batting": [], "bowling": []}
                for _, r in bat_edit.iterrows():
                    if _text(r["player"]):
                        runs, balls = _int(r["runs"]), _int(r["balls"])
                        p["batting"].append({
                            "team": team, "player": _text(r["player"]),
                            "how_out": "not out" if bool(r["not_out"]) else "other", "bowler": "", "fielder": "",
                            "runs": runs, "balls": balls, "fours": _int(r["fours"]), "sixes": _int(r["sixes"]),
                            "sr": round(runs / balls * 100, 2) if balls else 0,
                            "pos": len(p["batting"]) + 1})
                for _, r in bowl_edit.iterrows():
                    if _text(r["player"]):
                        overs, runs = _float(r["overs"]), _int(r["runs"])
                        p["bowling"].append({
                            "team": team, "player": _text(r["player"]),
                            "overs": overs, "maidens": _int(r["maidens"]), "runs": runs,
                            "wickets": _int(r["wickets"]),
                            "econ": round(runs / overs, 2) if overs else 0,
                            "dots": 0, "fours_conceded": 0, "sixes_conceded": 0,
                            "wides": _int(r["wides"]), "no_balls": _int(r["no_balls"])})
                typed_types = {_text(r["player"]): r["type"] for _, r in bowl_edit.iterrows()
                               if _text(r["player"]) and r["type"] in ("Pace", "Spin")}
                no_type = [b["player"] for b in p["bowling"]
                           if b["player"] not in types and b["player"] not in typed_types]
                if not (p["batting"] or p["bowling"]):
                    st.warning("Add at least one batter or bowler before saving.")
                elif not (tb.strip() and m_venue and m_outcome and m_format and m_overs and m_toss and m_inn):
                    st.warning("Fill in the opponent, venue, result, toss, innings, match format and overs.")
                elif not m_ball:
                    st.warning("Pick white ball or red ball.")
                elif no_type:
                    st.warning(f"Pick pace or spin for new bowler(s): {', '.join(no_type)}.")
                else:
                    squad, sp = db.squad_names(), db.spellings()
                    manual_names = {}
                    for row in p["batting"] + p["bowling"]:
                        r = resolve_name(row["player"], squad, sp) if squad else {"status": "ask"}
                        if r["status"] != "ask":
                            manual_names[row["player"]] = r["full"]
                    if typed_types:
                        db.set_bowling_types({manual_names.get(k, k): v for k, v in typed_types.items()})
                    nb, nbw = db.append_match(p, {"A"}, names=manual_names)
                    st.session_state["add_done"] = f"Saved {nb} batting and {nbw} bowling entries."
                    st.rerun()


# ---------------- Player search ----------------
def render_profile(name, bat_f, field_f, bowl_f):
    pb = bat_f[bat_f["player"] == name].sort_values("date_dt", na_position="first")
    pw = bowl_f[bowl_f["player"] == name].sort_values("date_dt", na_position="first")
    teams = sorted({t for t in pd.concat([pb["team"], pw["team"]]) if t})
    fmts = sorted({f for f in pd.concat([pb["format_label"], pw["format_label"]]) if f != NO_FORMAT})
    n_matches = len(set(pb["match_id"]) | set(pw["match_id"]))
    st.markdown(f"### {name}")
    ui.chips(teams + [f"{n_matches} match(es)"] + fmts)

    if pb.empty and pw.empty:
        st.info("No innings for this player in the current filters — change the sidebar filters.")
        return
    with st.expander("📸  Player card — download and share"):
        import json
        mp = mvp_points()
        png = card_png(json.dumps({**build_card(name, pb, pw, field_f, mp, teams, n_matches, fmts),
                                   "v": ui.GRAPHICS_VERSION}, default=str))
        c1, c2 = st.columns([2, 3])
        c1.image(png, width="stretch")
        c2.markdown("A ready-made image of this player's stats for WhatsApp or Instagram. "
                    "It uses the filters you've picked in the sidebar.")
        c2.download_button("⬇️  Download card (PNG)", png, key=f"dl_{short_hash(name)}",
                           file_name=f"UOL-QHPC-{'-'.join(name.split())}.png", mime="image/png",
                           type="primary")

    view = st.radio("Show", ["Batting", "Bowling"], horizontal=True, key=f"pv_{short_hash(name)}",
                    label_visibility="collapsed")
    if view == "Batting":
        if pb.empty:
            st.caption("No batting innings under the current filters.")
            return
        r = career_batting(pb, fielding=field_f).iloc[0]
        ui.cards([(lbl, v, "") for lbl, v in [
            ("Innings", r["Inns"]), ("Runs", r["Runs"]), ("Highest", r["HS"]),
            ("Average", "—" if pd.isna(r["Avg"]) else f"{r['Avg']:.2f}"),
            ("Strike rate", "—" if pd.isna(r["SR"]) else f"{r['SR']:.2f}"),
            ("50s", r["50s"]), ("100s", r["100s"]), ("Catches", r["Catches"])]], small=True)
        fd = field_f.loc[field_f["player"] == name, "dismissal"].value_counts()
        if fd.sum():
            st.caption("Fielding: " + " · ".join(
                f"{int(fd.get(k, 0))} {one if int(fd.get(k, 0)) == 1 else many}" for k, one, many in [
                    ("caught", "catch", "catches"), ("caught & bowled", "caught & bowled", "caught & bowled"),
                    ("stumped", "stumping", "stumpings"), ("run out", "run-out", "run-outs")]
                if fd.get(k, 0)))
        how, not_outs, outs = dismissal_breakdown(pb)
        st.markdown("**How he got out**")
        if outs:
            top_how = how.iloc[0]
            st.caption(f"Out {outs} time(s) in {len(pb)} innings"
                       + (f", not out {not_outs}" if not_outs else "")
                       + f" — most often {top_how['How out'].lower()} ({int(top_how['Times'])}, "
                         f"{top_how['% of dismissals']:.0f}%).")
            st.write("")   # room for the table's toolbar
            hl, hr = st.columns([2, 3])
            hl.dataframe(ui.styled(how.assign(**{"% of dismissals": how["% of dismissals"].astype(int).astype(str) + "%"})),
                         hide_index=True, width="stretch")
            hr.bar_chart(how.set_index("How out")[["Times"]], horizontal=True, height=max(140, 42 * len(how)),
                         x_label="", y_label="Times")
        else:
            st.caption(f"Not out in all {len(pb)} innings so far.")
        left, right = st.columns([2, 3])
        with left:
            st.markdown("**By batting position**")
            bp = batting_by_position(pb)
            if bp.empty:
                st.caption("No batting positions recorded.")
            else:
                st.dataframe(ui.styled(bp), hide_index=True, width="stretch")
        with right:
            st.markdown("**Runs, innings by innings**")
            form = pb.reset_index(drop=True)[["runs"]].rename(columns={"runs": "Runs"})
            form.index = form.index + 1
            st.bar_chart(form, height=220, x_label="Innings", y_label="Runs")
        st.markdown("**Match by match**")
        mbm = pb.sort_values("date_dt", ascending=False, na_position="last")
        show_table(pd.DataFrame({
            "Date": fmt_date(mbm["date_dt"]), "Vs": mbm["opponent"], "Ball": mbm["ball_label"],
            "Format": mbm["format_label"], "Pos": mbm["position"],
            "How out": mbm["how_out"].map(lambda h: "Not out" if h == "not out" else HOW_OUT_LABELS.get(h, "—")),
            "R": mbm["runs"], "B": mbm["balls"],
            "4s": mbm["fours"], "6s": mbm["sixes"], "SR": mbm["sr"]}), f"mbm_bat_{short_hash(name)}",
            mobile_cols=["Date", "Vs", "R", "B", "SR"])
    else:
        if pw.empty:
            st.caption("No bowling innings under the current filters.")
            return
        r = career_bowling(pw).iloc[0]
        st.caption(f"Bowling type: **{r['Type']}**")
        ui.cards([(lbl, v, "") for lbl, v in [
            ("Innings", r["Inns"]), ("Overs", f"{r['Overs']:g}"), ("Wickets", r["Wkts"]), ("Best", r["Best"]),
            ("Average", "—" if pd.isna(r["Avg"]) else f"{r['Avg']:.2f}"),
            ("Economy", "—" if pd.isna(r["Econ"]) else f"{r['Econ']:.2f}"), ("3W+", r["3W+"])]], small=True)
        st.markdown("**Match by match**")
        mbm = pw.sort_values("date_dt", ascending=False, na_position="last")
        show_table(pd.DataFrame({
            "Date": fmt_date(mbm["date_dt"]), "Vs": mbm["opponent"], "Ball": mbm["ball_label"],
            "Format": mbm["format_label"], "O": mbm["overs"], "M": mbm["maidens"], "R": mbm["runs"], "W": mbm["wickets"],
            "Econ": mbm["econ"], "Wd": mbm["wides"], "NB": mbm["no_balls"]}), f"mbm_bowl_{short_hash(name)}",
            mobile_cols=["Date", "Vs", "O", "R", "W"])


def compare_table(names, bat_f, field_f, bowl_f):
    cb = career_batting(bat_f[bat_f["player"].isin(names)], fielding=field_f).set_index("Player")
    cw = career_bowling(bowl_f[bowl_f["player"].isin(names)]).set_index("Player")
    rows = {}
    for n in names:
        b = cb.loc[n] if n in cb.index else None
        w = cw.loc[n] if n in cw.index else None
        rows[n] = {
            "Bat inns": b["Inns"] if b is not None else 0,
            "Runs": b["Runs"] if b is not None else 0,
            "HS": b["HS"] if b is not None else "—",
            "Bat avg": dash(b["Avg"]) if b is not None else "—",
            "Strike rate": dash(b["SR"]) if b is not None else "—",
            "50s / 100s": f'{b["50s"]} / {b["100s"]}' if b is not None else "—",
            "Usual position": dash(b["Pos"]) if b is not None and "Pos" in b else "—",
            "Bowling type": w["Type"] if w is not None and "Type" in w else "—",
            "Bowl inns": w["Inns"] if w is not None else 0,
            "Wickets": w["Wkts"] if w is not None else 0,
            "Best": w["Best"] if w is not None else "—",
            "Bowl avg": dash(w["Avg"]) if w is not None else "—",
            "Economy": dash(w["Econ"]) if w is not None else "—",
        }
    return pd.DataFrame(rows).astype(str)


@st.fragment
def player_search(bat_f, field_f, bowl_f, players):
    show_filter_note()
    if not players:
        st.info("No data yet — add a match to get started.")
        return
    q = st.text_input("Search players", key="ps_q",
                      placeholder="Type any part of a name — e.g. hasn, ali, saif")
    names, fuzzy = search_names(q, players)
    if q.strip() and not names:
        st.warning(f"No player matches “{q}”.")
        return
    if fuzzy:
        st.caption(f"No exact match for “{q}” — closest spellings:")

    overview = player_overview(bat_f, bowl_f, field_f)
    found = overview[overview["Player"].isin(names)]
    missing = [n for n in names if n not in set(found["Player"])]
    if missing:   # players with no innings under the current filters
        found = pd.concat([found, pd.DataFrame({"Player": missing, "Matches": 0})], ignore_index=True)
    if fuzzy:
        found = found.set_index("Player").loc[names].reset_index()
    show_table(found, "ps_found", max_height=320, mobile_cols=["Player", "Matches", "Runs", "Wkts"],
               column_config={"Teams": st.column_config.TextColumn(width="medium")})

    pick_list = found["Player"].tolist()
    pick = pick_list[0] if len(pick_list) == 1 else st.selectbox(
        f"Open profile ({len(pick_list)} found)", pick_list)

    others_opts = [p for p in players if p != pick]
    if "ps_cmp" in st.session_state:
        st.session_state["ps_cmp"] = [p for p in st.session_state["ps_cmp"] if p in others_opts]
    others = st.multiselect("Compare with (optional)", others_opts, key="ps_cmp", max_selections=3,
                            placeholder="Add up to 3 players to compare side by side")
    st.divider()
    if others:
        st.markdown("**Side by side**")
        st.dataframe(compare_table([pick] + others, bat_f, field_f, bowl_f), width="stretch")
        st.divider()
    render_profile(pick, bat_f, field_f, bowl_f)


with TABS["search"]:
    player_search(bat_f, field_f, bowl_f, players)


# ---------------- Leaderboards ----------------
def highlight_batting(table, bat):
    if table.empty:
        return
    top = table.iloc[0]
    hs = bat.loc[bat["runs"].idxmax()]
    sr = table[table["Balls"] >= 20].sort_values("SR", ascending=False)
    ct = table.sort_values("Catches", ascending=False).iloc[0]
    ui.cards([
        ("Runs scored", f"{int(table['Runs'].sum()):,}", f"{bat['match_id'].nunique()} match(es) · {len(bat)} innings"),
        ("Leading run-scorer", int(top["Runs"]), top["Player"]),
        ("Highest score", f"{int(hs['runs'])}{'*' if hs['how_out'] == 'not out' else ''}",
         f"{hs['player']} · {int(hs['balls'])} balls"),
        ("Best strike rate", f"{sr.iloc[0]['SR']:.1f}" if not sr.empty else "—",
         f"{sr.iloc[0]['Player']} (min 20 balls)" if not sr.empty else "min 20 balls"),
        ("Most catches", int(ct["Catches"]), ct["Player"] if ct["Catches"] else ""),
    ])


def highlight_bowling(table, bowl):
    if table.empty:
        return
    top = table.iloc[0]
    best = bowl.sort_values(["wickets", "runs"], ascending=[False, True]).iloc[0]
    eco = table[table["Overs"] >= 6].sort_values("Econ")
    ui.cards([
        ("Wickets taken", int(table["Wkts"].sum()), f"{bowl['match_id'].nunique()} match(es) · {len(bowl)} spells"),
        ("Leading wicket-taker", int(top["Wkts"]), top["Player"]),
        ("Best figures", f"{int(best['wickets'])}/{int(best['runs'])}", best["player"]),
        ("Best economy", f"{eco.iloc[0]['Econ']:.2f}" if not eco.empty and pd.notna(eco.iloc[0]["Econ"]) else "—",
         f"{eco.iloc[0]['Player']} (min 6 overs)" if not eco.empty else "min 6 overs"),
        ("Maidens", int(table["Mdns"].sum()), ""),
    ])


LEADERBOARD_GRAPHIC_ROWS = 15


@st.fragment
def leaderboards(bat_f, field_f, bowl_f):
    c1, c2, c3 = st.columns([2, 1, 2])
    which = c1.radio("Show", ["Batting", "Bowling"], horizontal=True, key="lb_which")
    min_inns = c2.number_input("Min. innings", min_value=1, value=1, step=1, key="lb_min")
    q = c3.text_input("Find player", key="lb_q", placeholder="Filter the table by name")
    show_filter_note(batting=which == "Batting")
    if which == "Batting":
        # no batting-position column here: positions are chosen with the sidebar filter instead
        table = career_batting(bat_f, fielding=field_f).drop(columns=["Pos"], errors="ignore")
        highlight_batting(table, bat_f)
    else:
        table = career_bowling(bowl_f)
        highlight_bowling(table, bowl_f)
    table = table[table["Inns"] >= min_inns]
    if q.strip():
        table = table[table["Player"].str.contains(q.strip(), case=False, regex=False)]
    if table.empty:
        st.info("Nothing to show with these filters.")
        return
    table = table.reset_index(drop=True)
    table.insert(0, "#", range(1, len(table) + 1))
    top15 = table.head(LEADERBOARD_GRAPHIC_ROWS)
    mins = f" · min {min_inns} innings" if min_inns > 1 else ""
    if which == "Batting":
        table_download("Batting leaderboard", f"Top {len(top15)} by runs{mins}",
                       [["#", 70, "r"], ["Player", None, "l"], ["Inns", 100, "r"], ["Runs", 110, "r"],
                        ["HS", 100, "r"], ["Avg", 120, "r"], ["SR", 130, "r"]],
                       [[r["#"], r["Player"], r["Inns"], r["Runs"], r["HS"], _num(r["Avg"]), _num(r["SR"])]
                        for _, r in top15.iterrows()],
                       key="lb_dl_bat", label=f"⬇️  Download batting leaderboard — top {len(top15)} (PNG)")
    else:
        table_download("Bowling leaderboard", f"Top {len(top15)} by wickets{mins}",
                       [["#", 70, "r"], ["Player", None, "l"], ["Inns", 100, "r"], ["Wkts", 110, "r"],
                        ["Best", 120, "r"], ["Avg", 120, "r"], ["Econ", 120, "r"]],
                       [[r["#"], r["Player"], r["Inns"], r["Wkts"], r["Best"], _num(r["Avg"]), _num(r["Econ"])]
                        for _, r in top15.iterrows()],
                       key="lb_dl_bowl", label=f"⬇️  Download bowling leaderboard — top {len(top15)} (PNG)")
    show_table(table, "lb_" + which, top=0 if q.strip() else 3, max_height=640,
               mobile_cols=(["#", "Player", "Runs", "Avg", "SR"] if which == "Batting"
                            else ["#", "Player", "Wkts", "Avg", "Econ"]))
    st.caption(f"{len(table)} player(s) · ranked by {'runs' if which == 'Batting' else 'wickets'}. "
               "Click a column header to sort.")


def _num(v, dec=2):
    return "—" if v is None or pd.isna(v) else f"{v:.{dec}f}"


def _when(r, with_format=True):
    parts = [f"vs {r['opponent']}" if r["opponent"] else "", f"{r['date_dt']:%d %b %Y}" if pd.notna(r["date_dt"]) else "",
             r["format_label"] if with_format and r["format_label"] != NO_FORMAT else ""]
    return " · ".join(p for p in parts if p)


MIN_INNS_FOR_AVG = 3     # innings needed to appear in "Best batting average"
MIN_WKTS_FOR_AVG = 3     # wickets needed to appear in "Best bowling average"
MIN_BALLS_FOR_SR = 20    # balls faced needed to appear in "Highest strike rate"


@st.fragment
def top_performers(bat_f, bowl_f):
    show_filter_note(batting=True)
    if bat_f.empty and bowl_f.empty:
        st.info("No matches yet — check back soon.")
        return
    n = st.radio("Show", [5, 10], horizontal=True, key="tp_n", format_func=lambda k: f"Top {k}")
    cb, cw = career_batting(bat_f), career_bowling(bowl_f)

    most_runs = [(r["Player"], int(r["Runs"]), f"{r['Inns']} inns · avg {_num(r['Avg'])} · SR {_num(r['SR'])}")
                 for _, r in cb.sort_values(["Runs", "SR"], ascending=[False, False]).head(n).iterrows()]
    top_inns = (bat_f.assign(_no=bat_f["how_out"] == "not out")
                .sort_values(["runs", "_no", "balls"], ascending=[False, False, True]).head(n))
    vs = lambda r: f"vs {r['opponent']}" if r["opponent"] else ""      # just the opposition, nothing else
    best_scores = [(r["player"], f"{int(r['runs'])}{'*' if r['how_out'] == 'not out' else ''}", vs(r))
                   for _, r in top_inns.iterrows()]
    most_wkts = [(r["Player"], int(r["Wkts"]), f"{r['Inns']} inns · avg {_num(r['Avg'])} · econ {_num(r['Econ'])}")
                 for _, r in cw[cw["Wkts"] > 0].sort_values(["Wkts", "Avg"], ascending=[False, True]).head(n).iterrows()]
    spells = bowl_f[bowl_f["wickets"] > 0].sort_values(["wickets", "runs"], ascending=[False, True]).head(n)
    best_figs = [(r["player"], f"{int(r['wickets'])}/{int(r['runs'])}", vs(r)) for _, r in spells.iterrows()]
    q_sr = cb[(cb["Balls"] >= MIN_BALLS_FOR_SR) & cb["SR"].notna()]
    best_sr = [(r["Player"], f"{r['SR']:.2f}", f"{r['Runs']} runs · {r['Balls']} balls · {r['Inns']} inns")
               for _, r in q_sr.sort_values(["SR", "Runs"], ascending=False).head(n).iterrows()]
    q_bat = cb[(cb["Inns"] >= MIN_INNS_FOR_AVG) & cb["Avg"].notna()]
    bat_avg = [(r["Player"], f"{r['Avg']:.2f}", f"{r['Inns']} inns · {r['Runs']} runs · SR {_num(r['SR'])}")
               for _, r in q_bat.sort_values(["Avg", "Runs"], ascending=False).head(n).iterrows()]
    q_bowl = cw[(cw["Wkts"] >= MIN_WKTS_FOR_AVG) & cw["Avg"].notna()]
    bowl_avg = [(r["Player"], f"{r['Avg']:.2f}", f"{r['Wkts']} wkts · {r['Overs']:g} ov · econ {_num(r['Econ'])}")
                for _, r in q_bowl.sort_values(["Avg", "Wkts"], ascending=[True, False]).head(n).iterrows()]

    st.caption("Each list has a download button for a ready-to-share graphic.")
    batting = [
        ("Most runs", "Batting", most_runs, "Batting", None),
        ("Highest scores", "Single innings", best_scores, "Highest single-innings scores", None),
        ("Best batting average", f"Min {MIN_INNS_FOR_AVG} innings", bat_avg,
         f"Batting average · min {MIN_INNS_FOR_AVG} innings", None),
        ("Highest strike rate", f"Min {MIN_BALLS_FOR_SR} balls", best_sr,
         f"Strike rate · min {MIN_BALLS_FOR_SR} balls faced", None),
    ]
    bowling = [
        ("Most wickets", "Bowling", most_wkts, "Bowling", None),
        ("Best bowling figures", "Single innings", best_figs, "Best figures in an innings", None),
        ("Best bowling average", f"Min {MIN_WKTS_FOR_AVG} wickets", bowl_avg,
         f"Bowling average · min {MIN_WKTS_FOR_AVG} wickets", None),
    ]
    for num, group, lists in (("01", "Batting", batting), ("02", "Bowling", bowling)):
        ui.section(num, group)
        for i in range(0, len(lists), 2):
            cols = st.columns(2, gap="medium")
            for col, (title, sub, rows, gsub, grows) in zip(cols, lists[i:i + 2]):
                with col:
                    ui.top_list(title, sub, rows)
                    graphic_download(title, f"Top {len(rows)} · {gsub}", grows or rows,
                                     key=f"tp_dl_{short_hash(title)}")
                    st.write("")


with TABS["top"]:
    top_performers(bat_f, bowl_f)


# ---------------- MVP ----------------
@st.fragment
def mvp_tab(bat_f, bowl_f, field_f):
    show_filter_note(batting=False)
    mp = mvp_points()
    table = mvp_table(mp)
    if table.empty:
        st.info("No matches yet — check back soon.")
        return
    potm = players_of_match(mp)
    top, best = table.iloc[0], mp.sort_values("total", ascending=False).iloc[0]
    most_potm = table.sort_values(["POTM", "Total"], ascending=False).iloc[0]
    ui.cards([
        ("MVP", int(top["Total"]), f"{top['Player']} · {top['Matches']} match(es)"),
        ("Most Player of the Match", int(most_potm["POTM"]), most_potm["Player"] if most_potm["POTM"] else ""),
        ("Best single match", int(best["total"]),
         f"{best['player']}" + (f" vs {best['opponent']}" if best["opponent"] else "")),
        ("Players with points", len(table), f"{mp['match_id'].nunique()} match(es)"),
    ])
    c1, c2 = st.columns([1, 2])
    min_m = c1.number_input("Min. matches", min_value=1, value=1, step=1, key="mvp_min")
    q = c2.text_input("Find player", key="mvp_q", placeholder="Filter the table by name")
    t = table[table["Matches"] >= min_m]
    top10 = t.head(10)
    graphic_download("MVP rankings", f"Top {len(top10)} · Points for batting, bowling and fielding",
                     [(r["Player"], int(r["Total"]),
                       f"{r['Matches']} match(es) · Bat {r['Batting']} · Bowl {r['Bowling']} · Field {r['Fielding']}"
                       + (f" · POTM {r['POTM']}" if r["POTM"] else ""))
                      for _, r in top10.iterrows()],
                     key="mvp_dl", label="⬇️  Download MVP top 10 (PNG)")
    if q.strip():
        t = t[t["Player"].str.contains(q.strip(), case=False, regex=False)]
    t = t.reset_index(drop=True)
    t.insert(0, "#", range(1, len(t) + 1))
    show_table(t, "mvp", top=0 if q.strip() else 3, max_height=640,
               mobile_cols=["#", "Player", "Total", "Per match", "POTM"],
               column_config={"POTM": st.column_config.NumberColumn("POTM", help="Player of the Match awards"),
                              "Best": st.column_config.NumberColumn("Best", help="Most points in one match")})
    with st.expander("How points work"):
        for group, rules in POINTS_RULES:
            st.markdown(f"**{group}**")
            st.dataframe(pd.DataFrame(rules, columns=["", "Points"]), hide_index=True, width="stretch")
        st.caption("Player of the Match = most points in that match. The sidebar filters apply "
                   "(except batting position and bowling type), so you can see the MVP for a format, "
                   "a ball type, home or away, or a date range.")

    ui.section("01", "Player of the Match")
    show_table(pd.DataFrame({
        "Date": fmt_date(potm["date_dt"]), "Opponent": potm["opponent"].replace("", "—"),
        "Player": potm["player"], "Performance": potm["performance"], "Points": potm["total"]}), "potm",
        mobile_cols=["Date", "Player", "Performance", "Points"])


with TABS["mvp"]:
    mvp_tab(bat_f, bowl_f, field_f)


# ---------------- Team analysis ----------------
@st.fragment
def team_analysis(m):
    show_filter_note(batting=False)
    rec = team_record(m)
    n_intra = int((m["outcome_label"] == INTRA).sum())
    if rec["Played"] == 0:
        st.info("No matches against other teams in the current filters."
                + (f" ({n_intra} intra-squad game(s) aren't counted in results.)" if n_intra else ""))
        return
    win = f"{rec['Win %']:.0f}%" if rec["Win %"] is not None else "—"
    ui.cards([
        ("Played", rec["Played"], f"+ {n_intra} intra-squad (not counted)" if n_intra else "vs other teams"),
        ("Won", rec["Won"], ""),
        ("Lost", rec["Lost"], ""),
        ("Tied · Drawn · NR", f"{rec['Tied']} · {rec['Drawn']} · {rec['No result']}", ""),
        ("Win %", win, "of games with a result"),
    ])
    if rec["Not set"]:
        st.caption(f"⚠️ {rec['Not set']} match(es) have no result recorded, so they're left out of win %.")

    games = m[m["outcome_label"] != INTRA].sort_values("date_dt", na_position="first")
    recent = games.tail(10)
    st.markdown("**Recent form** — oldest to latest")
    ui.form_strip([(r["outcome_label"], f"{r['outcome_label']} vs {r['opponent_label']}"
                    + (f" · {r['date_dt']:%d %b %Y}" if pd.notna(r["date_dt"]) else ""))
                   for _, r in recent.iterrows()])

    ui.section("01", "Head to head")
    st.caption("Your record against each team.")
    show_table(record_table(games, "opponent_label", "Opponent"), "h2h", result_col="Last result",
               mobile_cols=["Opponent"] + REC_MOBILE)

    ui.section("02", "Toss · batting first vs chasing")
    known_inn = games[games["innings_label"] != NOT_SET]
    bf, ch = (team_record(known_inn[known_inn["innings_label"] == k]) for k in INNINGS)
    tw = team_record(games[games["toss_label"] == "Won"])
    fmt_pct = lambda r: f"{r['Win %']:.0f}%" if r["Win %"] is not None else "—"
    ui.cards([
        ("Win % batting first", fmt_pct(bf), f"{bf['Won']} won of {bf['Played']}"),
        ("Win % chasing", fmt_pct(ch), f"{ch['Won']} won of {ch['Played']}"),
        ("Win % after winning toss", fmt_pct(tw), f"{tw['Won']} won of {tw['Played']}"),
        ("Tosses won", f"{int((games['toss_label'] == 'Won').sum())} of "
                       f"{int(games['toss_label'].isin(TOSSES).sum())}", ""),
    ])
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        show_table(record_table(games, "innings_label", "Our innings").drop(columns=["Last result"]), "rec_innings_label",
                   mobile_cols=["Our innings"] + REC_MOBILE)
    with c2:
        show_table(record_table(games, "toss_label", "Toss").drop(columns=["Last result"]), "rec_toss_label",
                   mobile_cols=["Toss"] + REC_MOBILE)
    show_table(record_table(games, "toss_innings", "Toss and decision").drop(columns=["Last result"]), "rec_toss_innings",
               mobile_cols=["Toss and decision"] + REC_MOBILE)
    unknown = int(((games["toss_label"] == NOT_SET) | (games["innings_label"] == NOT_SET)).sum())
    if unknown:
        st.caption(f"{unknown} match(es) have no toss or innings recorded — set them in the 📋 Matches tab.")

    ui.section("03", "Home and away · white and red ball")
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        show_table(record_table(games, "venue_label", "Venue").drop(columns=["Last result"]), "rec_venue_label",
                   mobile_cols=["Venue"] + REC_MOBILE)
    with c2:
        show_table(record_table(games, "ball_label", "Ball").drop(columns=["Last result"]), "rec_ball_label",
                   mobile_cols=["Ball"] + REC_MOBILE)

    ui.section("04", "By match format")
    show_table(record_table(games, "format_label", "Format").drop(columns=["Last result"]), "rec_format_label",
               mobile_cols=["Format"] + REC_MOBILE)

    ui.section("05", "All results")
    allr = games.sort_values("date_dt", ascending=False, na_position="last")
    show_table(pd.DataFrame({
        "Date": fmt_date(allr["date_dt"]), "Opponent": allr["opponent_label"], "Venue": allr["venue_label"],
        "Toss": allr["toss_label"], "Innings": allr["innings_label"], "Format": allr["format_label"],
        "Ball": allr["ball_label"], "Us": allr["our_score"], "Them": allr["opp_score"],
        "Result": allr["outcome_label"], "Margin": allr["result"]}), "all_results", result_col="Result",
        mobile_cols=["Date", "Opponent", "Result"])


with TABS["team"]:
    team_analysis(m_view)

with TABS["board"]:
    leaderboards(bat_f, field_f, bowl_f)

# ---------------- Matches ----------------
if IS_ADMIN:
    with TABS["matches"]:
        show_filter_note(batting=False)
        if matches.empty:
            st.info("No matches saved yet.")
        elif m_view.empty:
            st.info("No matches in the current filters.")
        else:
            flash("ball_done")
            mv = m_view.sort_values("date_dt", ascending=False, na_position="last")
            normal = mv["our_side"].isin(["A", "B"]).tolist()
            table = pd.DataFrame({
                "Date": fmt_date(mv["date_dt"]),
                "Opponent": [o if n else INTRA for o, n in zip(mv["opponent_label"], normal)],
                "Venue": [v if v in VENUES else None for v in mv["venue_label"]],
                "Result": [o if o in OUTCOMES else None for o in mv["outcome_label"]],
                "Toss": [t if t in TOSSES else None for t in mv["toss_label"]],
                "Innings": [i if i in INNINGS else None for i in mv["innings_label"]],
                "Format": [f or None for f in mv["format"]],
                "Overs": pd.to_numeric(mv["overs"], errors="coerce"),
                "Ball": [b if b in BALLS else None for b in mv["ball_label"]],
                "Us": mv["our_score"], "Them": mv["opp_score"], "Scorecard result": mv["result"],
            }).reset_index(drop=True)
            ids = mv["match_id"].tolist()
            st.caption("Opponent, venue, result, toss, innings, format, overs and ball can be corrected here: "
                       "edit the cells, then press Save.")
            edit_cols = {"Opponent": "opponent", "Venue": "venue", "Result": "outcome", "Toss": "toss_result",
                         "Innings": "innings", "Format": "format", "Overs": "overs", "Ball": "ball"}
            edited = st.data_editor(
                table, hide_index=True, width="stretch", key=f"m_ed_{short_hash('|'.join(ids))}",
                disabled=[c for c in table.columns if c not in edit_cols],
                column_config={
                    "Venue": st.column_config.SelectboxColumn("Venue", options=list(VENUES)),
                    "Result": st.column_config.SelectboxColumn("Result", options=list(OUTCOMES)),
                    "Toss": st.column_config.SelectboxColumn("Toss", options=list(TOSSES)),
                    "Innings": st.column_config.SelectboxColumn("Innings", options=list(INNINGS)),
                    "Format": st.column_config.SelectboxColumn(
                        "Format", options=list(dict.fromkeys(FORMAT_CHOICES + known_formats))),
                    "Overs": st.column_config.NumberColumn("Overs", min_value=1, max_value=200, step=1),
                    "Ball": st.column_config.SelectboxColumn("Ball", options=list(BALLS))})
            changes = {}
            for i, mid in enumerate(ids):
                for col, field in edit_cols.items():
                    old, new = table.at[i, col], edited.at[i, col]
                    if col in ("Opponent", "Toss", "Innings") and (not normal[i] or not str(new or "").strip()):
                        continue
                    if pd.notna(new) and not (pd.notna(old) and new == old) and str(new).strip():
                        changes.setdefault(mid, {})[field] = (str(int(new)) if col == "Overs"
                                                              else str(new).strip())
            n_changes = sum(len(v) for v in changes.values())
            if st.button(f"💾  Save {n_changes} change(s)", disabled=not changes, key="m_ed_save"):
                db.update_matches(changes)
                st.session_state["ball_done"] = f"Updated {len(changes)} match(es)."
                st.rerun()
        st.divider()
        if st.button("⬇️  Build Excel export"):
            out = "/tmp/uol-qhpc-cricket-db.xlsx"
            db.export_excel(out)
            with open(out, "rb") as f:
                st.download_button("Download uol-qhpc-cricket-db.xlsx", f.read(),
                                   file_name="uol-qhpc-cricket-db.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------- Manage ----------------
GROUPS_PER_PAGE = 8


@st.fragment
def duplicate_scan(matches, bat_all, bowl_all):
    st.subheader("Duplicate matches")
    st.caption("Finds the same match saved twice — e.g. once from the PDF and once from a screenshot. "
               "Check the proof, then choose which copy to keep. Nothing is deleted until you confirm.")
    flash("dup_done")
    if len(matches) < 2:
        st.caption("Fewer than two matches saved.")
        return
    ignored = tuple(sorted(tuple(sorted(p)) for p in db.dup_ignored()))
    found = dup_pairs(matches, bat_all, ignored)
    if not found:
        st.success("No duplicate matches found 👍")
        return
    st.markdown(f"**{len(found)} possible duplicate(s) found.**")
    for d in found:
        a, b = d["a"], d["b"]
        ida, idb = str(a["match_id"]), str(b["match_id"])
        k = short_hash(f"{ida}|{idb}|{d['level']}")
        la, lb = f"Match 1 · {match_source(ida, a['date'])}", f"Match 2 · {match_source(idb, b['date'])}"
        ba, bb = bat_all[bat_all["match_id"] == ida], bat_all[bat_all["match_id"] == idb]
        wa, wb = bowl_all[bowl_all["match_id"] == ida], bowl_all[bowl_all["match_id"] == idb]
        with st.container(border=True):
            st.markdown(f"**{DUP_LABELS[d['level']]}** — {a['team_a']} vs {a['team_b']}")
            show_proof(a, b, la, lb, ba, bb, d["reasons"], wa, wb)
            if ida == idb:
                st.caption("This match was saved twice under the same ID. Removing the extra copy keeps one "
                           "copy of every row.")
                sure = st.checkbox("I've checked the proof — remove the extra copy", key=f"dup_sure_{k}")
                if st.button("🗑  Remove the extra copy", key=f"dup_go_{k}", disabled=not sure):
                    db.remove_double_save(ida)
                    st.session_state["dup_done"] = f"Removed the extra copy of match {ida}."
                    st.rerun()
                continue
            more_a = len(ba) + len(wa) >= len(bb) + len(wb)
            keep = st.radio(
                "Which copy is right?", ["a", "b", "both"], index=0 if more_a else 1, horizontal=True,
                key=f"dup_keep_{k}",
                format_func={"a": f"Keep Match 1 (ID {ida}, {len(ba)} batters)",
                             "b": f"Keep Match 2 (ID {idb}, {len(bb)} batters)",
                             "both": "Not duplicates — keep both"}.get)
            if keep == "both":
                if st.button("✋  Not duplicates — stop flagging these two", key=f"dup_no_{k}"):
                    db.add_dup_ignore(ida, idb)
                    st.session_state["dup_done"] = "Noted — these two matches won't be flagged again."
                    st.rerun()
            else:
                drop, drop_label = (idb, "Match 2") if keep == "a" else (ida, "Match 1")
                sure = st.checkbox(f"I've checked the proof — delete {drop_label} (ID {drop}) and its stats",
                                   key=f"dup_sure_{k}")
                if st.button(f"🗑  Delete {drop_label}", key=f"dup_go_{k}", disabled=not sure):
                    db.delete_match(drop)
                    st.session_state["dup_done"] = f"Deleted {drop_label} (ID {drop}). The other copy is kept."
                    st.rerun()


@st.fragment
def similar_names(players, bat_all, bowl_all):
    st.subheader("Similar names")
    st.caption("Spellings that look like the same player. Untick any name that doesn't belong, "
               "choose the spelling to keep and merge — or mark the group as different players "
               "so it isn't suggested again.")
    flash("sim_done")
    if len(players) < 2:
        st.info("Not enough players yet to look for duplicates.")
        return

    ignored = db.ignored_pairs()
    if st.session_state.pop("sim_undo_reset", False):
        st.session_state.pop("sim_undo", None)
    with st.expander("Settings and names marked as different"):
        thr = st.slider("How alike names must be", 0.70, 0.95, 0.85, 0.01, key="sim_thr",
                        help="Lower finds more possible matches (and more false alarms).")
        dismissed = sorted(tuple(sorted(p)) for p in ignored)
        if dismissed:
            labels = [f"{a}  ≠  {b}" for a, b in dismissed]
            undo = st.multiselect("Marked as different players", labels, key="sim_undo",
                                  placeholder="Pick pairs to suggest again")
            if st.button("↩️  Suggest these again", disabled=not undo, key="sim_undo_btn"):
                db.remove_ignored_pairs([dismissed[labels.index(u)] for u in undo])
                st.session_state["sim_done"] = f"{len(undo)} pair(s) will be suggested again."
                st.session_state["sim_undo_reset"] = True
                st.rerun()
        else:
            st.caption("Nothing marked as different yet.")

    groups = name_groups(tuple(players), tuple(sorted(tuple(sorted(p)) for p in ignored)), thr)
    if not groups:
        st.success("No look-alike names right now 👍")
        return

    details = name_details(bat_all, bowl_all)
    shown = st.session_state.get("sim_show", GROUPS_PER_PAGE)
    st.markdown(f"**{len(groups)} possible group(s)** — most entries first in each group.")
    for i, group in enumerate(groups[:shown]):
        k = short_hash("|".join(group))
        group = sorted(group, key=lambda n: -details.get(n, (0, ""))[0])
        with st.container(border=True):
            st.markdown(f"**Group {i + 1}**")
            ticked = [n for n in group
                      if st.checkbox(f"**{n}** — {details.get(n, (0, ''))[1]}", value=True,
                                     key=f"sim_cb_{k}_{short_hash(n)}")]
            canonical = ""
            if len(ticked) >= 2:
                c1, c2 = st.columns(2)
                keep = c1.selectbox("Keep this spelling", ticked,
                                    key=f"sim_keep_{k}_{short_hash('|'.join(ticked))}")
                typed = c2.text_input("…or type the correct name", key=f"sim_typed_{k}",
                                      placeholder="optional")
                canonical = typed.strip() or keep
            else:
                st.caption("Tick at least two names to merge them.")
            b1, b2, _ = st.columns([1, 1, 2])
            if b1.button("🔗  Merge", type="primary", key=f"sim_merge_{k}", disabled=len(ticked) < 2):
                n = db.rename_player(ticked, canonical)
                left_out = [x for x in group if x not in ticked]
                if left_out:   # unticked = a different player, don't pair it with this one again
                    db.add_ignored_pairs([(x, canonical) for x in left_out])
                st.session_state["sim_done"] = (f"Merged {', '.join(ticked)} into '{canonical}' "
                                                f"({n} entries updated).")
                st.rerun()
            if b2.button("✋  Not the same player", key=f"sim_no_{k}"):
                db.add_ignored_pairs(list(combinations(group, 2)))
                st.session_state["sim_done"] = "Noted — these names won't be suggested together again."
                st.rerun()
    if len(groups) > shown:
        st.button(f"Show more ({len(groups) - shown} more group(s))", key="sim_more",
                  on_click=lambda: st.session_state.update(sim_show=shown + GROUPS_PER_PAGE))


@st.fragment
def squad_section(players):
    st.subheader("Squad list — full names")
    st.caption("Your squad list lives in your Google Sheet: tab **squad**, column **Full name**, one player per "
               "row. Add new admissions there — the app reads it within 2 minutes, or press Refresh.")
    flash("squad_done")
    squad, sp = db.squad_names(), db.spellings()
    c1, c2 = st.columns([3, 1], vertical_alignment="center")
    c1.markdown(f"**{len(squad)}** players in the squad list · **{len(sp)}** remembered spellings "
                "(tab **spellings** — you can edit them there)")
    if c2.button("🔄  Refresh", key="squad_refresh_m"):
        db.clear_cache()
        st.rerun()
    if not squad:
        st.info("The squad tab is empty. Add your players' full names to it, then press Refresh.")
        return
    todo = []
    for p in players:
        if p in squad:
            continue
        r = resolve_name(p, squad, sp)
        if r["status"] == "remembered" and r["full"] == p:       # a guest you've already marked
            continue
        todo.append((p, r))
    if not todo:
        st.success("Every saved player name is a full name from the squad list 👍")
        return
    st.markdown(f"**{len(todo)} saved name(s) aren't from the squad list yet.** Pick the right player, "
                "then press Apply — all their stats move to the full name.")
    sig = short_hash("|".join(squad))
    known = {x.lower(): x for x in squad}
    pairs, remember, new_players, guests = {}, {}, [], 0
    for p, r in todo:
        opts = list(dict.fromkeys(r["options"] + squad))
        pre = r["full"] if r["status"] != "ask" else r.get("default")
        a, b = st.columns([2, 3], vertical_alignment="center")
        a.markdown(f"**{p}**")
        a.caption(r["reason"])
        kind, full = pick_player(p, opts, opts.index(pre) if pre in opts else None, f"link_{short_hash(p)}_{sig}", b)
        if kind == "guest":
            remember[p] = (p, False)
            guests += 1
        elif kind in ("squad", "new"):
            if kind == "new":
                if full.lower() in known:
                    full = known[full.lower()]
                elif full not in new_players:
                    new_players.append(full)
            pairs[p] = full
            remember[p] = (full, True)
    n_chosen = len(remember)
    if st.button(f"✅  Apply {n_chosen} name(s)", type="primary", disabled=not n_chosen, key="link_apply"):
        import gspread
        try:
            with st.spinner("Updating saved matches…"):
                db.relink_names(pairs, remember, new_players)
        except gspread.exceptions.APIError:
            st.error("Google Sheets is busy right now (too many changes in one minute). Wait a minute, then "
                     "press Apply again — names already linked stay linked.")
            return
        st.session_state["squad_done"] = (
            f"Linked {len(pairs)} name(s) to the squad list"
            + (f", added {len(new_players)} new player(s) to it" if new_players else "")
            + (f" and marked {guests} as guests" if guests else "") + ".")
        st.rerun()


@st.fragment
def opponent_names(matches):
    st.subheader("Opposition names")
    st.caption("Keep one name per team so head-to-head records stay together. Renaming updates "
               "every saved match that uses that name.")
    flash("opp_done")
    ours = matches[matches["our_side"].isin(["A", "B"])]
    counts = ours.groupby("opponent_label").size()
    names = sorted((n for n in counts.index if n), key=str.lower)
    if not names:
        st.caption("No opposition teams yet.")
        return

    def rename(old_names, new):
        ids = ours.loc[ours["opponent_label"].isin(old_names), "match_id"]
        db.update_matches({mid: {"opponent": new} for mid in ids})
        st.session_state["opp_done"] = f"Renamed {', '.join(old_names)} → '{new}' ({len(ids)} match(es))."
        st.rerun()

    for g in name_clusters(names):
        k = short_hash("|".join(g))
        with st.container(border=True):
            st.markdown("**Looks like the same team:** " + ", ".join(f"{n} ({counts[n]})" for n in g))
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            keep = c1.selectbox("Use this name", sorted(g, key=lambda n: -counts[n]), key=f"oppkeep_{k}")
            if c2.button("🔗  Merge", key=f"oppmerge_{k}", type="primary"):
                rename([n for n in g if n != keep], keep)

    st.markdown("**Rename a team**")
    c1, c2, c3 = st.columns([3, 3, 1], vertical_alignment="bottom")
    if "opp_sel" in st.session_state:
        st.session_state["opp_sel"] = [n for n in st.session_state["opp_sel"] if n in names]
    sel = c1.multiselect("Current name(s)", names, key="opp_sel", format_func=lambda n: f"{n} ({counts[n]})")
    new = c2.text_input("New name", key=f"opp_new_{short_hash('|'.join(sel))}",
                        value=sel[0] if len(sel) == 1 else "")
    if c3.button("✏️  Rename", key="opp_go", disabled=not (sel and new.strip())):
        rename(sel, new.strip())


@st.fragment
def bowling_type_editor(bowl_all, types):
    st.subheader("Bowling types")
    st.caption("Pace or spin for each of your bowlers — used by the Bowling type filter. "
               "Bowlers without a type are listed first.")
    flash("bt_done")
    bowlers = sorted(set(bowl_all["player"]), key=lambda b: (b in types, b.lower()))
    if not bowlers:
        st.caption("No bowlers yet.")
        return
    if st.session_state.pop("bt_reset", False):
        st.session_state.pop("bt_editor", None)
    inns = bowl_all["player"].value_counts()
    df = pd.DataFrame({"Player": bowlers, "Type": [types.get(b) for b in bowlers],
                       "Innings": [int(inns.get(b, 0)) for b in bowlers]})
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key="bt_editor", height=min(38 + 35 * len(df), 420),
        column_config={"Player": st.column_config.TextColumn(disabled=True),
                       "Type": st.column_config.SelectboxColumn(options=["Pace", "Spin"]),
                       "Innings": st.column_config.NumberColumn(disabled=True, help="bowling innings")})
    picked = {r.Player: (r.Type if r.Type in ("Pace", "Spin") else None) for r in edited.itertuples()}
    changes = {p: (t or "") for p, t in picked.items() if t != types.get(p)}
    missing = int(edited["Type"].isna().sum())
    if missing:
        st.caption(f"{missing} bowler(s) without a type — they show as “{NOT_SET}”.")
    if st.button(f"💾  Save {len(changes)} change(s)", type="primary", disabled=not changes, key="bt_save"):
        db.set_bowling_types(changes)
        st.session_state["bt_done"] = f"Saved bowling type for {len(changes)} bowler(s)."
        st.session_state["bt_reset"] = True
        st.rerun()


@st.fragment
def merge_full_list(players, bat_all, bowl_all):
    st.subheader("Merge from the full name list")
    st.caption("Every name at once. Tick the ones that are the same player, "
               "choose the spelling to keep, and merge.")
    if not players:
        st.caption("No players yet.")
        return
    if st.session_state.pop("allnames_reset", False) and "allnames_editor" in st.session_state:
        del st.session_state["allnames_editor"]
    flash("allnames_done")

    details = name_details(bat_all, bowl_all)
    names_df = pd.DataFrame({"Merge?": False, "Player": players})
    names_df["Entries"] = names_df["Player"].map(lambda p: details.get(p, (0, ""))[0])
    edited = st.data_editor(
        names_df, hide_index=True, width="stretch", height=420, key="allnames_editor",
        column_config={
            "Merge?": st.column_config.CheckboxColumn("Merge?", width="small"),
            "Player": st.column_config.TextColumn("Player", disabled=True),
            "Entries": st.column_config.NumberColumn("Entries", disabled=True,
                                                     help="batting + bowling innings on record"),
        })
    picked = edited[edited["Merge?"]]["Player"].tolist()
    if picked:
        st.write("**Selected:** " + ", ".join(picked))
        canon3 = st.selectbox("Keep this spelling", picked, key=f"allnames_keep_{short_hash('|'.join(picked))}")
        typed3 = st.text_input("…or type the correct name instead (optional)", key="allnames_typed")
        if typed3.strip():
            canon3 = typed3.strip()
        if st.button("🔗  Merge these names", type="primary",
                     disabled=len(picked) < 2, key="allnames_merge"):
            n = db.rename_player(picked, canon3)
            st.session_state["allnames_done"] = f"Merged {len(picked)} names into '{canon3}' ({n} entries updated)."
            st.session_state["allnames_reset"] = True
            st.rerun()


if IS_ADMIN:
    with TABS["manage"]:
        squad_section(players)
        st.divider()
        duplicate_scan(matches, bat_all, bowl_all)
        st.divider()
        opponent_names(matches)
        st.divider()
        bowling_type_editor(bowl_all, types)
        st.divider()
        similar_names(players, bat_all, bowl_all)
        st.divider()
        merge_full_list(players, bat_all, bowl_all)

        st.divider()
        st.subheader("Delete a match")
        flash("del_done")
        if matches.empty:
            st.caption("No matches saved.")
        else:
            md = matches.sort_values("date_dt", ascending=False, na_position="last")
            opts = {f"{r['date'] or 'no date'} · {r['team_a']} vs {r['team_b']}  ({r['match_id']})": r["match_id"]
                    for _, r in md.iterrows()}
            pick = st.selectbox("Match to delete", list(opts.keys()))
            sure = st.checkbox("Yes — delete this match and all its stats")
            if st.button("🗑  Delete match", disabled=not sure):
                db.delete_match(opts[pick])
                st.session_state["del_done"] = "Match deleted."
                st.rerun()

        st.divider()
        st.subheader("Remove other teams' stats")
        flash("keepteams_done")
        if not all_teams:
            st.caption("No team data.")
        else:
            st.caption("Keeps the batting and bowling of the teams you tick as yours and deletes every "
                       "other team's. Match results stay in the Matches list.")
            rows = pd.concat([bat_all[["team", "player"]].assign(kind="bat"),
                              bowl_all[["team", "player"]].assign(kind="bowl")])
            counts = rows.groupby("team").agg(
                Batting=("kind", lambda k: int((k == "bat").sum())),
                Bowling=("kind", lambda k: int((k == "bowl").sum())),
                Players=("player", "nunique"))
            label = lambda t: t or "(no team name)"
            keep_sel = st.multiselect(
                "Your team(s) — keep these", all_teams, default=[t for t in all_teams if t not in other_teams],
                format_func=label, key=f"keep_teams_{short_hash('|'.join(our_names) + '|'.join(all_teams))}")
            remove = [t for t in all_teams if t not in keep_sel]
            if not keep_sel:
                st.warning("Tick at least one team to keep.")
            elif not remove:
                st.success("Only your team's stats are saved 👍")
            else:
                st.markdown(f"**Will delete stats for {len(remove)} team(s):**")
                st.dataframe(counts.loc[remove].rename(index=label).rename_axis("Team").reset_index(),
                             hide_index=True, width="stretch")
                sure2 = st.checkbox("Yes — delete these teams' player stats", key="keep_teams_sure")
                if st.button("🗑  Delete other teams' stats", disabled=not sure2):
                    n = db.keep_only_teams(keep_sel)
                    st.session_state["keepteams_done"] = f"Removed {n} rows from {len(remove)} other team(s)."
                    st.rerun()


# ---------------- Data health ----------------
if IS_ADMIN:
    with TABS["health"]:
        n_fix = sum(1 for t, k, w, rows in HEALTH if k == "fix" and not rows.empty)
        n_check = sum(1 for t, k, w, rows in HEALTH if k == "check" and not rows.empty)
        ui.cards([("To fix", n_fix, "wrong or missing data"), ("To check", n_check, "worth a quick look"),
                  ("All clear", len(HEALTH) - n_fix - n_check, f"of {len(HEALTH)} checks")])
        st.caption("Checks run on all saved data (the sidebar filters don't apply here). "
                   "Fix things where each card says, then come back — the count updates by itself.")
        order = sorted(HEALTH, key=lambda c: (c[3].empty, c[1] != "fix"))
        for title, kind, where, rows in order:
            if rows.empty:
                st.markdown(f"✅ &nbsp;{title}")
                continue
            icon = "🔴" if kind == "fix" else "🟡"
            with st.expander(f"{icon}  {title} ({len(rows)})", expanded=kind == "fix"):
                st.caption(f"Where to fix: {where}")
                st.dataframe(ui.styled(rows), hide_index=True, width="stretch",
                             height=min(38 + 35 * len(rows), 360))


ui.footer(matches["date_dt"].max() if not matches.empty else None)
