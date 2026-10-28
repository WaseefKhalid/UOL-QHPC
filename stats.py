"""Career tables, filter helpers (dates, formats, batting positions) and name cleanup."""
import difflib
import re

import numpy as np
import pandas as pd

BAT_COLS = ["Player", "Inns", "NO", "Runs", "Balls", "HS", "Avg", "SR", "50s", "100s", "4s", "6s", "Catches"]
BOWL_COLS = ["Player", "Inns", "Overs", "Mdns", "Runs", "Wkts", "Avg", "Econ", "Best", "3W+", "Wd", "NB"]
NO_FORMAT = "Not recorded"
NOT_SET = "Not set"
BALLS = ("White ball", "Red ball")
VENUES = ("Home", "Away")
OUTCOMES = ("Won", "Lost", "Tied", "Drawn", "No result")
INTRA = "Intra-squad"
FORMAT_CHOICES = ["Inter Academy Senior Team Match", "Inter Academy Junior Team Match",
                  "Intra Academy Senior Match", "Intra Academy Junior Match",
                  "PCB Official Club Cricket Match"]


def overs_from_scorecard(meta):
    """Overs per side from the scorecard: the 'Overs' line, else a full innings like '(20.0)'."""
    try:
        v = float(str(meta.get("overs") or "").strip())
        if v > 0:
            return int(round(v))
    except ValueError:
        pass
    full = [score_parts(meta.get(k))[2] for k in ("score_a", "score_b")]
    full = [o for o in full if o and float(o).is_integer()]
    return int(max(full)) if full else None


def guess_ball(fmt, overs=""):
    """White or red ball from the match format text (None if it can't tell)."""
    f = str(fmt or "").lower()
    if re.search(r"\b(t20|t10|odi|one[- ]day|limited|hundred|\d+\s*overs?)\b", f):
        return "White ball"
    if re.search(r"\b(test|first[- ]class|multi[- ]?day|\d+[- ]?days?|two[- ]day|three[- ]day|four[- ]day|unlimited)\b", f):
        return "Red ball"
    try:
        return "White ball" if 0 < float(overs) <= 50 else None
    except (TypeError, ValueError):
        return None


# ---------------- small helpers ----------------
def overs_to_balls(o):
    """Cricket overs (4.3 = 4 overs 3 balls) -> balls."""
    try:
        o = float(o or 0)
    except (TypeError, ValueError):
        return 0
    whole = int(o)
    return whole * 6 + int(round((o - whole) * 10))


def balls_to_overs(b):
    return float(f"{b // 6}.{b % 6}")


def _avg(runs, outs):
    return round(runs / outs, 2) if outs > 0 else None


def _sr(runs, balls):
    return round(runs / balls * 100, 2) if balls > 0 else None


# ---------------- career tables ----------------
CATCHES = ("caught", "caught & bowled")


def career_batting(bat, fielding=None):
    """Batting table per player. Catches come from `fielding` (one row per catch/stumping/
    run-out by your players); without it, from the fielder column of the batting rows."""
    if bat is None or bat.empty:
        return pd.DataFrame(columns=BAT_COLS)
    if fielding is not None:
        catches = fielding.loc[fielding["dismissal"].isin(CATCHES), "player"].value_counts()
    else:
        caught = bat[bat["how_out"].isin(CATCHES)]
        catches = caught["fielder"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().value_counts()
    has_pos = "position" in bat.columns

    rows = []
    for player, g in bat.groupby("player"):
        inns = len(g)
        no = int((g["how_out"] == "not out").sum())
        runs = int(g["runs"].sum())
        balls = int(g["balls"].sum())
        row = {
            "Player": player, "Inns": inns, "NO": no, "Runs": runs, "Balls": balls,
            "HS": int(g["runs"].max()), "Avg": _avg(runs, inns - no), "SR": _sr(runs, balls),
            "50s": int(((g["runs"] >= 50) & (g["runs"] < 100)).sum()),
            "100s": int((g["runs"] >= 100).sum()),
            "4s": int(g["fours"].sum()), "6s": int(g["sixes"].sum()),
            "Catches": int(catches.get(player, 0)),
        }
        if has_pos:
            p = g["position"].dropna()
            row["Pos"] = int(p.mode().iloc[0]) if not p.empty else None
        rows.append(row)
    cols = BAT_COLS[:1] + (["Pos"] if has_pos else []) + BAT_COLS[1:]
    out = pd.DataFrame(rows, columns=cols).sort_values("Runs", ascending=False, ignore_index=True)
    for c in ("Avg", "SR"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if has_pos:
        out["Pos"] = out["Pos"].astype("Int64")
    return out


def career_bowling(bowl):
    has_type = bowl is not None and "bowl_type" in bowl.columns
    cols = BOWL_COLS[:1] + (["Type"] if has_type else []) + BOWL_COLS[1:]
    if bowl is None or bowl.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for player, g in bowl.groupby("player"):
        balls_each = g["overs"].map(overs_to_balls)
        balls = int(balls_each.sum())
        runs = int(g["runs"].sum())
        wkts = int(g["wickets"].sum())
        runs_with_overs = int(g.loc[balls_each > 0, "runs"].sum())  # screenshot rows have no overs
        best = g.sort_values(["wickets", "runs"], ascending=[False, True]).iloc[0]
        rows.append({
            "Player": player, "Inns": len(g), "Overs": balls_to_overs(balls),
            "Mdns": int(g["maidens"].sum()), "Runs": runs, "Wkts": wkts,
            "Avg": round(runs / wkts, 2) if wkts > 0 else None,
            "Econ": round(runs_with_overs / balls * 6, 2) if balls > 0 else None,
            "Best": f"{int(best['wickets'])}/{int(best['runs'])}",
            "3W+": int((g["wickets"] >= 3).sum()),
            "Wd": int(g["wides"].sum()), "NB": int(g["no_balls"].sum()),
            **({"Type": g["bowl_type"].iloc[0]} if has_type else {}),
        })
    out = pd.DataFrame(rows, columns=cols)
    for c in ("Avg", "Econ"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.sort_values(["Wkts", "Avg"], ascending=[False, True], ignore_index=True)


def batting_by_position(bat):
    """One player's batting split by batting position."""
    rows = []
    for pos, g in bat.dropna(subset=["position"]).groupby("position"):
        inns = len(g)
        no = int((g["how_out"] == "not out").sum())
        runs, balls = int(g["runs"].sum()), int(g["balls"].sum())
        rows.append({"Pos": int(pos), "Inns": inns, "Runs": runs, "HS": int(g["runs"].max()),
                     "Avg": _avg(runs, inns - no), "SR": _sr(runs, balls)})
    out = pd.DataFrame(rows, columns=["Pos", "Inns", "Runs", "HS", "Avg", "SR"])
    for c in ("Avg", "SR"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def player_overview(bat, bowl, fielding=None):
    """One row per player with headline batting + bowling numbers (used by Player search)."""
    cb = career_batting(bat, fielding)[["Player", "Inns", "Runs", "HS", "Avg", "SR"]]
    cb = cb.rename(columns={"Inns": "Bat inns", "Avg": "Bat avg"})
    cw = career_bowling(bowl)[["Player", "Inns", "Wkts", "Best", "Avg", "Econ"]]
    cw = cw.rename(columns={"Inns": "Bowl inns", "Avg": "Bowl avg"})
    both = pd.concat([bat[["player", "team", "match_id"]], bowl[["player", "team", "match_id"]]])
    base = both.groupby("player").agg(
        Teams=("team", lambda t: ", ".join(sorted({x for x in t if x}))),
        Matches=("match_id", "nunique")).reset_index().rename(columns={"player": "Player"})
    out = base.merge(cb, on="Player", how="left").merge(cw, on="Player", how="left")
    for c in ("Bat inns", "Runs", "HS", "Bowl inns", "Wkts"):
        out[c] = out[c].fillna(0).astype(int)
    return out.sort_values(["Matches", "Runs"], ascending=False, ignore_index=True)


# ---------------- dates, formats, positions ----------------
def parse_dates(values):
    """Parse the mixed date strings found in the data (STUMPS 'Date & Time', manual ISO dates,
    Google Sheets' own formatting) into dates. Day-first unless the data clearly shows month-first."""
    s = pd.Series(values).fillna("").astype(str).str.strip()
    clean = (s.str.replace(r"(\d)(st|nd|rd|th)\b", r"\1", regex=True)
              .str.replace(r"[|,]", " ", regex=True)
              .str.replace(r"\s+at\s+", " ", regex=True)
              .str.replace(r"\s+", " ", regex=True).str.strip())
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    iso = clean.str.match(r"^\d{4}-\d{1,2}-\d{1,2}")
    if iso.any():
        part = clean[iso].str.extract(r"^(\d{4}-\d{1,2}-\d{1,2})")[0]
        out[iso] = pd.to_datetime(part, format="%Y-%m-%d", errors="coerce")

    serial = clean.str.fullmatch(r"\d{5}(\.\d+)?")  # spreadsheet day numbers
    if serial.any():
        out[serial] = pd.Timestamp("1899-12-30") + pd.to_timedelta(clean[serial].astype(float), unit="D")

    rest = ~iso & ~serial & (clean != "")
    if rest.any():
        nums = clean[rest].str.extract(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}")
        a, b = pd.to_numeric(nums[0], errors="coerce"), pd.to_numeric(nums[1], errors="coerce")
        month_first = bool((b > 12).any()) and not bool((a > 12).any())
        out[rest] = pd.to_datetime(clean[rest], format="mixed", dayfirst=not month_first, errors="coerce")
    return out.dt.normalize()


def _lookup(keys, column):
    """column[key] for each key (NaN where missing). Safe on empty data, unlike Series.map."""
    return pd.Series(column.reindex(keys.to_numpy()).to_numpy(), index=keys.index)


def _side(name, team_a, team_b):
    """'A' / 'B' / None — which team a name refers to (exact, then near spelling)."""
    k, a, b = team_key(name), team_key(team_a), team_key(team_b)
    if not k:
        return None
    if k == a:
        return "A"
    if k == b:
        return "B"
    ra = difflib.SequenceMatcher(None, k, a).ratio() if a else 0
    rb = difflib.SequenceMatcher(None, k, b).ratio() if b else 0
    if max(ra, rb) < 0.6:
        return None
    return "A" if ra >= rb else "B"


def guess_outcome(result, team_a, team_b, our_side):
    """Won / Lost / Tied / Drawn / No result for your side, read from the result line.
    None when the line can't be read."""
    r = str(result or "").lower()
    if not r.strip() or our_side not in ("A", "B"):
        return None
    if re.search(r"\btie[d]?\b", r):
        return "Tied"
    if re.search(r"\b(draw|drawn)\b", r):
        return "Drawn"
    if re.search(r"\b(no result|abandon|abandoned|washed out)\b", r):
        return "No result"
    m = re.search(r"^(.*?)\s+(won|win|beat|beats)\b", str(result), re.I)
    if not m:
        return None
    winner = _side(m.group(1), team_a, team_b)
    if winner is None:
        return None
    return "Won" if winner == our_side else "Lost"


def match_table(m_df):
    m = m_df.copy()
    m["match_id"] = m["match_id"].astype(str)
    for c in ("date", "format", "team_a", "team_b", "score_a", "score_b", "result", "our_team", "type"):
        m[c] = m[c].fillna("").astype(str).str.strip() if c in m else ""
    m["date_dt"] = parse_dates(m["date"])
    m["format_label"] = m["format"].str.replace(r"\s+", " ", regex=True).replace("", NO_FORMAT)
    col = lambda c: (m[c] if c in m else pd.Series("", index=m.index)).fillna("").astype(str).str.strip()
    m["ball_label"] = col("ball").where(lambda b: b.isin(BALLS), NOT_SET)
    m["venue_label"] = col("venue").where(lambda v: v.isin(VENUES), NOT_SET)

    sides, opps, ours, theirs, outcomes = [], [], [], [], []
    for r, opp, out in zip(m.to_dict("records"), col("opponent"), col("outcome")):
        if r["type"] == "intra" or " & " in r["our_team"]:
            side = "AB"
        else:
            side = _side(r["our_team"], r["team_a"], r["team_b"])
        sides.append(side)
        if side in ("A", "B"):
            other = "B" if side == "A" else "A"
            opps.append(opp or r[f"team_{other.lower()}"])
            ours.append(r[f"score_{side.lower()}"])
            theirs.append(r[f"score_{other.lower()}"])
            outcomes.append(out if out in OUTCOMES else
                            (guess_outcome(r["result"], r["team_a"], r["team_b"], side) or NOT_SET))
        else:
            opps.append(opp)
            ours.append("")
            theirs.append("")
            outcomes.append(INTRA if side == "AB" else (out if out in OUTCOMES else NOT_SET))
    m["our_side"], m["opponent_label"] = sides, opps
    m["our_score"], m["opp_score"], m["outcome_label"] = ours, theirs, outcomes
    return m


FIELD_COLS = ["match_id", "date", "type", "team", "player", "dismissal"]
FIELDING_DISMISSALS = ("caught", "caught & bowled", "stumped", "run out")


def enrich(m_df, bat, bowl, field=None):
    """Attach date, match format, opponent and batting position to every row.

    Batting position comes from the `pos` column when it was saved; for older rows it is
    the batting order inside the match (rows are stored in scorecard order). Screenshot
    uploads only list top scorers, so their position is left unknown.

    Fielding: rows from the fielding tab, plus — for older matches saved before that tab
    existed — catches read from the fielder column of the batting rows."""
    m = match_table(m_df)
    info = m.drop_duplicates("match_id").set_index("match_id")
    screenshot_ids = set(m.loc[(m["date"] == "") & ~m["match_id"].str.startswith("manual-"), "match_id"])

    def attach(df):
        df = df.copy()
        df["match_id"] = df["match_id"].astype(str)
        df["player"] = df["player"].fillna("").astype(str)
        df = df[df["player"].str.strip() != ""].copy()
        df["team"] = df["team"].fillna("").astype(str)
        dates = _lookup(df["match_id"], info["date_dt"])
        missing = dates.isna()
        if missing.any():
            dates[missing] = parse_dates(df.loc[missing, "date"])
        df["date_dt"] = pd.to_datetime(dates)
        df["format_label"] = _lookup(df["match_id"], info["format_label"]).fillna(NO_FORMAT)
        df["ball_label"] = _lookup(df["match_id"], info["ball_label"]).fillna(NOT_SET)
        df["venue_label"] = _lookup(df["match_id"], info["venue_label"]).fillna(NOT_SET)
        ta = _lookup(df["match_id"], info["team_a"]).fillna("")
        tb = _lookup(df["match_id"], info["team_b"]).fillna("")
        df["opponent"] = np.where(df["team"] == ta, tb, np.where(df["team"] == tb, ta, ""))
        # your-team rows in a normal match use the cleaned-up opposition name
        clean = _lookup(df["match_id"], info["opponent_label"]).fillna("")
        normal = _lookup(df["match_id"], info["our_side"]).isin(["A", "B"])
        df["opponent"] = np.where(normal & (clean != ""), clean, df["opponent"])
        return df

    field = (field.copy() if field is not None else pd.DataFrame(columns=FIELD_COLS)).reindex(columns=FIELD_COLS)
    field["match_id"] = field["match_id"].astype(str)
    old = bat.copy()
    old["match_id"] = old["match_id"].astype(str)
    old = old[~old["match_id"].isin(set(field["match_id"])) & old["how_out"].isin(FIELDING_DISMISSALS)]
    old_f = old["fielder"].fillna("").astype(str).str.strip()
    old = old[old_f != ""]
    if not old.empty:
        ta = _lookup(old["match_id"], info["team_a"]).fillna("")
        tb = _lookup(old["match_id"], info["team_b"]).fillna("")
        team = old["team"].fillna("").astype(str)
        derived_f = pd.DataFrame({"match_id": old["match_id"], "date": old["date"], "type": old["type"],
                                  "team": np.where(team == ta, tb, ta), "player": old_f[old_f != ""],
                                  "dismissal": old["how_out"]})
        field = pd.concat([field, derived_f], ignore_index=True)

    bat, bowl, field = attach(bat), attach(bowl), attach(field)
    saved = pd.to_numeric(bat["pos"], errors="coerce").fillna(0) if "pos" in bat else pd.Series(0, index=bat.index)
    order = bat.groupby(["match_id", "team"], sort=False).cumcount() + 1
    derived = order.where(~bat["match_id"].isin(screenshot_ids))
    bat["position"] = saved.where(saved > 0, derived).astype("Int64")
    return m, bat, bowl, field


def filter_rows(df, date_range=None, include_undated=True, formats=None, positions=None, bowl_types=None,
                balls=None, venues=None):
    out = df
    if date_range:
        lo, hi = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
        ok = out["date_dt"].between(lo, hi)
        if include_undated:
            ok |= out["date_dt"].isna()
        out = out[ok]
    if formats:
        out = out[out["format_label"].isin(formats)]
    if positions:
        out = out[out["position"].isin(positions).fillna(False).astype(bool)]
    if bowl_types:
        out = out[out["bowl_type"].isin(bowl_types)]
    if balls:
        out = out[out["ball_label"].isin(balls)]
    if venues:
        out = out[out["venue_label"].isin(venues)]
    return out


# ---------------- team results ----------------
def team_record(m):
    """Played / won / lost ... for your team. Intra-squad games are left out.
    Win % = won ÷ matches with a result (no-results don't count either way)."""
    m = m[m["outcome_label"] != INTRA]
    oc = m["outcome_label"]
    rec = {k: int((oc == k).sum()) for k in OUTCOMES}
    rec["Played"] = len(m)
    rec["Not set"] = int((oc == NOT_SET).sum())
    decided = rec["Played"] - rec["No result"] - rec["Not set"]
    rec["Win %"] = round(rec["Won"] / decided * 100, 1) if decided else None
    return rec


def record_table(m, by, label):
    """One row per value of `by` (opponent, venue, ball, format) with P / W / L / T / D / NR / Win %."""
    m = m[m["outcome_label"] != INTRA]
    rows = []
    for key, g in m.groupby(by):
        r = team_record(g)
        last = g.sort_values("date_dt").iloc[-1]
        rows.append({label: key or "—", "P": r["Played"], "W": r["Won"], "L": r["Lost"], "T": r["Tied"],
                     "D": r["Drawn"], "NR": r["No result"], "Win %": r["Win %"],
                     "Last result": last["outcome_label"]})
    out = pd.DataFrame(rows, columns=[label, "P", "W", "L", "T", "D", "NR", "Win %", "Last result"])
    out["Win %"] = pd.to_numeric(out["Win %"], errors="coerce")
    return out.sort_values(["P", "Win %"], ascending=False, ignore_index=True)


def name_clusters(names, threshold=0.8):
    """Groups of names that are probably the same team (case/punctuation only, or close spellings)."""
    by_key = {}
    for n in names:
        if str(n).strip():
            by_key.setdefault(team_key(n), []).append(n)
    reps = {v[0]: v for v in by_key.values()}
    groups = []
    for g in suggest_name_groups(list(reps), threshold):
        groups.append(sorted({x for r in g for x in reps[r]}))
    seen = {x for g in groups for x in g}
    groups += [sorted(v) for v in by_key.values() if len(v) > 1 and not set(v) & seen]
    return sorted(groups)


def closest_name(name, known, threshold=0.8):
    """The known name this one most likely means (or None)."""
    k = team_key(name)
    best, score = None, 0
    for n in known:
        kn = team_key(n)
        if kn == k:
            return n
        s = difflib.SequenceMatcher(None, k, kn).ratio()
        if kn and (re.search(rf"\b{re.escape(kn)}\b", k) or re.search(rf"\b{re.escape(k)}\b", kn)):
            s = max(s, 0.9)
        if s > score:
            best, score = n, s
    return best if score >= threshold else None


def guess_type(name, types):
    """(type, known name) when a near-identical spelling already has a bowling type."""
    for known, t in types.items():
        if known.lower().strip() == name.lower().strip() or _similar(known, name, 0.88):
            return t, known
    return None, None


# ---------------- your team ----------------
def team_key(name):
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def team_matches(team, our_names, threshold=0.85):
    """True if a team name is one of yours: same name ignoring case/punctuation, a near
    spelling, or your name plus extra words ("UOL QHPC Whites" for "UOL QHPC")."""
    t = team_key(team)
    if not t:
        return False
    for n in our_names:
        k = team_key(n)
        if not k:
            continue
        if t == k or re.search(rf"\b{re.escape(k)}\b", t):
            return True
        if difflib.SequenceMatcher(None, t, k).ratio() >= threshold:
            return True
    return False


def default_side(team_a, team_b, our_names):
    """'A', 'B', 'AB' (both are yours) or None (can't tell)."""
    hits = [s for s, t in (("A", team_a), ("B", team_b)) if team_matches(t, our_names)]
    if len(hits) == 2:
        return "AB"
    return hits[0] if hits else None


# ---------------- duplicate matches ----------------
def score_parts(score):
    """'150-5 in 20.0 overs' -> (150, 5, 20.0). Missing parts come back as None."""
    s = str(score or "")
    m = re.search(r"(\d+)\s*[-/]\s*(\d+)", s)
    if m:
        runs, wkts = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"\d+", s)
        runs, wkts = (int(m.group()) if m else None), None
    o = re.search(r"in\s*([\d.]+)\s*overs?", s)
    return runs, wkts, (float(o.group(1)) if o else None)


def match_source(match_id, date=""):
    mid = str(match_id)
    if mid.startswith("manual-"):
        return "Manual entry"
    if mid.startswith("ss-") or not str(date or "").strip():
        return "Screenshot"
    return "PDF"


def _team_same(a, b):
    ka, kb = team_key(a), team_key(b)
    return bool(ka) and bool(kb) and (ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.8)


def _pair_innings(a, b):
    """Line up the two innings of match a with match b (teams may be listed in either order)."""
    ia = [(a.get("team_a", ""), a.get("score_a", "")), (a.get("team_b", ""), a.get("score_b", ""))]
    ib = [(b.get("team_a", ""), b.get("score_a", "")), (b.get("team_b", ""), b.get("score_b", ""))]
    for order in (ib, ib[::-1]):
        if all(_team_same(x[0], y[0]) for x, y in zip(ia, order)):
            return list(zip(ia, order))
    return None


def _bat_frame(rows):
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    if df.empty or "player" not in df:
        return pd.DataFrame(columns=["key", "player", "runs", "balls"])
    df = df[["player", "runs", "balls"]].copy()
    df["player"] = df["player"].fillna("").astype(str).str.strip()
    df["key"] = df["player"].str.lower()
    df["runs"] = pd.to_numeric(df["runs"], errors="coerce").fillna(0).astype(int)
    df["balls"] = pd.to_numeric(df["balls"], errors="coerce").fillna(0).astype(int)
    return df[df["key"] != ""].drop_duplicates("key")


def batting_overlap(rows_a, rows_b):
    """(players in both, of those how many have identical runs and balls)."""
    a, b = _bat_frame(rows_a), _bat_frame(rows_b)
    both = a.merge(b, on="key", suffixes=("_a", "_b"))
    same = int(((both["runs_a"] == both["runs_b"]) & (both["balls_a"] == both["balls_b"])).sum())
    return len(both), same


def duplicate_evidence(a, b, bat_a=None, bat_b=None):
    """Is match b the same match as match a? Returns (level, reasons):
    level is 'certain' (same match ID), 'likely', 'possible' or None (not a duplicate)."""
    reasons = []
    same_id = str(a.get("match_id", "")).strip() and str(a.get("match_id")) == str(b.get("match_id"))
    if same_id:
        reasons.append(f"Same match ID ({a['match_id']})")

    pairs = _pair_innings(a, b)
    if pairs is None:
        return ("certain", reasons) if same_id else (None, [])
    reasons.append("Same two teams")

    compared, shown = 0, []
    for (ta, sa), (tb, sb) in pairs:
        ra, wa, _ = score_parts(sa)
        rb, wb, _ = score_parts(sb)
        if ra is None or rb is None:
            continue
        if ra != rb or (wa is not None and wb is not None and wa != wb):
            return ("certain", reasons) if same_id else (None, [])
        compared += 1
        shown.append(f"{ta} {ra}" + (f"-{wa}" if wa is not None else ""))
    if compared == 0 and not same_id:
        return None, []
    if shown:
        reasons.append("Same score: " + ", ".join(shown))

    da, db_ = parse_dates([a.get("date", ""), b.get("date", "")])
    if pd.notna(da) and pd.notna(db_):
        if da != db_ and not same_id:
            return None, []
        if da == db_:
            reasons.append(f"Same date ({da:%d %b %Y})")
    else:
        reasons.append("Date missing on one of them")

    level = "certain" if same_id else ("likely" if compared == 2 else "possible")
    if bat_a is not None and bat_b is not None:
        common, same = batting_overlap(bat_a, bat_b)
        if common:
            reasons.append(f"{same} of {common} batters in both have identical runs and balls")
            if not same_id and same < common / 2:
                level = "possible"
    return level, reasons


DUP_LABELS = {"certain": "🟥 Certain duplicate", "likely": "🟧 Very likely duplicate",
              "possible": "🟨 Possible duplicate"}


def match_proof(a, b, label_a, label_b, bat_a=None, bat_b=None, bowl_a=None, bowl_b=None):
    """Side-by-side table of two matches with a ✅/❌ per field."""
    def score_line(m, team):
        for t, s in ((m.get("team_a", ""), m.get("score_a", "")), (m.get("team_b", ""), m.get("score_b", ""))):
            if _team_same(t, team):
                return f"{t}: {s or '—'}"
        return "—"

    def same_score(x, y):
        rx, wx, _ = score_parts(x.split(": ", 1)[-1])
        ry, wy, _ = score_parts(y.split(": ", 1)[-1])
        return rx is not None and rx == ry and (wx is None or wy is None or wx == wy)

    da, db_ = parse_dates([a.get("date", ""), b.get("date", "")])
    rows = [
        ("Match ID", str(a.get("match_id", "")), str(b.get("match_id", "")), None),
        ("Source", match_source(a.get("match_id"), a.get("date")), match_source(b.get("match_id"), b.get("date")), ""),
        ("Date", f"{da:%d %b %Y}" if pd.notna(da) else "—", f"{db_:%d %b %Y}" if pd.notna(db_) else "—", None),
        ("Format", a.get("format") or "—", b.get("format") or "—", None),
    ]
    for team in (a.get("team_a", ""), a.get("team_b", "")):
        x, y = score_line(a, team), score_line(b, team)
        rows.append(("Score", x, y, "✅" if same_score(x, y) else "❌"))
    rows.append(("Result", a.get("result") or "—", b.get("result") or "—", None))
    if bat_a is not None and bat_b is not None:
        rows.append(("Batting rows", str(len(bat_a)), str(len(bat_b)), ""))
    if bowl_a is not None and bowl_b is not None:
        rows.append(("Bowling rows", str(len(bowl_a)), str(len(bowl_b)), ""))

    out = []
    for field, x, y, mark in rows:
        if mark is None:
            mark = "—" if "—" in (x, y) else ("✅" if team_key(x) == team_key(y) else "❌")
        out.append({"": field, label_a: x, label_b: y, "Same?": mark})
    return pd.DataFrame(out)


def batting_proof(rows_a, rows_b, label_a, label_b):
    """Batter-by-batter comparison: runs (balls) in each match."""
    a, b = _bat_frame(rows_a), _bat_frame(rows_b)
    both = a.merge(b, on="key", how="outer", suffixes=("_a", "_b"))
    out = []
    for _, r in both.iterrows():
        has_a, has_b = pd.notna(r["runs_a"]), pd.notna(r["runs_b"])
        out.append({
            "Player": r["player_a"] if has_a else r["player_b"],
            label_a: f"{int(r['runs_a'])} ({int(r['balls_a'])})" if has_a else "—",
            label_b: f"{int(r['runs_b'])} ({int(r['balls_b'])})" if has_b else "—",
            "Same?": ("✅" if (r["runs_a"] == r["runs_b"] and r["balls_a"] == r["balls_b"]) else "❌")
                     if has_a and has_b else "—",
        })
    return pd.DataFrame(out, columns=["Player", label_a, label_b, "Same?"])


def find_duplicate_matches(matches_df, bat_df, ignore=()):
    """Pairs of saved matches that look like the same match. Returns a list of
    dicts {a, b, level, reasons}; `ignore` holds match-id pairs marked 'not duplicates'."""
    ignore = {frozenset(p) for p in ignore}
    recs = matches_df.to_dict("records")
    bats = {mid: g for mid, g in bat_df.groupby("match_id")} if not bat_df.empty else {}
    empty = pd.DataFrame(columns=["player", "runs", "balls"])
    out, order = [], {"certain": 0, "likely": 1, "possible": 2}
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            a, b = recs[i], recs[j]
            ida, idb = str(a["match_id"]), str(b["match_id"])
            if frozenset((ida, idb)) in ignore and ida != idb:
                continue
            level, reasons = duplicate_evidence(a, b, bats.get(ida, empty), bats.get(idb, empty))
            if level:
                out.append({"a": a, "b": b, "level": level, "reasons": reasons})
    return sorted(out, key=lambda d: order[d["level"]])


# ---------------- Name cleanup helpers ----------------
def _similar(a, b, threshold):
    la, lb = a.lower().strip(), b.lower().strip()
    if la == lb:
        return False
    ta, tb = set(la.split()), set(lb.split())
    if ta and tb and (ta < tb or tb < ta):   # one name's words are a subset of the other ("Hasim" vs "Hasim Ijaz")
        return True
    if min(len(la), len(lb)) >= 3 and (la.startswith(lb) or lb.startswith(la)):  # abbreviation ("Saif" vs "Saifullah")
        return True
    return difflib.SequenceMatcher(None, la, lb).ratio() >= threshold


def suggest_name_groups(names, threshold=0.85, ignore=()):
    """Group look-alike names. Each group is anchored to one fuller name, so a shared
    first name (Ali, Abdullah, Muhammad) doesn't chain many players into one blob.
    `ignore` holds name pairs already marked as different players; they are never paired."""
    ignore = {frozenset(p) for p in ignore}
    names = [n for n in dict.fromkeys(names) if isinstance(n, str) and n.strip()]
    order = sorted(names, key=lambda n: (-len(n), n))   # fuller names act as anchors first
    used, groups = set(), []
    for seed in order:
        if seed in used:
            continue
        group = [seed] + [o for o in names
                          if o != seed and o not in used
                          and frozenset((seed, o)) not in ignore
                          and _similar(seed, o, threshold)]
        if len(group) > 1:
            used.update(group)
            groups.append(sorted(group))
    return sorted(groups)


def suggest_similar_names(names, threshold=0.85, ignore=()):
    """Suggestions as a DataFrame for display: one row per group of look-alike names."""
    groups = suggest_name_groups(names, threshold, ignore)
    return pd.DataFrame([{"Possible same player": ", ".join(g)} for g in groups])


def search_names(query, names, limit=25):
    """Names containing the query (any case); if none, the closest spellings."""
    q = re.sub(r"\s+", " ", query.strip().lower())
    if not q:
        return list(names), False
    hits = [n for n in names if q in n.lower()]
    if hits:
        return hits, False
    scored = []
    for n in names:
        parts = [n.lower()] + n.lower().split()
        score = max(difflib.SequenceMatcher(None, q, p).ratio() for p in parts)
        if score >= 0.6:
            scored.append((score, n))
    return [n for _, n in sorted(scored, key=lambda x: -x[0])[:limit]], True
