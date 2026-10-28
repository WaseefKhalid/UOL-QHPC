import io
import time
import hashlib
import datetime as dt
from itertools import combinations

import pandas as pd
import streamlit as st

import db
from parser import parse_stumps_pdf, parse_summary_image
from stats import (NO_FORMAT, career_batting, career_bowling, batting_by_position, player_overview,
                   enrich, filter_rows, suggest_name_groups, search_names)

st.set_page_config(page_title="QHPC Cricket Stats", page_icon="🏏", layout="wide")

if "our_team" not in st.session_state:
    st.session_state.our_team = "UOL QHPC"


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
def prepared(m_df, bat_df, bowl_df):
    return enrich(m_df, bat_df, bowl_df)


@st.cache_data(show_spinner=False)
def name_groups(names, ignored, threshold):
    return suggest_name_groups(list(names), threshold, ignore=ignored)


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
bat_raw, bowl_raw = db.load("batting"), db.load("bowling")
matches, bat_all, bowl_all = prepared(m_df, bat_raw, bowl_raw)
players = sorted(set(bat_all["player"]) | set(bowl_all["player"]))

# ---------------- Sidebar: store status + filters ----------------
PERIODS = ["All time", "Last 7 days", "Last 30 days", "Last 3 months", "This year", "Custom range"]
FILTER_KEYS = ["f_period", "f_range", "f_undated", "f_formats", "f_pos"]


def reset_filters():
    for k in FILTER_KEYS:
        st.session_state.pop(k, None)


with st.sidebar:
    st.header("🏏 QHPC Stats")
    st.session_state.our_team = st.text_input(
        "Our team name", st.session_state.our_team,
        help="Used to tag our players and to filter inter-academy matches.")
    online = db.use_sheets()
    st.caption(("🟢 " if online else "🟡 ") + "Data store: " + db.backend_label())
    if not online:
        st.caption("Add Google Sheets secrets to make saved data permanent online (see README).")
    c1, c2 = st.columns(2)
    c1.metric("Matches", len(matches))
    c2.metric("Players", len(players))

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

    m_view = filter_rows(matches, date_range, include_undated, fmt_sel)
    if date_range or fmt_sel or pos_sel:
        st.caption(f"Showing **{len(m_view)}** of {len(matches)} matches")
        st.button("↺  Reset filters", on_click=reset_filters)

# rows after filters: catches are counted before the position filter (a catch has nothing
# to do with where the catcher batted)
bat_base = filter_rows(bat_all, date_range, include_undated, fmt_sel)
bat_f = filter_rows(bat_base, positions=pos_sel)
bowl_f = filter_rows(bowl_all, date_range, include_undated, fmt_sel)

note_parts = []
if date_range:
    note_parts.append(f"{date_range[0]:%d %b %Y} – {date_range[1]:%d %b %Y}")
if fmt_sel:
    note_parts.append(", ".join(fmt_sel))
if pos_sel:
    note_parts.append("batting at " + ", ".join(f"No. {p}" for p in pos_sel))
FILTER_NOTE = ("🔽 Filtered: " + " · ".join(note_parts) + " — change in the sidebar.") if note_parts else ""
unknown_pos = int(bat_base["position"].isna().sum()) if pos_sel else 0


def show_filter_note(batting=True):
    if FILTER_NOTE:
        st.caption(FILTER_NOTE)
    if batting and unknown_pos:
        st.caption(f"{unknown_pos} innings from screenshot uploads have no batting position, "
                   "so they're left out while a position is picked.")


st.title("QHPC Cricket Stats")
tab_add, tab_search, tab_board, tab_matches, tab_manage = st.tabs(
    ["➕  Add matches", "🔎  Player search", "📊  Leaderboards", "📋  Matches", "🛠  Manage"])

# ---------------- Add matches ----------------
with tab_add:
    flash("add_done", balloons=True)
    up_key = f"uploads_{st.session_state.get('up_n', 0)}"
    ups = st.file_uploader(
        "Drop in one or more files — STUMPS PDFs and/or summary screenshots",
        type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key=up_key)

    files = sorted(ups or [], key=lambda u: not u.name.lower().endswith(".pdf"))  # PDFs first
    if files:
        parsed = st.session_state.setdefault("parsed", {})
        existing = set(matches["match_id"])
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

        overview = []
        for name, is_pdf, u, p in items:
            kind = "PDF" if is_pdf else "Screenshot"
            if "error" in p:
                overview.append({"File": name, "Type": kind, "Match": "could not read",
                                 "Status": str(p["error"])[:40]})
                continue
            mt = p["meta"]
            overview.append({"File": name, "Type": kind,
                             "Match": f'{mt["team_a"]} vs {mt["team_b"]}',
                             "Format": mt.get("format") or "—",
                             "Read": f'{len(p["batting"])} bat / {len(p["bowling"])} bowl',
                             "Status": "already saved" if str(mt["match_id"]) in existing else "new"})
        st.dataframe(pd.DataFrame(overview), hide_index=True, width="stretch")

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

        our_side = st.session_state.our_team
        if st.button("✅  Save all new matches", type="primary"):
            to_save, seen, skipped, errs = [], set(), 0, 0
            for name, is_pdf, u, p in items:
                if "error" in p:
                    errs += 1
                    continue
                mid = str(p["meta"]["match_id"])
                if mid in existing or mid in seen:
                    skipped += 1
                    continue
                seen.add(mid)
                to_save.append(p)
            if to_save:
                with st.spinner(f"Saving {len(to_save)} match(es) to your sheet…"):
                    db.append_many(to_save, "intra", our_side)
            msg = f"Saved {len(to_save)} new match(es)."
            if skipped:
                msg += f" Skipped {skipped} already saved."
            if errs:
                msg += f" {errs} couldn't be read."
            st.session_state["add_done"] = msg
            st.session_state.parsed = {}
            st.session_state["up_n"] = st.session_state.get("up_n", 0) + 1  # empties the uploader
            st.rerun()

    with st.expander("Add a match by hand (fallback if a screenshot won't read)"):
        st.caption("Type what you can read. Leave blanks as 0. Enter batters in batting order.")
        mc1, mc2 = st.columns(2)
        ta = mc1.text_input("Team A", key="m_ta")
        sa = mc2.text_input("Team A score", key="m_sa", placeholder="e.g. 190-6 in 25.0 overs")
        tb = mc1.text_input("Team B", key="m_tb")
        sb = mc2.text_input("Team B score", key="m_sb")
        res = st.text_input("Result", key="m_res", placeholder="e.g. UOL QHPC won by 15 runs")
        mc3, mc4, mc5 = st.columns(3)
        m_date = mc3.date_input("Date", value=today, key="m_date", format="DD/MM/YYYY")
        known_formats = [f for f in formats if f != NO_FORMAT]
        m_format = mc4.selectbox("Match format", known_formats + ["Other…"], key="m_format",
                                 index=None, placeholder="Pick or choose Other…")
        if m_format == "Other…":
            m_format = mc4.text_input("Type the format", key="m_format_typed")
        m_overs = mc5.text_input("Overs per side", key="m_overs", placeholder="e.g. 20")
        m_type = "intra"  # manual entries save both teams too
        m_side = st.selectbox("Which team is ours?", [t for t in [ta, tb] if t] or ["—"], key="m_side")

        st.markdown("**Batting** (one row per batter, in batting order)")
        bat_tmpl = pd.DataFrame([{"team": "", "player": "", "runs": 0, "balls": 0,
                                  "fours": 0, "sixes": 0, "not_out": False}])
        bat_edit = st.data_editor(bat_tmpl, num_rows="dynamic", hide_index=True,
                                  width="stretch", key="m_bat")
        st.markdown("**Bowling** (one row per bowler)")
        bowl_tmpl = pd.DataFrame([{"team": "", "player": "", "overs": 0.0, "maidens": 0,
                                   "runs": 0, "wickets": 0, "wides": 0, "no_balls": 0}])
        bowl_edit = st.data_editor(bowl_tmpl, num_rows="dynamic", hide_index=True,
                                   width="stretch", key="m_bowl")

        if st.button("✅  Save manual entry"):
            mid = f"manual-{int(time.time())}"
            p = {"meta": {"match_id": mid, "date": str(m_date), "format": (m_format or "").strip(),
                          "overs": m_overs.strip(), "result": res, "toss": "", "team_a": ta, "team_b": tb,
                          "score_a": sa, "score_b": sb}, "batting": [], "bowling": []}
            order = {}
            for _, r in bat_edit.iterrows():
                if _text(r["player"]):
                    team = _text(r["team"])
                    order[team] = order.get(team, 0) + 1
                    runs, balls = _int(r["runs"]), _int(r["balls"])
                    p["batting"].append({
                        "team": team, "player": _text(r["player"]),
                        "how_out": "not out" if bool(r["not_out"]) else "other", "bowler": "", "fielder": "",
                        "runs": runs, "balls": balls, "fours": _int(r["fours"]), "sixes": _int(r["sixes"]),
                        "sr": round(runs / balls * 100, 2) if balls else 0, "pos": order[team]})
            for _, r in bowl_edit.iterrows():
                if _text(r["player"]):
                    overs, runs = _float(r["overs"]), _int(r["runs"])
                    p["bowling"].append({
                        "team": _text(r["team"]), "player": _text(r["player"]),
                        "overs": overs, "maidens": _int(r["maidens"]), "runs": runs,
                        "wickets": _int(r["wickets"]),
                        "econ": round(runs / overs, 2) if overs else 0,
                        "dots": 0, "fours_conceded": 0, "sixes_conceded": 0,
                        "wides": _int(r["wides"]), "no_balls": _int(r["no_balls"])})
            nb, nbw = db.append_match(p, m_type, m_side)
            st.session_state["add_done"] = f"Saved {nb} batting and {nbw} bowling entries."
            st.rerun()


# ---------------- Player search ----------------
def render_profile(name, bat_f, bat_base, bowl_f):
    pb = bat_f[bat_f["player"] == name].sort_values("date_dt", na_position="first")
    pw = bowl_f[bowl_f["player"] == name].sort_values("date_dt", na_position="first")
    teams = sorted({t for t in pd.concat([pb["team"], pw["team"]]) if t})
    fmts = sorted({f for f in pd.concat([pb["format_label"], pw["format_label"]]) if f != NO_FORMAT})
    n_matches = len(set(pb["match_id"]) | set(pw["match_id"]))
    st.markdown(f"### {name}")
    st.caption(" · ".join(x for x in [", ".join(teams), f"{n_matches} match(es)", ", ".join(fmts)] if x))

    view = st.radio("Show", ["Batting", "Bowling"], horizontal=True, key=f"pv_{short_hash(name)}",
                    label_visibility="collapsed")
    if view == "Batting":
        if pb.empty:
            st.caption("No batting innings under the current filters.")
            return
        r = career_batting(pb, catches_from=bat_base).iloc[0]
        for col, (lbl, v) in zip(st.columns(8), [
                ("Inns", r["Inns"]), ("Runs", r["Runs"]), ("HS", r["HS"]), ("Avg", dash(r["Avg"])),
                ("SR", dash(r["SR"])), ("50s", r["50s"]), ("100s", r["100s"]), ("Catches", r["Catches"])]):
            col.metric(lbl, v)
        left, right = st.columns([2, 3])
        with left:
            st.markdown("**By batting position**")
            bp = batting_by_position(pb)
            if bp.empty:
                st.caption("No batting positions recorded.")
            else:
                st.dataframe(bp, hide_index=True, width="stretch")
        with right:
            st.markdown("**Runs, innings by innings**")
            form = pb.reset_index(drop=True)[["runs"]].rename(columns={"runs": "Runs"})
            form.index = form.index + 1
            st.bar_chart(form, height=220, x_label="Innings", y_label="Runs")
        st.markdown("**Match by match**")
        mbm = pb.sort_values("date_dt", ascending=False, na_position="last")
        st.dataframe(pd.DataFrame({
            "Date": fmt_date(mbm["date_dt"]), "Vs": mbm["opponent"], "Format": mbm["format_label"],
            "Pos": mbm["position"], "How out": mbm["how_out"], "R": mbm["runs"], "B": mbm["balls"],
            "4s": mbm["fours"], "6s": mbm["sixes"], "SR": mbm["sr"]}),
            hide_index=True, width="stretch")
    else:
        if pw.empty:
            st.caption("No bowling innings under the current filters.")
            return
        r = career_bowling(pw).iloc[0]
        for col, (lbl, v) in zip(st.columns(7), [
                ("Inns", r["Inns"]), ("Overs", r["Overs"]), ("Wkts", r["Wkts"]), ("Best", r["Best"]),
                ("Avg", dash(r["Avg"])), ("Econ", dash(r["Econ"])), ("3W+", r["3W+"])]):
            col.metric(lbl, v)
        st.markdown("**Match by match**")
        mbm = pw.sort_values("date_dt", ascending=False, na_position="last")
        st.dataframe(pd.DataFrame({
            "Date": fmt_date(mbm["date_dt"]), "Vs": mbm["opponent"], "Format": mbm["format_label"],
            "O": mbm["overs"], "M": mbm["maidens"], "R": mbm["runs"], "W": mbm["wickets"],
            "Econ": mbm["econ"], "Wd": mbm["wides"], "NB": mbm["no_balls"]}),
            hide_index=True, width="stretch")


def compare_table(names, bat_f, bat_base, bowl_f):
    cb = career_batting(bat_f[bat_f["player"].isin(names)], catches_from=bat_base).set_index("Player")
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
            "Bowl inns": w["Inns"] if w is not None else 0,
            "Wickets": w["Wkts"] if w is not None else 0,
            "Best": w["Best"] if w is not None else "—",
            "Bowl avg": dash(w["Avg"]) if w is not None else "—",
            "Economy": dash(w["Econ"]) if w is not None else "—",
        }
    return pd.DataFrame(rows).astype(str)


@st.fragment
def player_search(bat_f, bat_base, bowl_f, players):
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

    overview = player_overview(bat_f, bowl_f, bat_base)
    found = overview[overview["Player"].isin(names)]
    missing = [n for n in names if n not in set(found["Player"])]
    if missing:   # players with no innings under the current filters
        found = pd.concat([found, pd.DataFrame({"Player": missing, "Matches": 0})], ignore_index=True)
    if fuzzy:
        found = found.set_index("Player").loc[names].reset_index()
    st.dataframe(found, hide_index=True, width="stretch", height=min(38 + 35 * len(found), 320),
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
        st.dataframe(compare_table([pick] + others, bat_f, bat_base, bowl_f), width="stretch")
        st.divider()
    render_profile(pick, bat_f, bat_base, bowl_f)


with tab_search:
    player_search(bat_f, bat_base, bowl_f, players)


# ---------------- Leaderboards ----------------
@st.fragment
def leaderboards(bat_f, bat_base, bowl_f):
    c1, c2, c3 = st.columns([2, 1, 2])
    which = c1.radio("Show", ["Batting", "Bowling"], horizontal=True, key="lb_which")
    min_inns = c2.number_input("Min. innings", min_value=1, value=1, step=1, key="lb_min")
    q = c3.text_input("Find player", key="lb_q", placeholder="Filter the table by name")
    show_filter_note(batting=which == "Batting")
    if which == "Batting":
        table = career_batting(bat_f, catches_from=bat_base)
    else:
        table = career_bowling(bowl_f)
    table = table[table["Inns"] >= min_inns]
    if q.strip():
        table = table[table["Player"].str.contains(q.strip(), case=False, regex=False)]
    if table.empty:
        st.info("Nothing to show with these filters.")
        return
    cfg = {"Pos": st.column_config.NumberColumn("Pos", help="Most common batting position")}
    st.dataframe(table, hide_index=True, width="stretch", column_config=cfg)
    st.caption(f"{len(table)} player(s). Click a column header to sort.")


with tab_board:
    leaderboards(bat_f, bat_base, bowl_f)

# ---------------- Matches ----------------
with tab_matches:
    show_filter_note(batting=False)
    if matches.empty:
        st.info("No matches saved yet.")
    elif m_view.empty:
        st.info("No matches in the current filters.")
    else:
        mv = m_view.sort_values("date_dt", ascending=False, na_position="last")
        st.dataframe(pd.DataFrame({
            "Date": fmt_date(mv["date_dt"]), "Format": mv["format_label"], "Overs": mv["overs"],
            "Team A": mv["team_a"], "Score A": mv["score_a"], "Team B": mv["team_b"],
            "Score B": mv["score_b"], "Result": mv["result"]}),
            hide_index=True, width="stretch")
    st.divider()
    if st.button("⬇️  Build Excel export"):
        out = "/tmp/qhpc-cricket-db.xlsx"
        db.export_excel(out)
        with open(out, "rb") as f:
            st.download_button("Download cricket-db.xlsx", f.read(),
                               file_name="qhpc-cricket-db.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------- Manage ----------------
GROUPS_PER_PAGE = 8


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


with tab_manage:
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
    st.subheader("Delete a team's stats")
    st.caption("Removes that team's batting and bowling rows (match fixtures stay in the Matches list).")
    flash("delteam_done")
    teams_all = sorted({t for t in pd.concat([bat_all["team"], bowl_all["team"]]) if t})
    if not teams_all:
        st.caption("No team data.")
    else:
        tpick = st.selectbox("Team", teams_all)
        sure2 = st.checkbox("Yes — remove all of this team's player stats")
        if st.button("🗑  Delete team stats", disabled=not sure2):
            db.delete_team(tpick)
            st.session_state["delteam_done"] = f"Removed {tpick}'s stats."
            st.rerun()
