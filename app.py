import io
import time
import hmac
import hashlib
import datetime as dt
from itertools import combinations

import pandas as pd
import streamlit as st

import db
import ui
from parser import parse_stumps_pdf, parse_summary_image
from stats import (NO_FORMAT, NOT_SET, BALLS, guess_ball, guess_type, career_batting, career_bowling, batting_by_position, player_overview,
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
types = db.bowling_types()
bowl_all["bowl_type"] = bowl_all["player"].map(types).fillna(NOT_SET)

# ---------------- Sidebar: store status + filters ----------------
PERIODS = ["All time", "Last 7 days", "Last 30 days", "Last 3 months", "This year", "Custom range"]
FILTER_KEYS = ["f_period", "f_range", "f_undated", "f_ball", "f_formats", "f_pos", "f_btype"]


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

    m_view = filter_rows(matches, date_range, include_undated, fmt_sel, balls=ball_sel)
    if date_range or ball_sel or fmt_sel or pos_sel or btype_sel:
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
bat_base = filter_rows(bat_all, date_range, include_undated, fmt_sel, balls=ball_sel)
bat_f = filter_rows(bat_base, positions=pos_sel)
bowl_f = filter_rows(bowl_all, date_range, include_undated, fmt_sel, bowl_types=btype_sel, balls=ball_sel)
field_f = filter_rows(field_all, date_range, include_undated, fmt_sel, balls=ball_sel)

note_parts = []
if date_range:
    note_parts.append(f"{date_range[0]:%d %b %Y} – {date_range[1]:%d %b %Y}")
if ball_sel:
    note_parts.append(" & ".join(ball_sel))
if fmt_sel:
    note_parts.append(", ".join(fmt_sel))
if pos_sel:
    note_parts.append("batting at " + ", ".join(f"No. {p}" for p in pos_sel))
if btype_sel:
    note_parts.append(" & ".join(btype_sel) + " bowlers")
FILTER_NOTE = ("Filtered: " + " · ".join(note_parts)) if note_parts else ""
unknown_pos = int(bat_base["position"].isna().sum()) if pos_sel else 0


def show_filter_note(batting=True):
    if FILTER_NOTE:
        ui.note(FILTER_NOTE)
    if batting and unknown_pos:
        st.caption(f"{unknown_pos} innings from screenshot uploads have no batting position, "
                   "so they're left out while a position is picked.")


ui.masthead(len(matches), len(players), matches["date_dt"].max() if not matches.empty else None)
TAB_LABELS = {"board": "📊  Leaderboards", "search": "🔎  Player search"}
if IS_ADMIN:
    TAB_LABELS.update({"add": "➕  Add matches", "matches": "📋  Matches", "manage": "🛠  Manage"})
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
                    side = ball = None
                    if action in ("new", "replace"):
                        default = default_side(mt["team_a"], mt["team_b"], our_names)
                        side = ctrl.selectbox(
                            f"Your team in {name}", list(labels), format_func=labels.get,
                            index=list(labels).index(default) if default else None,
                            key=f"side_{fk}_{short_hash(names_sig)}",
                            placeholder="Pick your side", label_visibility="collapsed")
                        if side in ("A", "B", "AB"):
                            guess = guess_ball(mt.get("format"), mt.get("overs"))
                            ball = ctrl.selectbox(
                                f"Ball for {name}", list(BALLS), key=f"ball_{fk}",
                                index=BALLS.index(guess) if guess else None,
                                placeholder="White ball or red ball?", label_visibility="collapsed")
                            _, b, w, _f = db.rows_for(p, set(side))
                            ctrl.caption((f"⚠️ Pick white or red ball · " if not ball else
                                          (f"{ball} (from format “{mt.get('format')}”) · " if guess else ""))
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
                choices.append((c, action, side, ball))
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

            live = [(c, action, side, ball) for c, action, side, ball in choices if action in ("new", "replace")]
            pending = sum(1 for _, _, side, _b in live if side is None)
            no_ball = sum(1 for _, _, side, ball in live if side in ("A", "B", "AB") and not ball)
            to_save = []
            for c, _, side, ball in live:
                if side in ("A", "B", "AB"):
                    c["p"]["meta"]["ball"] = ball or ""
                    to_save.append((c["p"], set(side)))
            to_replace = sorted({str(c["saved"]["match_id"]) for c, action, side, _b in live
                                 if action == "replace" and side in ("A", "B", "AB")})
            if pending:
                st.warning(f"Pick your side for {pending} match(es) — or choose “Skip this match”.")
            if no_ball:
                st.warning(f"Pick white ball or red ball for {no_ball} match(es).")

            # ---- your bowlers with no bowling type yet: ask once per player, pace or spin
            new_bowlers = sorted({r["player"] for p, sides in to_save for r in db.rows_for(p, sides)[2]
                                  if r["player"] not in types})
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
                                   disabled=bool(pending) or bool(no_ball) or bool(missing_types) or not to_save):
                with st.spinner(f"Saving {len(to_save)} match(es) to your sheet…"):
                    if bowl_choice:
                        db.set_bowling_types(bowl_choice)
                    for mid in to_replace:
                        db.delete_match(mid)
                    nb, nw = db.append_many(to_save)
                skipped = len(cards) - len(to_save)
                st.session_state["add_done"] = (
                    f"Saved {len(to_save)} match(es): {nb} batting and {nw} bowling entries for your team."
                    + (f" Replaced {len(to_replace)} saved copy(ies)." if to_replace else "")
                    + (f" Skipped {skipped}." if skipped else ""))
                st.session_state.parsed = {}
                st.session_state["up_n"] = st.session_state.get("up_n", 0) + 1  # empties the uploader
                st.rerun()
            elif cards and not to_save and not pending:
                st.caption("Nothing to save — every file is skipped.")

        with st.expander("Add a match by hand (fallback if a screenshot won't read)"):
            st.caption("Enter your team's players only. Leave blanks as 0. Enter batters in batting order.")
            mc1, mc2 = st.columns(2)
            ta = mc1.text_input("Your team", value=our_names[0] if our_names else "", key="m_ta")
            sa = mc2.text_input("Your score", key="m_sa", placeholder="e.g. 190-6 in 25.0 overs")
            tb = mc1.text_input("Opponent", key="m_tb")
            sb = mc2.text_input("Opponent score", key="m_sb")
            res = st.text_input("Result", key="m_res", placeholder="e.g. UOL QHPC won by 15 runs")
            mc3, mc4, mc5 = st.columns(3)
            m_date = mc3.date_input("Date", value=today, key="m_date", format="DD/MM/YYYY")
            known_formats = [f for f in formats if f != NO_FORMAT]
            m_format = mc4.selectbox("Match format", known_formats + ["Other…"], key="m_format",
                                     index=None, placeholder="Pick or choose Other…")
            if m_format == "Other…":
                m_format = mc4.text_input("Type the format", key="m_format_typed")
            m_overs = mc5.text_input("Overs per side", key="m_overs", placeholder="e.g. 20")
            m_ball = st.radio("Ball", list(BALLS), index=None, horizontal=True, key="m_ball")

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
                              "overs": m_overs.strip(), "result": res, "toss": "", "team_a": team,
                              "ball": m_ball or "",
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
                elif not m_ball:
                    st.warning("Pick white ball or red ball.")
                elif no_type:
                    st.warning(f"Pick pace or spin for new bowler(s): {', '.join(no_type)}.")
                else:
                    if typed_types:
                        db.set_bowling_types(typed_types)
                    nb, nbw = db.append_match(p, {"A"})
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

    view = st.radio("Show", ["Batting", "Bowling"], horizontal=True, key=f"pv_{short_hash(name)}",
                    label_visibility="collapsed")
    if view == "Batting":
        if pb.empty:
            st.caption("No batting innings under the current filters.")
            return
        r = career_batting(pb, fielding=field_f).iloc[0]
        for col, (lbl, v) in zip(st.columns(8), [
                ("Inns", r["Inns"]), ("Runs", r["Runs"]), ("HS", r["HS"]), ("Avg", dash(r["Avg"])),
                ("SR", dash(r["SR"])), ("50s", r["50s"]), ("100s", r["100s"]), ("Catches", r["Catches"])]):
            col.metric(lbl, v)
        fd = field_f.loc[field_f["player"] == name, "dismissal"].value_counts()
        if fd.sum():
            st.caption("Fielding: " + " · ".join(
                f"{int(fd.get(k, 0))} {one if int(fd.get(k, 0)) == 1 else many}" for k, one, many in [
                    ("caught", "catch", "catches"), ("caught & bowled", "caught & bowled", "caught & bowled"),
                    ("stumped", "stumping", "stumpings"), ("run out", "run-out", "run-outs")]
                if fd.get(k, 0)))
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
        st.dataframe(ui.styled(pd.DataFrame({
            "Date": fmt_date(mbm["date_dt"]), "Vs": mbm["opponent"], "Ball": mbm["ball_label"],
            "Format": mbm["format_label"], "Pos": mbm["position"], "How out": mbm["how_out"], "R": mbm["runs"], "B": mbm["balls"],
            "4s": mbm["fours"], "6s": mbm["sixes"], "SR": mbm["sr"]})),
            hide_index=True, width="stretch")
    else:
        if pw.empty:
            st.caption("No bowling innings under the current filters.")
            return
        r = career_bowling(pw).iloc[0]
        st.caption(f"Bowling type: **{r['Type']}**")
        for col, (lbl, v) in zip(st.columns(7), [
                ("Inns", r["Inns"]), ("Overs", r["Overs"]), ("Wkts", r["Wkts"]), ("Best", r["Best"]),
                ("Avg", dash(r["Avg"])), ("Econ", dash(r["Econ"])), ("3W+", r["3W+"])]):
            col.metric(lbl, v)
        st.markdown("**Match by match**")
        mbm = pw.sort_values("date_dt", ascending=False, na_position="last")
        st.dataframe(ui.styled(pd.DataFrame({
            "Date": fmt_date(mbm["date_dt"]), "Vs": mbm["opponent"], "Ball": mbm["ball_label"],
            "Format": mbm["format_label"], "O": mbm["overs"], "M": mbm["maidens"], "R": mbm["runs"], "W": mbm["wickets"],
            "Econ": mbm["econ"], "Wd": mbm["wides"], "NB": mbm["no_balls"]})),
            hide_index=True, width="stretch")


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
    st.dataframe(ui.styled(found), hide_index=True, width="stretch", height=min(38 + 35 * len(found), 320),
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
    st.dataframe(ui.styled(table, top=0 if q.strip() else 3), hide_index=True, width="stretch",
                 height=min(38 + 35 * len(table), 640),
                 column_config={"#": st.column_config.NumberColumn("#", width="small")})
    st.caption(f"{len(table)} player(s) · ranked by {'runs' if which == 'Batting' else 'wickets'}. "
               "Click a column header to sort.")


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
            table = pd.DataFrame({
                "Date": fmt_date(mv["date_dt"]),
                "Ball": [b if b in BALLS else None for b in mv["ball_label"]],
                "Format": mv["format_label"], "Overs": mv["overs"],
                "Team A": mv["team_a"], "Score A": mv["score_a"], "Team B": mv["team_b"],
                "Score B": mv["score_b"], "Result": mv["result"]}).reset_index(drop=True)
            ids = mv["match_id"].tolist()
            st.caption("You can change **Ball** here — pick a value in the column, then save.")
            edited = st.data_editor(
                table, hide_index=True, width="stretch", key=f"ball_ed_{short_hash('|'.join(ids))}",
                disabled=[c for c in table.columns if c != "Ball"],
                column_config={"Ball": st.column_config.SelectboxColumn("Ball", options=list(BALLS))})
            ball_changes = {mid: new for mid, old, new in zip(ids, table["Ball"], edited["Ball"])
                            if new in BALLS and new != old}
            if st.button(f"💾  Save {len(ball_changes)} ball change(s)", disabled=not ball_changes,
                         key="ball_save"):
                db.set_match_ball(ball_changes)
                st.session_state["ball_done"] = f"Updated ball type for {len(ball_changes)} match(es)."
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
        duplicate_scan(matches, bat_all, bowl_all)
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


ui.footer(matches["date_dt"].max() if not matches.empty else None)
