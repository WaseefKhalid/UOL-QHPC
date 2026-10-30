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
                "team_b", "score_b", "result", "format", "overs", "toss", "ball",
                "venue", "opponent", "outcome", "toss_result", "innings", "coach"],
    "batting": ["match_id", "date", "type", "team", "is_ours", "player", "how_out",
                "bowler", "fielder", "runs", "balls", "fours", "sixes", "sr", "pos", "scorecard_name"],
    "bowling": ["match_id", "date", "type", "team", "is_ours", "player", "overs",
                "maidens", "runs", "wickets", "econ", "dots", "fours_conceded",
                "sixes_conceded", "wides", "no_balls", "scorecard_name"],
    # your players' catches / stumpings / run-outs (they sit in the opposition's batting
    # scorecard, which isn't saved)
    "fielding": ["match_id", "date", "type", "team", "player", "dismissal", "scorecard_name"],
    # name pairs you marked "not the same player", so they stop being suggested
    "name_ignores": ["name_a", "name_b"],
    # match pairs you checked and marked "not duplicates"
    "dup_ignores": ["match_a", "match_b"],
    # one row per player: things that don't change match to match (bowling type)
    "players": ["player", "bowling_type"],
    # your squad list — add new admissions here (one full name per row)
    "squad": ["Full name"],
    # scorecard spellings you've confirmed: "Hasnian" -> "Muhammad Hasnain Ali"
    "spellings": ["Scorecard name", "Full name", "In squad"],
    # lists you can change any time (in the app: Manage → Lists, or straight in the sheet)
    "formats": ["Match format"],
    "coaches": ["Coach"],
}
BOWLING_TYPES = ("Pace", "Spin")
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
    "players": [],
    "squad": [],
    "spellings": [],
    "formats": [],
    "coaches": [],
}


def _defaults():
    from stats import FORMAT_CHOICES, COACHES
    return {"formats": FORMAT_CHOICES, "coaches": COACHES}


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
    waits = [3, 6, 12, 20, 30]          # ~70 s in total: Google's read/write quota resets each minute
    for i in range(len(waits) + 1):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            last = e
            status = None
            try:
                status = e.response.status_code
            except Exception:
                pass
            if status in (429, 500, 502, 503) and i < len(waits):
                time.sleep(waits[i])
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
    seed = [[v] for v in _defaults().get(name, [])]          # new list tabs start with the default list
    _retry(ws.update, [COLUMNS[name]] + seed, "A1")
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
    if not os.path.exists(p) and name in _defaults():
        pd.DataFrame({COLUMNS[name][0]: _defaults()[name]}).to_csv(p, index=False)
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
        "ball": meta.get("ball", ""),
        "venue": meta.get("venue", ""), "opponent": meta.get("opponent", ""), "outcome": meta.get("outcome", ""),
        "toss_result": meta.get("toss_result", ""), "innings": meta.get("innings", ""),
        "coach": meta.get("coach", ""),
    }
    extra = {"match_id": meta["match_id"], "date": meta["date"], "type": match_type, "is_ours": True}
    bat = [{**b, **extra} for b in parsed["batting"] if ours(b["team"])]
    bowl = [{**b, **extra} for b in parsed["bowling"] if ours(b["team"])]
    field = _fielding_rows(parsed["batting"], meta["team_a"], meta["team_b"], sides,
                           {"match_id": meta["match_id"], "date": meta["date"], "type": match_type})
    return match_row, bat, bowl, field


def append_many(items, names=None):
    """items: list of (parsed, sides). names: {scorecard name: full name} from the squad list.
    Saves every match in 4 write calls total (one per tab)."""
    names = names or {}
    all_m, all_b, all_w, all_f = [], [], [], []
    for parsed, sides in items:
        m, b, w, f = rows_for(parsed, sides)
        for row in b + w + f:
            row["scorecard_name"] = row["player"]
            row["player"] = names.get(row["player"], row["player"])
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


def append_match(parsed, sides, names=None):
    return append_many([(parsed, sides)], names)


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


def rename_many(mapping):
    """Rename several players in one go: {old name: new name}. Reads and writes each tab once
    (renaming one player at a time used too many Google Sheets calls and hit the per-minute limit).
    Returns how many entries changed."""
    mapping = {str(o): str(n) for o, n in mapping.items() if str(o).strip() and str(n).strip() and o != n}
    if not mapping:
        return 0
    changed = 0
    for name, cols in (("batting", ("player", "bowler", "fielder")), ("bowling", ("player",)),
                       ("fielding", ("player",))):
        df = load(name)
        if df.empty:
            continue
        hits = int(df[list(cols)].isin(list(mapping)).to_numpy().sum())
        if hits:
            for col in cols:
                df[col] = df[col].replace(mapping)
            _overwrite(name, df)
            changed += hits
    types = bowling_types()
    if any(o in types for o in mapping):
        for old, new in mapping.items():
            if old in types:
                types.setdefault(new, types[old])
                types.pop(old)
        _save_types(types)
    clear_cache()
    return changed


def merge_players(variants, canonical):
    rename_many({v: canonical for v in variants if v != canonical})


def rename_player(variants, canonical):
    return rename_many({v: canonical for v in variants if v != canonical})


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


# ---------------- editable lists: match formats, coaches ----------------
def get_list(name):
    """Values of a one-column list tab, in order, blanks and repeats removed (falls back to the defaults)."""
    col = COLUMNS[name][0]
    out = []
    for v in load(name)[col].fillna("").astype(str):
        v = " ".join(v.split())
        if v and v.lower() != col.lower() and v not in out:
            out.append(v)
    return out or list(_defaults().get(name, []))


def save_list(name, values):
    vals = []
    for v in values:
        v = " ".join(str(v or "").split())
        if v and v not in vals:
            vals.append(v)
    _overwrite(name, pd.DataFrame({COLUMNS[name][0]: vals}))


def add_to_list(name, value):
    value = " ".join(str(value or "").split())
    if value and value.lower() not in {v.lower() for v in get_list(name)}:
        _append(name, [{COLUMNS[name][0]: value}])
        clear_cache()


def rename_in_matches(field, old_values, new_value):
    """Change a match field (format, coach, opponent) from any of old_values to new_value, in one write."""
    m = load("matches")
    if field == "coach":                 # a match can have two coaches: change the name inside "A & B" too
        from stats import split_coaches, join_coaches
        olds = {str(o).lower() for o in old_values}
        before = m["coach"].fillna("").astype(str)
        after = before.map(lambda c: join_coaches([new_value if x.lower() in olds else x for x in split_coaches(c)])
                           if any(x.lower() in olds for x in split_coaches(c)) else c)
        hit = after != before
        if hit.any():
            m["coach"] = m["coach"].astype(object)
            m.loc[hit, "coach"] = after[hit]
            _overwrite("matches", m)
        clear_cache()
        return int(hit.sum())
    hit = m[field].fillna("").astype(str).isin(set(old_values))
    if hit.any():
        m[field] = m[field].astype(object)
        m.loc[hit, field] = new_value
        _overwrite("matches", m)
    clear_cache()
    return int(hit.sum())


# ---------------- squad list + remembered spellings ----------------
_HEADER_WORDS = {"name", "names", "full name", "player", "player name", "players"}


def squad_names():
    """Full names from the squad tab, in the order typed (blank rows and repeats dropped)."""
    df = load("squad")
    out = []
    for n in df["Full name"].fillna("").astype(str):
        n = " ".join(n.split())
        if n and n.lower() not in _HEADER_WORDS and n not in out:
            out.append(n)
    return out


def squad_duplicates():
    names = [" ".join(str(n).split()) for n in load("squad")["Full name"].fillna("") if str(n).strip()]
    seen, dup = set(), []
    for n in names:
        k = n.lower()
        if k in seen and n not in dup:
            dup.append(n)
        seen.add(k)
    return dup


def spellings():
    """{scorecard name (lower-case, marks removed): (full name, in squad?)}"""
    from stats import name_key
    df = load("spellings")
    out = {}
    for raw, full, ins in zip(df["Scorecard name"].fillna(""), df["Full name"].fillna(""), df["In squad"].fillna("")):
        raw, full = str(raw).strip(), " ".join(str(full).split())
        if raw and full:
            out[name_key(raw)] = (full, str(ins).strip().lower() not in ("no", "n", "false", "0"))
    return out


def save_spellings(updates):
    """updates: {scorecard name: (full name, in squad?)} — added or replaced in the spellings tab."""
    from stats import name_key
    if not updates:
        return
    df = load("spellings")
    keys = df["Scorecard name"].fillna("").astype(str).map(name_key)
    drop = keys.isin({name_key(r) for r in updates})
    new = pd.DataFrame([{"Scorecard name": r, "Full name": f, "In squad": "Yes" if ins else "No"}
                        for r, (f, ins) in updates.items()], columns=COLUMNS["spellings"])
    out = pd.concat([df[~drop], new], ignore_index=True)
    _overwrite("spellings", out)


def add_to_squad(names):
    """Add new players to the squad tab (names already there, in any case, are skipped)."""
    have = {n.lower() for n in squad_names()}
    new = []
    for n in names:
        n = " ".join(str(n).split())
        if n and n.lower() not in have:
            have.add(n.lower())
            new.append({"Full name": n})
    _append("squad", new)
    clear_cache()
    return len(new)


def move_player_rows(wrong, correct, match_ids):
    """Move one player's entries in the chosen matches to another player (a wrong name link).
    Only those matches change; the player's other matches stay as they are. One read and one write
    per tab. Returns {tab: rows moved}."""
    ids = {str(m) for m in match_ids}
    wrong, correct = str(wrong), str(correct)
    moved = {}
    if not ids or not wrong.strip() or not correct.strip() or wrong == correct:
        return moved
    # batting also names him as bowler/fielder on older rows (same columns rename_many updates)
    for name, cols in (("batting", ("player", "bowler", "fielder")), ("bowling", ("player",)),
                       ("fielding", ("player",))):
        df = load(name)
        if df.empty:
            continue
        in_match = df["match_id"].astype(str).isin(ids)
        n = 0
        for col in cols:
            if col not in df:
                continue
            hit = in_match & (df[col].fillna("").astype(str) == wrong)
            if hit.any():
                df.loc[hit, col] = correct
                n += int(hit.sum())
        if n:
            _overwrite(name, df)
            moved[name] = n
    clear_cache()
    return moved


def relink_names(pairs, remember=None, new_players=()):
    """Rename saved players to their squad-list full names in one pass: {saved name: full name}.
    new_players are typed full names to add to the squad tab first."""
    add_to_squad(new_players)
    rename_many(pairs)
    save_spellings(remember or {})
    clear_cache()


# ---------------- bowling type (pace / spin), stored once per player ----------------
def bowling_types():
    """{player: 'Pace' | 'Spin'}"""
    df = load("players")
    out = {}
    for name, t in zip(df["player"].fillna("").astype(str), df["bowling_type"].fillna("").astype(str)):
        if name.strip() and t in BOWLING_TYPES:
            out[name] = t
    return out


def _save_types(types):
    rows = sorted((n, t) for n, t in types.items() if t in BOWLING_TYPES)
    _overwrite("players", pd.DataFrame(rows, columns=COLUMNS["players"]))


def set_bowling_types(updates):
    """Add or change bowling types: {player: 'Pace' | 'Spin' | ''} ('' clears it)."""
    types = bowling_types()
    changed = False
    for name, t in updates.items():
        if t in BOWLING_TYPES and types.get(name) != t:
            types[name], changed = t, True
        elif not t and name in types:
            types.pop(name)
            changed = True
    if changed:
        _save_types(types)
    clear_cache()


# ---------------- white ball / red ball ----------------
def update_matches(updates):
    """Change details of saved matches: {match_id: {"ball": ..., "venue": ..., "opponent": ...}}."""
    m = load("matches")
    ids = m["match_id"].astype(str)
    changed = False
    for mid, fields in updates.items():
        hit = ids == str(mid)
        if not hit.any():
            continue
        for col, val in fields.items():
            if col in COLUMNS["matches"] and col != "match_id":
                m[col] = m[col].astype(object)
                m.loc[hit, col] = val
                changed = True
    if changed:
        _overwrite("matches", m)
    clear_cache()


def set_match_ball(updates):
    """{match_id: 'White ball' | 'Red ball'} for matches already saved."""
    update_matches({mid: {"ball": b} for mid, b in updates.items()})


# ---------------- duplicates ----------------
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


def export_excel(path):
    from stats import career_batting, career_bowling, enrich
    m, bat, bowl, field = load("matches"), load("batting"), load("bowling"), load("fielding")
    _, bat_e, bowl_e, field_e = enrich(m, bat, bowl, field)
    types = bowling_types()
    bowl_e["bowl_type"] = bowl_e["player"].map(types).fillna("Not set")
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        m.to_excel(xl, sheet_name="Matches", index=False)
        bat.to_excel(xl, sheet_name="Batting", index=False)
        bowl.to_excel(xl, sheet_name="Bowling", index=False)
        field.to_excel(xl, sheet_name="Fielding", index=False)
        load("players").to_excel(xl, sheet_name="Bowling types", index=False)
        career_batting(bat_e, fielding=field_e).drop(columns=["Pos"], errors="ignore") \
            .to_excel(xl, sheet_name="Career Batting", index=False)
        career_bowling(bowl_e).to_excel(xl, sheet_name="Career Bowling", index=False)
    return path
