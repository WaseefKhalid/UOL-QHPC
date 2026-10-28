"""Data store with two backends, chosen automatically:
- Google Sheets  -> used when Streamlit secrets contain `gcp_service_account` + `sheet_key`.
- Local CSV      -> used on your own machine when no secrets are set.

Google Sheets calls are cached and batched, with automatic back-off on rate limits,
so bulk uploads stay under Google's per-minute quota.
"""
import difflib
import os
import re
import time
import pandas as pd
import streamlit as st

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

COLUMNS = {
    "matches": ["match_id", "date", "type", "our_team", "team_a", "score_a",
                "team_b", "score_b", "result", "format", "overs", "toss"],
    "batting": ["match_id", "date", "type", "team", "is_ours", "player", "how_out",
                "bowler", "fielder", "runs", "balls", "fours", "sixes", "sr", "pos"],
    "bowling": ["match_id", "date", "type", "team", "is_ours", "player", "overs",
                "maidens", "runs", "wickets", "econ", "dots", "fours_conceded",
                "sixes_conceded", "wides", "no_balls"],
    # your players' catches / stumpings / run-outs (they sit in the opposition's batting
    # scorecard, which isn't saved)
    "fielding": ["match_id", "date", "type", "team", "player", "dismissal"],
    # name pairs you marked "not the same player", so they stop being suggested
    "name_ignores": ["name_a", "name_b"],
    # match pairs you checked and marked "not duplicates"
    "dup_ignores": ["match_a", "match_b"],
}
STAT_TABS = ("matches", "batting", "bowling", "fielding")
FIELDING_DISMISSALS = ("caught", "caught & bowled", "stumped", "run out")
NUMERIC = {
    "matches": [],
    "batting": ["runs", "balls", "fours", "sixes", "sr", "pos"],
    "bowling": ["overs", "maidens", "runs", "wickets", "econ", "dots",
                "fours_conceded", "sixes_conceded", "wides", "no_balls"],
    "fielding": [],
    "name_ignores": [],
    "dup_ignores": [],
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
    """Call a gspread function, backing off and retrying on rate limits (429) and brief Google outages (5xx)."""
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
            if status in (429, 500, 502, 503):
                time.sleep(2 * (i + 1))
                continue
            raise
    if last:
        raise last


def _norm(header):
    return str(header).strip().lower()


def _col(n):
    """Column number -> letter: 1 -> 'A', 14 -> 'N', 27 -> 'AA'."""
    import gspread
    return gspread.utils.rowcol_to_a1(1, n)[:-1]


@st.cache_resource(show_spinner=False)
def _spreadsheet():
    import gspread
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    return gc.open_by_key(st.secrets["sheet_key"])


# The tab layout, passed into the cached setup below so that adding a tab or column
# gives a new cache entry (otherwise Streamlit reuses the old "done" from before the change).
SCHEMA = tuple((name, tuple(cols)) for name, cols in COLUMNS.items())


def _create_tab(sh, name):
    ws = _retry(sh.add_worksheet, title=name, rows=2000, cols=len(COLUMNS[name]))
    _retry(ws.update, [COLUMNS[name]], "A1")
    return ws


@st.cache_resource(show_spinner=False)
def _ensure_tabs(schema):
    """Create missing tabs, and extend the header of older tabs when new columns were
    added to COLUMNS (e.g. batting 'pos'), so new data lands under the right header."""
    sh = _spreadsheet()
    existing = {ws.title: ws for ws in _retry(sh.worksheets)}
    for name, cols in schema:
        cols = list(cols)
        ws = existing.get(name)
        if ws is None:
            _create_tab(sh, name)
            continue
        if ws.col_count < len(cols):
            _retry(ws.add_cols, len(cols) - ws.col_count)
        head = [_norm(h) for h in _retry(ws.row_values, 1)]
        while head and not head[-1]:
            head.pop()
        want = [_norm(c) for c in cols]
        if head and len(head) < len(want) and head == want[:len(head)]:
            _retry(ws.update, [cols], "A1")
    return True


@st.cache_resource(show_spinner=False)
def _ws_cached(name, schema):
    import gspread
    _ensure_tabs(schema)
    sh = _spreadsheet()
    try:
        return _retry(sh.worksheet, name)
    except gspread.exceptions.WorksheetNotFound:
        return _create_tab(sh, name)  # safety net: never fail just because a tab is missing


def _ws(name):
    return _ws_cached(name, SCHEMA)


@st.cache_data(ttl=120, show_spinner=False)
def _sheets_load(name):
    """Read a tab by column *name*, so blank, duplicate, extra or reordered columns
    in the sheet can't break the load (gspread's get_all_records crashes on them)."""
    import gspread
    cols = COLUMNS[name]
    grid = _retry(_ws(name).get_all_values)
    rows = [r for r in grid if any(str(c).strip() for c in r)]  # skip fully blank rows
    if not rows:
        return pd.DataFrame(columns=cols)

    pos = {}
    for i, h in enumerate(rows[0]):
        key = _norm(h)
        if key and key not in pos:  # first occurrence wins if a name repeats
            pos[key] = i
    idx = [pos.get(_norm(c)) for c in cols]

    if any(i is not None for i in idx):
        body = rows[1:]
    else:
        # No recognisable header row (it was wiped): rows are written in COLUMNS order,
        # so read them by position instead of losing the data.
        idx, body = list(range(len(cols))), rows

    records = [
        gspread.utils.numericise_all(
            [r[i] if i is not None and i < len(r) else "" for i in idx]
        )
        for r in body
    ]
    return pd.DataFrame(records, columns=cols)


def _sheets_append(name, rows):
    if not rows:
        return
    ws = _ws(name)
    values = [[r.get(c, "") for c in COLUMNS[name]] for r in rows]
    # table_range="A1" anchors new rows to column A; without it Sheets can place
    # appended rows a few columns to the right, under blank headers.
    _retry(ws.append_rows, values, value_input_option="USER_ENTERED", table_range="A1")


def _sheets_overwrite(name, df):
    """Write header + rows from A1 first, then clear whatever is left over.
    (Clearing first meant a failed write could leave the tab empty, header included.)"""
    cols = COLUMNS[name]
    values = [[(v.item() if hasattr(v, "item") else v) for v in row] for row in df.values.tolist()]
    data = [cols] + values

    ws = _retry(_spreadsheet().worksheet, name)  # fresh copy so the grid size is current
    if ws.row_count < len(data):
        _retry(ws.add_rows, len(data) - ws.row_count)
    if ws.col_count < len(cols):
        _retry(ws.add_cols, len(cols) - ws.col_count)
    _retry(ws.update, data, "A1", value_input_option="USER_ENTERED")

    stale = []
    if ws.row_count > len(data):  # old rows below the new data
        stale.append(f"A{len(data) + 1}:{_col(max(ws.col_count, len(cols)))}{ws.row_count}")
    if ws.col_count > len(cols):  # stray cells to the right of the last header
        stale.append(f"{_col(len(cols) + 1)}1:{_col(ws.col_count)}{len(data)}")
    if stale:
        _retry(ws.batch_clear, stale)


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
    df = pd.concat([df.reindex(columns=COLUMNS[name]),
                    pd.DataFrame(rows).reindex(columns=COLUMNS[name])], ignore_index=True)
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


def _team_key(name):
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def side_of(team, team_a, team_b):
    """'A' or 'B': which side of the match a row's team belongs to. Tolerant of small
    differences between the summary header and the scorecard header."""
    t, a, b = _team_key(team), _team_key(team_a), _team_key(team_b)
    if t == a:
        return "A"
    if t == b:
        return "B"
    ra = difflib.SequenceMatcher(None, t, a).ratio()
    rb = difflib.SequenceMatcher(None, t, b).ratio()
    return "A" if ra >= rb else "B"


def _fielding_rows(bat_rows, team_a, team_b, sides, extra):
    """Credits for your fielders, taken from the scorecard of the side they bowled at."""
    names = {"A": team_a, "B": team_b}
    out = []
    for b in bat_rows:
        side = "B" if side_of(b["team"], team_a, team_b) == "A" else "A"   # the fielding side
        how, fielder = b.get("how_out"), str(b.get("fielder") or "").strip()
        if side not in sides or how not in FIELDING_DISMISSALS or not fielder:
            continue
        for f in (fielder.split("/") if how == "run out" else [fielder]):
            if f.strip():
                out.append({**extra, "team": names[side], "player": f.strip(), "dismissal": how})
    return out


def rows_for(parsed, sides):
    """Rows to save for one match. `sides` is the side(s) that are your team:
    {"A"}, {"B"}, or {"A", "B"} for an intra-squad game. The other side's players are
    not saved; only your fielders' catches/stumpings/run-outs are taken from their scorecard."""
    meta = parsed["meta"]
    names = {"A": meta["team_a"], "B": meta["team_b"]}
    match_type = "intra" if len(sides) == 2 else "inter"
    ours = lambda team: side_of(team, meta["team_a"], meta["team_b"]) in sides
    match_row = {
        "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
        "our_team": " & ".join(names[s] for s in sorted(sides)), "team_a": meta["team_a"],
        "score_a": meta["score_a"], "team_b": meta["team_b"], "score_b": meta["score_b"],
        "result": meta["result"], "format": meta["format"], "overs": meta["overs"], "toss": meta["toss"],
    }
    extra = {"match_id": meta["match_id"], "date": meta["date"], "type": match_type, "is_ours": True}
    bat = [{**b, **extra} for b in parsed["batting"] if ours(b["team"])]
    bowl = [{**b, **extra} for b in parsed["bowling"] if ours(b["team"])]
    field = _fielding_rows(parsed["batting"], meta["team_a"], meta["team_b"], sides,
                           {"match_id": meta["match_id"], "date": meta["date"], "type": match_type})
    return match_row, bat, bowl, field


def append_many(items):
    """items: list of (parsed, sides). Saves every match in 4 write calls total (one per tab)."""
    all_m, all_b, all_w, all_f = [], [], [], []
    for parsed, sides in items:
        m, b, w, f = rows_for(parsed, sides)
        all_m.append(m)
        all_b += b
        all_w += w
        all_f += f
    _append("matches", all_m)
    _append("batting", all_b)
    _append("bowling", all_w)
    _append("fielding", all_f)
    clear_cache()
    return len(all_b), len(all_w)


def append_match(parsed, sides):
    return append_many([(parsed, sides)])


def delete_match(match_id):
    for name in ("matches", "batting", "bowling", "fielding"):
        df = load(name)
        if not df.empty:
            _overwrite(name, df[df["match_id"].astype(str) != str(match_id)])
    clear_cache()


def delete_team(team):
    for name in ("batting", "bowling", "fielding"):
        df = load(name)
        if not df.empty:
            _overwrite(name, df[df["team"] != team])
    clear_cache()


def keep_only_teams(teams):
    """Delete the batting and bowling rows of every team not in `teams`.
    The match list (results, scores) is kept. Returns the number of rows removed."""
    keep = {str(t) for t in teams}

    # Before the other teams' batting goes, copy your fielders' catches out of it
    # (only for matches that don't have fielding rows yet, so nothing is counted twice).
    bat, m = load("batting"), load("matches")
    have = set(load("fielding")["match_id"].astype(str))
    info = {str(r["match_id"]): (str(r["team_a"]), str(r["team_b"]), str(r["type"]))
            for _, r in m.iterrows()}
    field = []
    gone = bat[~bat["team"].fillna("").astype(str).isin(keep)]
    for _, r in gone.iterrows():
        mid = str(r["match_id"])
        if mid in have or mid not in info:
            continue
        ta, tb, mtype = info[mid]
        sides = {s for s, t in (("A", ta), ("B", tb)) if t in keep}
        field += _fielding_rows([r.to_dict()], ta, tb, sides,
                                {"match_id": r["match_id"], "date": r["date"], "type": mtype})
    _append("fielding", field)
    clear_cache()  # the loop below must see the rows just added

    removed = 0
    for name in ("batting", "bowling", "fielding"):
        df = load(name)
        if df.empty:
            continue
        mask = df["team"].fillna("").astype(str).isin(keep)
        if (~mask).any():
            removed += int((~mask).sum())
            _overwrite(name, df[mask])
    clear_cache()
    return removed


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
    field = load("fielding")
    if not field.empty and field["player"].isin(variants).any():
        field["player"] = field["player"].replace(variants, canonical)
        _overwrite("fielding", field)
    clear_cache()


def rename_player(variants, canonical):
    targets = [v for v in variants if v != canonical]
    bat, bowl = load("batting"), load("bowling")
    n = 0
    if not bat.empty:
        n += int(bat[["player", "bowler", "fielder"]].isin(targets).to_numpy().sum())
    if not bowl.empty:
        n += int(bowl[["player"]].isin(targets).to_numpy().sum())
    n += int(load("fielding")["player"].isin(targets).sum())
    merge_players(variants, canonical)
    return n


# ---------------- "not the same player" memory ----------------
def ignored_pairs():
    """Name pairs marked as different players, as a set of frozensets."""
    df = load("name_ignores")
    pairs = set()
    for a, b in zip(df["name_a"].fillna("").astype(str), df["name_b"].fillna("").astype(str)):
        if a.strip() and b.strip() and a != b:
            pairs.add(frozenset((a, b)))
    return pairs


def add_ignored_pairs(pairs):
    have = ignored_pairs()
    rows = []
    for a, b in pairs:
        key = frozenset((a, b))
        if a != b and key not in have:
            have.add(key)
            x, y = sorted(key)
            rows.append({"name_a": x, "name_b": y})
    _append("name_ignores", rows)
    clear_cache()


def remove_ignored_pairs(pairs):
    drop = {frozenset(p) for p in pairs}
    df = load("name_ignores")
    keep = [frozenset((str(a), str(b))) not in drop
            for a, b in zip(df["name_a"].fillna(""), df["name_b"].fillna(""))]
    _overwrite("name_ignores", df[keep])


# ---------------- duplicates + start from zero ----------------
def dup_ignored():
    df = load("dup_ignores")
    return {frozenset((str(a), str(b))) for a, b in zip(df["match_a"].fillna(""), df["match_b"].fillna(""))
            if str(a).strip() and str(b).strip()}


def add_dup_ignore(match_a, match_b):
    if frozenset((str(match_a), str(match_b))) not in dup_ignored():
        _append("dup_ignores", [{"match_a": str(match_a), "match_b": str(match_b)}])
    clear_cache()


def remove_double_save(match_id):
    """A match saved twice under the same ID: keep one copy of its rows in every tab."""
    for name in STAT_TABS:
        df = load(name)
        if df.empty:
            continue
        is_m = df["match_id"].astype(str) == str(match_id)
        if name == "matches":
            dup = is_m & df["match_id"].astype(str).duplicated()
        else:
            dup = is_m & df.astype(str).duplicated()
        if dup.any():
            _overwrite(name, df[~dup])
    clear_cache()


def counts():
    """Rows per tab, for the 'start from zero' screen."""
    return {name: len(load(name)) for name in COLUMNS}


def delete_all(forget_decisions=False):
    """Empty every stats tab (headers stay). With forget_decisions, also clear the
    'not the same player' and 'not duplicates' decisions."""
    tabs = list(STAT_TABS) + (["name_ignores", "dup_ignores"] if forget_decisions else [])
    for name in tabs:
        _overwrite(name, pd.DataFrame(columns=COLUMNS[name]))
    clear_cache()


def export_excel(path):
    from stats import career_batting, career_bowling, enrich
    m, bat, bowl, field = load("matches"), load("batting"), load("bowling"), load("fielding")
    _, bat_e, bowl_e, field_e = enrich(m, bat, bowl, field)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        m.to_excel(xl, sheet_name="Matches", index=False)
        bat.to_excel(xl, sheet_name="Batting", index=False)
        bowl.to_excel(xl, sheet_name="Bowling", index=False)
        field.to_excel(xl, sheet_name="Fielding", index=False)
        career_batting(bat_e, fielding=field_e).drop(columns=["Pos"], errors="ignore") \
            .to_excel(xl, sheet_name="Career Batting", index=False)
        career_bowling(bowl_e).to_excel(xl, sheet_name="Career Bowling", index=False)
    return path
