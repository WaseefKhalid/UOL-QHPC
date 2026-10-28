"""Career tables, filter helpers (dates, formats, batting positions) and name cleanup."""
import difflib
import re

import numpy as np
import pandas as pd

BAT_COLS = ["Player", "Inns", "NO", "Runs", "Balls", "HS", "Avg", "SR", "50s", "100s", "4s", "6s", "Catches"]
BOWL_COLS = ["Player", "Inns", "Overs", "Mdns", "Runs", "Wkts", "Avg", "Econ", "Best", "3W+", "Wd", "NB"]
NO_FORMAT = "Not recorded"


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
def career_batting(bat, catches_from=None):
    """Batting table per player. `catches_from` lets catches be counted from a wider set of
    rows than the batting rows shown (e.g. when filtering by batting position)."""
    if bat is None or bat.empty:
        return pd.DataFrame(columns=BAT_COLS)
    src = bat if catches_from is None else catches_from
    caught = src[src["how_out"].isin(["caught", "caught & bowled"])]
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
    if has_pos:
        out["Pos"] = out["Pos"].astype("Int64")
    return out


def career_bowling(bowl):
    if bowl is None or bowl.empty:
        return pd.DataFrame(columns=BOWL_COLS)
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
        })
    return pd.DataFrame(rows, columns=BOWL_COLS).sort_values(["Wkts", "Avg"], ascending=[False, True],
                                                             ignore_index=True)


def batting_by_position(bat):
    """One player's batting split by batting position."""
    rows = []
    for pos, g in bat.dropna(subset=["position"]).groupby("position"):
        inns = len(g)
        no = int((g["how_out"] == "not out").sum())
        runs, balls = int(g["runs"].sum()), int(g["balls"].sum())
        rows.append({"Pos": int(pos), "Inns": inns, "Runs": runs, "HS": int(g["runs"].max()),
                     "Avg": _avg(runs, inns - no), "SR": _sr(runs, balls)})
    return pd.DataFrame(rows, columns=["Pos", "Inns", "Runs", "HS", "Avg", "SR"])


def player_overview(bat, bowl, catches_from=None):
    """One row per player with headline batting + bowling numbers (used by Player search)."""
    cb = career_batting(bat, catches_from)[["Player", "Inns", "Runs", "HS", "Avg", "SR"]]
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


def match_table(m_df):
    m = m_df.copy()
    m["match_id"] = m["match_id"].astype(str)
    for c in ("date", "format", "team_a", "team_b"):
        m[c] = m[c].fillna("").astype(str).str.strip()
    m["date_dt"] = parse_dates(m["date"])
    m["format_label"] = m["format"].str.replace(r"\s+", " ", regex=True).replace("", NO_FORMAT)
    return m


def enrich(m_df, bat, bowl):
    """Attach date, match format, opponent and batting position to every row.

    Batting position comes from the `pos` column when it was saved; for older rows it is
    the batting order inside the match (rows are stored in scorecard order). Screenshot
    uploads only list top scorers, so their position is left unknown."""
    m = match_table(m_df)
    info = m.drop_duplicates("match_id").set_index("match_id")
    screenshot_ids = set(m.loc[(m["date"] == "") & ~m["match_id"].str.startswith("manual-"), "match_id"])

    def attach(df):
        df = df.copy()
        df["match_id"] = df["match_id"].astype(str)
        df["player"] = df["player"].fillna("").astype(str)
        df = df[df["player"].str.strip() != ""].copy()
        df["team"] = df["team"].fillna("").astype(str)
        dates = df["match_id"].map(info["date_dt"])
        missing = dates.isna()
        if missing.any():
            dates[missing] = parse_dates(df.loc[missing, "date"])
        df["date_dt"] = pd.to_datetime(dates)
        df["format_label"] = df["match_id"].map(info["format_label"]).fillna(NO_FORMAT)
        ta = df["match_id"].map(info["team_a"]).fillna("")
        tb = df["match_id"].map(info["team_b"]).fillna("")
        df["opponent"] = np.where(df["team"] == ta, tb, np.where(df["team"] == tb, ta, ""))
        return df

    bat, bowl = attach(bat), attach(bowl)
    saved = pd.to_numeric(bat["pos"], errors="coerce").fillna(0) if "pos" in bat else pd.Series(0, index=bat.index)
    order = bat.groupby(["match_id", "team"], sort=False).cumcount() + 1
    derived = order.where(~bat["match_id"].isin(screenshot_ids))
    bat["position"] = saved.where(saved > 0, derived).astype("Int64")
    return m, bat, bowl


def filter_rows(df, date_range=None, include_undated=True, formats=None, positions=None):
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
    return out


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
