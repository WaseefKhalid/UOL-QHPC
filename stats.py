"""Compute career batting and bowling tables from the raw rows."""
import pandas as pd

BAT_COLS = ["Player", "Inns", "NO", "Runs", "Balls", "HS", "Avg", "SR", "50s", "100s", "4s", "6s", "Catches"]
BOWL_COLS = ["Player", "Inns", "Overs", "Mdns", "Runs", "Wkts", "Avg", "Econ", "Best", "3W+", "Wd", "NB"]


def career_batting(bat):
    if bat is None or bat.empty:
        return pd.DataFrame(columns=BAT_COLS)
    catches = bat["fielder"].fillna("").replace("", pd.NA).value_counts()
    rows = []
    for player, g in bat.groupby("player"):
        inns = len(g)
        no = int((g["how_out"] == "not out").sum())
        runs = int(g["runs"].sum())
        balls = int(g["balls"].sum())
        outs = inns - no
        rows.append({
            "Player": player, "Inns": inns, "NO": no, "Runs": runs, "Balls": balls,
            "HS": int(g["runs"].max()),
            "Avg": round(runs / outs, 2) if outs > 0 else None,
            "SR": round(runs / balls * 100, 2) if balls > 0 else None,
            "50s": int(((g["runs"] >= 50) & (g["runs"] < 100)).sum()),
            "100s": int((g["runs"] >= 100).sum()),
            "4s": int(g["fours"].sum()), "6s": int(g["sixes"].sum()),
            "Catches": int(catches.get(player, 0)),
        })
    return pd.DataFrame(rows, columns=BAT_COLS).sort_values("Runs", ascending=False, ignore_index=True)


def career_bowling(bowl):
    if bowl is None or bowl.empty:
        return pd.DataFrame(columns=BOWL_COLS)
    rows = []
    for player, g in bowl.groupby("player"):
        overs = round(float(g["overs"].sum()), 1)
        runs = int(g["runs"].sum())
        wkts = int(g["wickets"].sum())
        rows.append({
            "Player": player, "Inns": len(g), "Overs": overs,
            "Mdns": int(g["maidens"].sum()), "Runs": runs, "Wkts": wkts,
            "Avg": round(runs / wkts, 2) if wkts > 0 else None,
            "Econ": round(runs / overs, 2) if overs > 0 else None,
            "Best": int(g["wickets"].max()),
            "3W+": int((g["wickets"] >= 3).sum()),
            "Wd": int(g["wides"].sum()), "NB": int(g["no_balls"].sum()),
        })
    return pd.DataFrame(rows, columns=BOWL_COLS).sort_values("Wkts", ascending=False, ignore_index=True)
