
import io
import time
import datetime as dt
import pandas as pd
import streamlit as st

import db
from parser import parse_stumps_pdf
from stats import career_batting, career_bowling

st.set_page_config(page_title="QHPC Cricket Stats", page_icon="🏏", layout="wide")

if "our_team" not in st.session_state:
    st.session_state.our_team = "UOL QHPC"

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("🏏 QHPC Stats")
    st.session_state.our_team = st.text_input("Our team name", st.session_state.our_team,
                                               help="Used to tag our players and to filter inter-academy matches.")
    st.divider()
    m_df = db.load("matches")
    bat_df, bowl_df = db.load("batting"), db.load("bowling")
    players = sorted(set(bat_df.get("player", pd.Series(dtype=str)).dropna()) |
                     set(bowl_df.get("player", pd.Series(dtype=str)).dropna()))
    c1, c2 = st.columns(2)
    c1.metric("Matches", len(m_df))
    c2.metric("Players", len(players))

st.title("QHPC Cricket Stats")
tab_add, tab_players, tab_board, tab_matches = st.tabs(
    ["➕  Add a match", "👤  Player profiles", "📊  Leaderboards", "📋  Matches"])

# ---------------- Add a match ----------------
with tab_add:
    st.subheader("Upload a STUMPS PDF")
    match_type = st.radio(
        "Match type", ["intra", "inter"], horizontal=True,
        format_func=lambda x: "Intra-academy — save both teams" if x == "intra"
        else "Inter-academy — save our players only")

    up = st.file_uploader("Drop the STUMPS match-report PDF here", type=["pdf"])
    if up is not None:
        key = f"{up.name}-{up.size}"
        if st.session_state.get("parsed_key") != key:
            with st.spinner("Reading the scorecard…"):
                try:
                    st.session_state.parsed = parse_stumps_pdf(io.BytesIO(up.getvalue()))
                    st.session_state.parsed_key = key
                except Exception as e:
                    st.error(f"Could not read this PDF: {e}")
                    st.session_state.parsed = None

        parsed = st.session_state.get("parsed")
        if parsed:
            meta = parsed["meta"]
            st.success(f"**{meta['result'] or 'Match'}**")
            a, b, c = st.columns(3)
            a.metric(meta["team_a"], meta["score_a"])
            b.metric(meta["team_b"], meta["score_b"])
            c.metric("Date", meta["date"].split(" ")[0] if meta["date"] else "—")

            teams = [meta["team_a"], meta["team_b"]]
            if match_type == "inter":
                default = teams.index(st.session_state.our_team) if st.session_state.our_team in teams else 0
                our_side = st.selectbox("Which team is ours?", teams, index=default)
                st.caption(f"Only **{our_side}** players will be saved.")
            else:
                our_side = st.session_state.our_team
                st.caption("Both teams will be saved.")

            with st.expander("Preview parsed scorecard", expanded=True):
                bcol, wcol = st.columns(2)
                bcol.markdown("**Batting**")
                bcol.dataframe(pd.DataFrame(parsed["batting"])[
                    ["team", "player", "how_out", "runs", "balls", "fours", "sixes"]],
                    hide_index=True, width='stretch')
                wcol.markdown("**Bowling**")
                wcol.dataframe(pd.DataFrame(parsed["bowling"])[
                    ["team", "player", "overs", "maidens", "runs", "wickets"]],
                    hide_index=True, width='stretch')

            if db.match_exists(meta["match_id"]):
                st.warning("This match is already in the database — nothing to add.")
            elif st.button("✅  Save match to database", type="primary"):
                nb, nbw = db.append_match(parsed, match_type, our_side)
                st.success(f"Saved {nb} batting and {nbw} bowling entries from this match.")
                st.balloons()
                st.session_state.parsed = None
                st.session_state.parsed_key = None

    with st.expander("No PDF? Add from the summary screenshot (manual)"):
        st.caption("Type what you can read off the summary image. Leave blanks as 0.")
        mc1, mc2 = st.columns(2)
        ta = mc1.text_input("Team A", key="m_ta")
        sa = mc2.text_input("Team A score", key="m_sa", placeholder="e.g. 190-6 in 25.0 overs")
        tb = mc1.text_input("Team B", key="m_tb")
        sb = mc2.text_input("Team B score", key="m_sb")
        res = st.text_input("Result", key="m_res", placeholder="e.g. UOL QHPC won by 15 runs")
        m_date = st.date_input("Date", value=dt.date.today(), key="m_date")
        m_type = st.radio("Match type", ["intra", "inter"], horizontal=True, key="m_type",
                          format_func=lambda x: "Intra — both teams" if x == "intra" else "Inter — our players only")
        m_side = st.selectbox("Which team is ours?", [t for t in [ta, tb] if t] or ["—"], key="m_side")

        st.markdown("**Batting** (one row per batter)")
        bat_tmpl = pd.DataFrame([{"team": "", "player": "", "runs": 0, "balls": 0,
                                  "fours": 0, "sixes": 0, "not_out": False}])
        bat_edit = st.data_editor(bat_tmpl, num_rows="dynamic", hide_index=True,
                                  width='stretch', key="m_bat")
        st.markdown("**Bowling** (one row per bowler)")
        bowl_tmpl = pd.DataFrame([{"team": "", "player": "", "overs": 0.0, "maidens": 0,
                                   "runs": 0, "wickets": 0, "wides": 0, "no_balls": 0}])
        bowl_edit = st.data_editor(bowl_tmpl, num_rows="dynamic", hide_index=True,
                                   width='stretch', key="m_bowl")

        if st.button("✅  Save manual entry"):
            mid = f"manual-{int(time.time())}"
            parsed = {"meta": {"match_id": mid, "date": str(m_date), "format": "", "overs": "",
                               "result": res, "toss": "", "team_a": ta, "team_b": tb,
                               "score_a": sa, "score_b": sb}, "batting": [], "bowling": []}
            for _, r in bat_edit.iterrows():
                if str(r["player"]).strip():
                    parsed["batting"].append({
                        "team": r["team"], "player": str(r["player"]).strip(),
                        "how_out": "not out" if r["not_out"] else "other", "bowler": "", "fielder": "",
                        "runs": int(r["runs"]), "balls": int(r["balls"]),
                        "fours": int(r["fours"]), "sixes": int(r["sixes"]),
                        "sr": round(int(r["runs"]) / int(r["balls"]) * 100, 2) if int(r["balls"]) else 0})
            for _, r in bowl_edit.iterrows():
                if str(r["player"]).strip():
                    parsed["bowling"].append({
                        "team": r["team"], "player": str(r["player"]).strip(),
                        "overs": float(r["overs"]), "maidens": int(r["maidens"]), "runs": int(r["runs"]),
                        "wickets": int(r["wickets"]), "econ": round(int(r["runs"]) / float(r["overs"]), 2) if float(r["overs"]) else 0,
                        "dots": 0, "fours_conceded": 0, "sixes_conceded": 0,
                        "wides": int(r["wides"]), "no_balls": int(r["no_balls"])})
            nb, nbw = db.append_match(parsed, m_type, m_side)
            st.success(f"Saved {nb} batting and {nbw} bowling entries.")

# ---------------- Player profiles ----------------
with tab_players:
    if not players:
        st.info("No data yet — add a match to get started.")
    else:
        player = st.selectbox("Select a player", players)
        cb = career_batting(bat_df)
        cbw = career_bowling(bowl_df)
        prow = cb[cb["Player"] == player]
        wrow = cbw[cbw["Player"] == player]

        st.markdown(f"### {player}")
        if not prow.empty:
            r = prow.iloc[0]
            st.markdown("**Batting**")
            cols = st.columns(7)
            vals = [("Inns", r["Inns"]), ("Runs", r["Runs"]), ("HS", r["HS"]),
                    ("Avg", r["Avg"] if pd.notna(r["Avg"]) else "—"),
                    ("SR", r["SR"] if pd.notna(r["SR"]) else "—"),
                    ("50s", r["50s"]), ("Catches", r["Catches"])]
            for col, (lbl, v) in zip(cols, vals):
                col.metric(lbl, v)
        if not wrow.empty:
            r = wrow.iloc[0]
            st.markdown("**Bowling**")
            cols = st.columns(6)
            vals = [("Inns", r["Inns"]), ("Wkts", r["Wkts"]), ("Best", r["Best"]),
                    ("Avg", r["Avg"] if pd.notna(r["Avg"]) else "—"),
                    ("Econ", r["Econ"] if pd.notna(r["Econ"]) else "—"), ("Overs", r["Overs"])]
            for col, (lbl, v) in zip(cols, vals):
                col.metric(lbl, v)

        st.markdown("**Match by match**")
        pb = bat_df[bat_df["player"] == player]
        if not pb.empty:
            st.dataframe(pb[["date", "team", "how_out", "runs", "balls", "fours", "sixes"]],
                         hide_index=True, width='stretch')

# ---------------- Leaderboards ----------------
with tab_board:
    which = st.radio("Show", ["Batting", "Bowling"], horizontal=True)
    if which == "Batting":
        st.dataframe(career_batting(bat_df), hide_index=True, width='stretch')
    else:
        st.dataframe(career_bowling(bowl_df), hide_index=True, width='stretch')

# ---------------- Matches ----------------
with tab_matches:
    if m_df.empty:
        st.info("No matches saved yet.")
    else:
        st.dataframe(m_df[["date", "type", "team_a", "score_a", "team_b", "score_b", "result"]],
                     hide_index=True, width='stretch')
    st.divider()
    if st.button("⬇️  Build Excel export"):
        out = "/tmp/qhpc-cricket-db.xlsx"
        db.export_excel(out)
        with open(out, "rb") as f:
            st.download_button("Download cricket-db.xlsx", f.read(),
                               file_name="qhpc-cricket-db.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
