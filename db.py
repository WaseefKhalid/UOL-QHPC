"""Data store with two backends, chosen automatically:
- Google Sheets  -> used when Streamlit secrets contain `gcp_service_account` + `sheet_key`
                    (this is what makes the deployed app remember data permanently).
- Local CSV      -> used on your own machine when no secrets are set.

The rest of the app calls the same functions either way:
load(name), append_match(...), match_exists(id), export_excel(path), clear_cache().
"""
import os
import pandas as pd
import streamlit as st

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

COLUMNS = {
    "matches": ["match_id", "date", "type", "our_team", "team_a", "score_a",
                "team_b", "score_b", "result", "format", "overs", "toss"],
    "batting": ["match_id", "date", "type", "team", "is_ours", "player", "how_out",
                "bowler", "fielder", "runs", "balls", "fours", "sixes", "sr"],
    "bowling": ["match_id", "date", "type", "team", "is_ours", "player", "overs",
                "maidens", "runs", "wickets", "econ", "dots", "fours_conceded",
                "sixes_conceded", "wides", "no_balls"],
}
NUMERIC = {
    "matches": [],
    "batting": ["runs", "balls", "fours", "sixes", "sr"],
    "bowling": ["overs", "maidens", "runs", "wickets", "econ", "dots",
                "fours_conceded", "sixes_conceded", "wides", "no_balls"],
}


def use_sheets():
    try:
        return "gcp_service_account" in st.secrets and "sheet_key" in st.secrets
    except Exception:
        return False


def backend_label():
    return "Online (Google Sheets)" if use_sheets() else "Local (this computer)"


@st.cache_resource(show_spinner=False)
def _spreadsheet():
    import gspread
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    return gc.open_by_key(st.secrets["sheet_key"])


def _worksheet(name):
    import gspread
    sh = _spreadsheet()
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=2000, cols=len(COLUMNS[name]))
    if not ws.get_all_values():
        ws.update([COLUMNS[name]])
    return ws


@st.cache_data(ttl=60, show_spinner=False)
def _sheets_load(name):
    ws = _worksheet(name)
    records = ws.get_all_records()
    return pd.DataFrame(records, columns=COLUMNS[name]) if records else pd.DataFrame(columns=COLUMNS[name])


def _sheets_append(name, rows):
    if not rows:
        return
    ws = _worksheet(name)
    values = [[r.get(c, "") for c in COLUMNS[name]] for r in rows]
    ws.append_rows(values, value_input_option="USER_ENTERED")


def _csv_path(name):
    return os.path.join(DATA_DIR, f"{name}.csv")


def _csv_load(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    p = _csv_path(name)
    return pd.read_csv(p, dtype={"match_id": str}) if os.path.exists(p) else pd.DataFrame(columns=COLUMNS[name])


def _csv_append(name, rows):
    if not rows:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    df = _csv_load(name)
    df = pd.concat([df, pd.DataFrame(rows)[COLUMNS[name]]], ignore_index=True)
    df.to_csv(_csv_path(name), index=False)


def load(name):
    df = _sheets_load(name) if use_sheets() else _csv_load(name)
    df = df.reindex(columns=COLUMNS[name])
    for col in NUMERIC[name]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def _append(name, rows):
    if use_sheets():
        _sheets_append(name, rows)
    else:
        _csv_append(name, rows)


def clear_cache():
    try:
        st.cache_data.clear()
    except Exception:
        pass


def match_exists(match_id):
    df = load("matches")
    return not df.empty and str(match_id) in df["match_id"].astype(str).values


def append_match(parsed, match_type, our_side):
    """intra keeps both teams; inter keeps `our_side` only."""
    meta = parsed["meta"]
    keep = lambda team: match_type == "intra" or team == our_side

    _append("matches", [{
        "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
        "our_team": our_side, "team_a": meta["team_a"], "score_a": meta["score_a"],
        "team_b": meta["team_b"], "score_b": meta["score_b"], "result": meta["result"],
        "format": meta["format"], "overs": meta["overs"], "toss": meta["toss"],
    }])

    bat_rows = [{**b, "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
                 "is_ours": b["team"] == our_side} for b in parsed["batting"] if keep(b["team"])]
    _append("batting", bat_rows)

    bowl_rows = [{**b, "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
                  "is_ours": b["team"] == our_side} for b in parsed["bowling"] if keep(b["team"])]
    _append("bowling", bowl_rows)

    clear_cache()
    return len(bat_rows), len(bowl_rows)


INT_COLS = {
    "matches": [],
    "batting": ["runs", "balls", "fours", "sixes"],
    "bowling": ["maidens", "runs", "wickets", "dots", "fours_conceded",
                "sixes_conceded", "wides", "no_balls"],
}


def _save_full(name, df):
    """Overwrite the whole table (used by delete and merge)."""
    df = df.reindex(columns=COLUMNS[name]).copy()
    for c in INT_COLS[name]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    if use_sheets():
        ws = _worksheet(name)
        ws.clear()
        values = [COLUMNS[name]] + df.fillna("").astype(object).values.tolist()
        ws.append_rows(values, value_input_option="USER_ENTERED")
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_csv(_csv_path(name), index=False)
    clear_cache()


def delete_match(match_id):
    """Remove a match and all its batting/bowling rows."""
    match_id = str(match_id)
    for name in ("matches", "batting", "bowling"):
        df = load(name)
        df = df[df["match_id"].astype(str) != match_id]
        _save_full(name, df)


def delete_team(team):
    """Remove a team's batting and bowling rows (match fixtures are left in place)."""
    for name in ("batting", "bowling"):
        df = load(name)
        df = df[df["team"] != team]
        _save_full(name, df)


def rename_player(old_names, new_name):
    """Merge one or more name spellings into a single canonical name."""
    old = set(old_names) - {new_name}
    if not old:
        return 0
    changed = 0
    bat = load("batting")
    for col in ("player", "bowler", "fielder"):
        mask = bat[col].isin(old)
        changed += int(mask.sum()) if col == "player" else 0
        bat.loc[mask, col] = new_name
    _save_full("batting", bat)
    bowl = load("bowling")
    mask = bowl["player"].isin(old)
    changed += int(mask.sum())
    bowl.loc[mask, "player"] = new_name
    _save_full("bowling", bowl)
    return changed


def export_excel(path):
    from stats import career_batting, career_bowling
    bat, bowl = load("batting"), load("bowling")
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        load("matches").to_excel(xl, sheet_name="Matches", index=False)
        bat.to_excel(xl, sheet_name="Batting", index=False)
        bowl.to_excel(xl, sheet_name="Bowling", index=False)
        career_batting(bat).to_excel(xl, sheet_name="Career Batting", index=False)
        career_bowling(bowl).to_excel(xl, sheet_name="Career Bowling", index=False)
    return path


# ---------------- Edit / cleanup operations ----------------
def _overwrite(name, df):
    """Replace an entire table with df (used by delete and merge)."""
    df = df.reindex(columns=COLUMNS[name]).copy()
    df = df.where(pd.notna(df), "")
    if use_sheets():
        ws = _worksheet(name)
        ws.clear()
        values = [[(v.item() if hasattr(v, "item") else v) for v in row] for row in df.values.tolist()]
        ws.update([COLUMNS[name]] + values, value_input_option="USER_ENTERED")
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_csv(_csv_path(name), index=False)
    clear_cache()


def delete_match(match_id):
    """Remove a match and all its batting/bowling rows."""
    for name in ("matches", "batting", "bowling"):
        df = load(name)
        if not df.empty:
            _overwrite(name, df[df["match_id"].astype(str) != str(match_id)])
    clear_cache()


def delete_team(team):
    """Remove all batting/bowling rows for a team (match records are kept)."""
    for name in ("batting", "bowling"):
        df = load(name)
        if not df.empty:
            _overwrite(name, df[df["team"] != team])
    clear_cache()


def merge_players(variants, canonical):
    """Rename every occurrence of `variants` to `canonical` across player, bowler and fielder."""
    bat = load("batting")
    if not bat.empty:
        for col in ("player", "bowler", "fielder"):
            bat[col] = bat[col].replace(variants, canonical)
        _overwrite("batting", bat)
    bowl = load("bowling")
    if not bowl.empty:
        bowl["player"] = bowl["player"].replace(variants, canonical)
        _overwrite("bowling", bowl)
    clear_cache()


def rename_player(variants, canonical):
    """UI-facing merge: rename `variants` to `canonical`, return how many entries changed."""
    targets = [v for v in variants if v != canonical]
    bat, bowl = load("batting"), load("bowling")
    n = 0
    if not bat.empty:
        n += int(bat[["player", "bowler", "fielder"]].isin(targets).to_numpy().sum())
    if not bowl.empty:
        n += int(bowl[["player"]].isin(targets).to_numpy().sum())
    merge_players(variants, canonical)
    return n
