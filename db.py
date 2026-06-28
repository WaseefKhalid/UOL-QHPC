"""Simple CSV-backed store. The app reads these with pandas; career stats are computed on the fly."""
import os
import pandas as pd

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


def _path(name):
    return os.path.join(DATA_DIR, f"{name}.csv")


def load(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    p = _path(name)
    if os.path.exists(p):
        return pd.read_csv(p, dtype={"match_id": str})
    return pd.DataFrame(columns=COLUMNS[name])


def _save(name, df):
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(_path(name), index=False)


def match_exists(match_id):
    df = load("matches")
    return not df.empty and str(match_id) in df["match_id"].astype(str).values


def append_match(parsed, match_type, our_side):
    """our_side = team name treated as ours. intra keeps both teams; inter keeps our_side only."""
    meta = parsed["meta"]
    keep = lambda team: match_type == "intra" or team == our_side

    matches = load("matches")
    matches = pd.concat([matches, pd.DataFrame([{
        "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
        "our_team": our_side, "team_a": meta["team_a"], "score_a": meta["score_a"],
        "team_b": meta["team_b"], "score_b": meta["score_b"], "result": meta["result"],
        "format": meta["format"], "overs": meta["overs"], "toss": meta["toss"],
    }])], ignore_index=True)
    _save("matches", matches)

    bat = load("batting")
    bat_rows = [{**b, "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
                 "is_ours": b["team"] == our_side} for b in parsed["batting"] if keep(b["team"])]
    if bat_rows:
        bat = pd.concat([bat, pd.DataFrame(bat_rows)[COLUMNS["batting"]]], ignore_index=True)
        _save("batting", bat)

    bowl = load("bowling")
    bowl_rows = [{**b, "match_id": meta["match_id"], "date": meta["date"], "type": match_type,
                  "is_ours": b["team"] == our_side} for b in parsed["bowling"] if keep(b["team"])]
    if bowl_rows:
        bowl = pd.concat([bowl, pd.DataFrame(bowl_rows)[COLUMNS["bowling"]]], ignore_index=True)
        _save("bowling", bowl)

    return len(bat_rows), len(bowl_rows)


def export_excel(path):
    """Write the whole database (raw + career) to one .xlsx for sharing."""
    from stats import career_batting, career_bowling
    bat, bowl = load("batting"), load("bowling")
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        load("matches").to_excel(xl, sheet_name="Matches", index=False)
        bat.to_excel(xl, sheet_name="Batting", index=False)
        bowl.to_excel(xl, sheet_name="Bowling", index=False)
        career_batting(bat).to_excel(xl, sheet_name="Career Batting", index=False)
        career_bowling(bowl).to_excel(xl, sheet_name="Career Bowling", index=False)
    return path
