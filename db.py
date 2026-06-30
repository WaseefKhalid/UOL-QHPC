"""Data store with two backends, chosen automatically:
- Google Sheets  -> used when Streamlit secrets contain `gcp_service_account` + `sheet_key`.
- Local CSV      -> used on your own machine when no secrets are set.

Google Sheets calls are cached and batched, with automatic back-off on rate limits,
so bulk uploads stay under Google's per-minute quota.
"""
import os
import time
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


# ---------------- Google Sheets backend (cached + rate-limit safe) ----------------
def _retry(fn, *args, **kwargs):
    """Call a gspread function, backing off and retrying if Google returns a rate limit (429)."""
    import gspread
    last = None
    for i in range(6):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            last = e
            status = None
            try:
                status = e.response.status_code
            except Exception:
                pass
            if status == 429:
                time.sleep(2 * (i + 1))
                continue
            raise
    if last:
        raise last


@st.cache_resource(show_spinner=False)
def _spreadsheet():
    import gspread
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    return gc.open_by_key(st.secrets["sheet_key"])


@st.cache_resource(show_spinner=False)
def _ensure_tabs():
    sh = _spreadsheet()
    titles = [ws.title for ws in _retry(sh.worksheets)]
    for name in COLUMNS:
        if name not in titles:
            ws = _retry(sh.add_worksheet, title=name, rows=2000, cols=len(COLUMNS[name]))
            _retry(ws.update, [COLUMNS[name]])
    return True


@st.cache_resource(show_spinner=False)
def _ws(name):
    _ensure_tabs()
    return _spreadsheet().worksheet(name)


@st.cache_data(ttl=120, show_spinner=False)
def _sheets_load(name):
    ws = _ws(name)
    records = _retry(ws.get_all_records)
    return pd.DataFrame(records, columns=COLUMNS[name]) if records else pd.DataFrame(columns=COLUMNS[name])


def _sheets_append(name, rows):
    if not rows:
        return
    ws = _ws(name)
    values = [[r.get(c, "") for c in COLUMNS[name]] for r in rows]
    _retry(ws.append_rows, values, value_input_option="USER_ENTERED")


def _sheets_overwrite(name, df):
    ws = _ws(name)
    _retry(ws.clear)
    values = [[(v.item() if hasattr(v, "item") else v) for v in row] for row in df.values.tolist()]
    _retry(ws.update, [COLUMNS[name]] + values, value_input_option="USER_ENTERED")


# ---------------- Local CSV backend ----------------
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


# ---------------- public API ----------------
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


def _overwrite(name, df):
    df = df.reindex(columns=COLUMNS[name]).copy()
    df = df.where(pd.notna(df), "")
    if use_sheets():
        _sheets_overwrite(name, df)
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_csv(_csv_path(name), index=False)
    clear_cache()


def clear_cache():
    try:
        st.cache_data.clear()
    except Exception:
        pass


def match_exists(match_id):
    df = load("matches")
    return not df.empty and str(match_id) in df["match_id"].astype(str).values


def _rows_for(parsed, match_type, our_side):
    meta = parsed["meta"]
    keep = lambda team: match_type == "intra" or team == our_side
    match_row = {
        "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
        "our_team": our_side, "team_a": meta["team_a"], "score_a": meta["score_a"],
        "team_b": meta["team_b"], "score_b": meta["score_b"], "result": meta["result"],
        "format": meta["format"], "overs": meta["overs"], "toss": meta["toss"],
    }
    bat = [{**b, "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
            "is_ours": b["team"] == our_side} for b in parsed["batting"] if keep(b["team"])]
    bowl = [{**b, "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
             "is_ours": b["team"] == our_side} for b in parsed["bowling"] if keep(b["team"])]
    return match_row, bat, bowl


def append_many(parsed_list, match_type, our_side):
    """Save several matches in just 3 write calls total (one per tab)."""
    all_m, all_b, all_w = [], [], []
    for p in parsed_list:
        m, b, w = _rows_for(p, match_type, our_side)
        all_m.append(m)
        all_b += b
        all_w += w
    _append("matches", all_m)
    _append("batting", all_b)
    _append("bowling", all_w)
    clear_cache()
    return len(all_b), len(all_w)


def append_match(parsed, match_type, our_side):
    return append_many([parsed], match_type, our_side)


def delete_match(match_id):
    for name in ("matches", "batting", "bowling"):
        df = load(name)
        if not df.empty:
            _overwrite(name, df[df["match_id"].astype(str) != str(match_id)])
    clear_cache()


def delete_team(team):
    for name in ("batting", "bowling"):
        df = load(name)
        if not df.empty:
            _overwrite(name, df[df["team"] != team])
    clear_cache()


def merge_players(variants, canonical):
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
    targets = [v for v in variants if v != canonical]
    bat, bowl = load("batting"), load("bowling")
    n = 0
    if not bat.empty:
        n += int(bat[["player", "bowler", "fielder"]].isin(targets).to_numpy().sum())
    if not bowl.empty:
        n += int(bowl[["player"]].isin(targets).to_numpy().sum())
    merge_players(variants, canonical)
    return n


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
