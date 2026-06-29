import io
import time
import datetime as dt
import pandas as pd
import streamlit as st

import db
from parser import parse_stumps_pdf
from stats import career_batting, career_bowling, suggest_name_groups, suggest_similar_names

st.set_page_config(page_title="QHPC Cricket Stats", page_icon="🏏", layout="wide")

if "our_team" not in st.session_state:
    st.session_state.our_team = "UOL QHPC"

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("🏏 QHPC Stats")
    st.session_state.our_team = st.text_input(
        "Our team name", st.session_state.our_team,
        help="Used to tag our players and to filter inter-academy matches.")
    online = db.use_sheets()
    st.caption(("🟢 " if online else "🟡 ") + "Data store: " + db.backend_label())
    if not online:
        st.caption("Add Google Sheets secrets to make saved data permanent online (see README).")
    st.divider()
    m_df = db.load("matches")
    bat_df, bowl_df = db.load("batting"), db.load("bowling")
    players = sorted(set(bat_df["player"].dropna()) | set(bowl_df["player"].dropna()))
    c1, c2 = st.columns(2)
    c1.metric("Matches", len(m_df))
    c2.metric("Players", len(players))

st.title("QHPC Cricket Stats")
tab_add, tab_players, tab_board, tab_matches, tab_manage = st.tabs(
    ["➕  Add matches", "👤  Player profiles", "📊  Leaderboards", "📋  Matches", "🛠  Manage"])

# ---------------- Add matches ----------------
with tab_add:
    match_type = "intra"  # every upload saves both teams; trim a side later via Manage if needed

    ups = st.file_uploader(
        "Drop in one or more files — STUMPS PDFs and/or summary screenshots",
        type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)

    pdfs = [u for u in (ups or []) if u.name.lower().endswith(".pdf")]
    imgs = [u for u in (ups or []) if not u.name.lower().endswith(".pdf")]

    # ----- PDFs: parse + batch save -----
    if pdfs:
        st.markdown(f"#### {len(pdfs)} PDF(s)")
        if "parsed" not in st.session_state:
            st.session_state.parsed = {}
        items = []
        for u in pdfs:
            key = f"{u.name}-{u.size}"
            if key not in st.session_state.parsed:
                try:
                    st.session_state.parsed[key] = parse_stumps_pdf(io.BytesIO(u.getvalue()))
                except Exception as e:
                    st.session_state.parsed[key] = {"error": str(e)}
            items.append((u.name, st.session_state.parsed[key]))

        overview, distinct_teams = [], set()
        for name, p in items:
            if "error" in p:
                overview.append({"File": name, "Match": "could not read", "Status": p["error"][:40]})
                continue
            mt = p["meta"]
            distinct_teams.update(t for t in (mt["team_a"], mt["team_b"]) if t)
            overview.append({"File": name,
                             "Match": f'{mt["team_a"]} vs {mt["team_b"]}',
                             "Result": mt["result"],
                             "Status": "already saved" if db.match_exists(mt["match_id"]) else "new"})
        st.dataframe(pd.DataFrame(overview), hide_index=True, width="stretch")

        teams = sorted(distinct_teams)
        if match_type == "inter":
            idx = teams.index(st.session_state.our_team) if st.session_state.our_team in teams else 0
            our_side = st.selectbox("Which team is ours?", teams, index=idx) if teams else st.session_state.our_team
            st.caption(f"Only **{our_side}** players will be saved from each match.")
        else:
            our_side = st.session_state.our_team

        if st.button("✅  Save all new matches", type="primary"):
            saved = skipped = missing = 0
            for name, p in items:
                if "error" in p:
                    continue
                mt = p["meta"]
                if db.match_exists(mt["match_id"]):
                    skipped += 1
                    continue
                if match_type == "inter" and our_side not in (mt["team_a"], mt["team_b"]):
                    missing += 1
                    continue
                db.append_match(p, match_type, our_side)
                saved += 1
            msg = f"Saved {saved} new match(es)."
            if skipped:
                msg += f" Skipped {skipped} already in the database."
            if missing:
                msg += f" {missing} skipped — '{our_side}' wasn't a team in them (check the name)."
            st.success(msg)
            st.session_state.parsed = {}
            st.balloons()

    # ----- Screenshots: show image + manual entry -----
    if imgs:
        st.markdown(f"#### {len(imgs)} screenshot(s)")
        st.caption("The summary screenshot only lists the top batters and bowlers, so screenshots "
                   "are entered by hand below. Use the images as your reference.")
        gcols = st.columns(min(3, len(imgs)))
        for i, u in enumerate(imgs):
            gcols[i % len(gcols)].image(u.getvalue(), caption=u.name, width="stretch")

    with st.expander("Add a match from a screenshot (manual entry)", expanded=bool(imgs)):
        st.caption("Type what you can read. Leave blanks as 0.")
        mc1, mc2 = st.columns(2)
        ta = mc1.text_input("Team A", key="m_ta")
        sa = mc2.text_input("Team A score", key="m_sa", placeholder="e.g. 190-6 in 25.0 overs")
        tb = mc1.text_input("Team B", key="m_tb")
        sb = mc2.text_input("Team B score", key="m_sb")
        res = st.text_input("Result", key="m_res", placeholder="e.g. UOL QHPC won by 15 runs")
        m_date = st.date_input("Date", value=dt.date.today(), key="m_date")
        m_type = "intra"  # manual entries save both teams too
        m_side = st.selectbox("Which team is ours?", [t for t in [ta, tb] if t] or ["—"], key="m_side")

        st.markdown("**Batting** (one row per batter)")
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
            p = {"meta": {"match_id": mid, "date": str(m_date), "format": "", "overs": "",
                          "result": res, "toss": "", "team_a": ta, "team_b": tb,
                          "score_a": sa, "score_b": sb}, "batting": [], "bowling": []}
            for _, r in bat_edit.iterrows():
                if str(r["player"]).strip():
                    p["batting"].append({
                        "team": r["team"], "player": str(r["player"]).strip(),
                        "how_out": "not out" if r["not_out"] else "other", "bowler": "", "fielder": "",
                        "runs": int(r["runs"]), "balls": int(r["balls"]),
                        "fours": int(r["fours"]), "sixes": int(r["sixes"]),
                        "sr": round(int(r["runs"]) / int(r["balls"]) * 100, 2) if int(r["balls"]) else 0})
            for _, r in bowl_edit.iterrows():
                if str(r["player"]).strip():
                    p["bowling"].append({
                        "team": r["team"], "player": str(r["player"]).strip(),
                        "overs": float(r["overs"]), "maidens": int(r["maidens"]), "runs": int(r["runs"]),
                        "wickets": int(r["wickets"]),
                        "econ": round(int(r["runs"]) / float(r["overs"]), 2) if float(r["overs"]) else 0,
                        "dots": 0, "fours_conceded": 0, "sixes_conceded": 0,
                        "wides": int(r["wides"]), "no_balls": int(r["no_balls"])})
            nb, nbw = db.append_match(p, m_type, m_side)
            st.success(f"Saved {nb} batting and {nbw} bowling entries.")

# ---------------- Player profiles ----------------
with tab_players:
    if not players:
        st.info("No data yet — add a match to get started.")
    else:
        player = st.selectbox("Select a player", players)
        cb, cbw = career_batting(bat_df), career_bowling(bowl_df)
        prow, wrow = cb[cb["Player"] == player], cbw[cbw["Player"] == player]
        st.markdown(f"### {player}")
        view = st.radio("Show", ["Batting", "Bowling"], horizontal=True, key="profile_view")
        if view == "Batting":
            if prow.empty:
                st.caption("No batting record for this player.")
            else:
                r = prow.iloc[0]
                for col, (lbl, v) in zip(st.columns(7), [
                        ("Inns", r["Inns"]), ("Runs", r["Runs"]), ("HS", r["HS"]),
                        ("Avg", r["Avg"] if pd.notna(r["Avg"]) else "—"),
                        ("SR", r["SR"] if pd.notna(r["SR"]) else "—"),
                        ("50s", r["50s"]), ("Catches", r["Catches"])]):
                    col.metric(lbl, v)
                pb = bat_df[bat_df["player"] == player]
                if not pb.empty:
                    st.markdown("**Match by match**")
                    st.dataframe(pb[["date", "team", "how_out", "runs", "balls", "fours", "sixes"]],
                                 hide_index=True, width="stretch")
        else:
            if wrow.empty:
                st.caption("No bowling record for this player.")
            else:
                r = wrow.iloc[0]
                for col, (lbl, v) in zip(st.columns(6), [
                        ("Inns", r["Inns"]), ("Wkts", r["Wkts"]), ("Best", r["Best"]),
                        ("Avg", r["Avg"] if pd.notna(r["Avg"]) else "—"),
                        ("Econ", r["Econ"] if pd.notna(r["Econ"]) else "—"), ("Overs", r["Overs"])]):
                    col.metric(lbl, v)
                wbm = bowl_df[bowl_df["player"] == player]
                if not wbm.empty:
                    st.markdown("**Match by match**")
                    st.dataframe(wbm[["date", "team", "overs", "maidens", "runs", "wickets", "econ"]],
                                 hide_index=True, width="stretch")

# ---------------- Leaderboards ----------------
with tab_board:
    which = st.radio("Show", ["Batting", "Bowling"], horizontal=True)
    table = career_batting(bat_df) if which == "Batting" else career_bowling(bowl_df)
    st.dataframe(table, hide_index=True, width="stretch")

# ---------------- Matches ----------------
with tab_matches:
    if m_df.empty:
        st.info("No matches saved yet.")
    else:
        st.dataframe(m_df[["date", "type", "team_a", "score_a", "team_b", "score_b", "result"]],
                     hide_index=True, width="stretch")
    st.divider()
    if st.button("⬇️  Build Excel export"):
        out = "/tmp/qhpc-cricket-db.xlsx"
        db.export_excel(out)
        with open(out, "rb") as f:
            st.download_button("Download cricket-db.xlsx", f.read(),
                               file_name="qhpc-cricket-db.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------- Manage (merge names, delete) ----------------
with tab_manage:
    st.subheader("Merge duplicate player names")
    st.caption("Scorers often spell the same player differently. Pick the spellings that "
               "are the same person and merge them into one — all their stats combine.")
    if len(players) < 2:
        st.info("Not enough players yet to look for duplicates.")
    else:
        # apply a pending reset / success message from the previous run (before widgets are drawn)
        if st.session_state.pop("merge_reset", False):
            st.session_state["merge_ms"] = []
        if st.session_state.get("merge_done"):
            st.success(st.session_state.pop("merge_done"))

        groups = suggest_name_groups(players)
        if not groups:
            st.caption("No obviously similar names found right now.")
        else:
            st.markdown("**Possible matches found** — pick one to load it below:")
            labels = [", ".join(g) for g in groups]
            choice = st.selectbox("Suggested groups", ["—"] + labels, key="grp_choice")
            if st.button("⬇️  Load this group") and choice != "—":
                st.session_state["merge_ms"] = groups[labels.index(choice)]
                st.rerun()

        st.markdown("**Spellings of ONE player** — remove any that don't belong, then merge:")
        sel = st.multiselect("Names (you can add or remove)", players, key="merge_ms")
        canonical = ""
        if sel:
            canonical = st.selectbox("Keep this spelling", sel, key="merge_keep")
            typed = st.text_input("…or type the correct name instead (optional)", key="merge_typed")
            if typed.strip():
                canonical = typed.strip()
        can_merge = len(sel) >= 2 and bool(canonical)
        if st.button("🔗  Merge selected names", type="primary", disabled=not can_merge):
            n = db.rename_player(sel, canonical)
            st.session_state["merge_done"] = f"Merged {len(sel)} spellings into '{canonical}' ({n} entries updated)."
            st.session_state["merge_reset"] = True
            st.rerun()

    st.divider()
    st.subheader("Delete a match")
    if m_df.empty:
        st.caption("No matches saved.")
    else:
        opts = {f"{r['date']} · {r['team_a']} vs {r['team_b']}  ({r['match_id']})": r["match_id"]
                for _, r in m_df.iterrows()}
        pick = st.selectbox("Match to delete", list(opts.keys()))
        sure = st.checkbox("Yes — delete this match and all its stats")
        if st.button("🗑  Delete match", disabled=not sure):
            db.delete_match(opts[pick])
            st.success("Match deleted.")
            st.rerun()

    st.divider()
    st.subheader("Delete a team's stats")
    st.caption("Removes that team's batting and bowling rows (match fixtures stay in the Matches list).")
    teams_all = sorted(set(bat_df["team"].dropna()) | set(bowl_df["team"].dropna()))
    if not teams_all:
        st.caption("No team data.")
    else:
        tpick = st.selectbox("Team", teams_all)
        sure2 = st.checkbox("Yes — remove all of this team's player stats")
        if st.button("🗑  Delete team stats", disabled=not sure2):
            db.delete_team(tpick)
            st.success(f"Removed {tpick}'s stats.")
            st.rerun()
