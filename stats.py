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


def suggest_similar_names(names):
    """Return likely same-player name pairs to help merge scorer variations."""
    from difflib import SequenceMatcher
    names = sorted({n for n in names if isinstance(n, str) and n.strip()})
    out = []
    for i, a in enumerate(names):
        ta = set(a.lower().split())
        for b in names[i + 1:]:
            tb = set(b.lower().split())
            ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
            subset = bool(ta and tb and (ta <= tb or tb <= ta))
            if ratio >= 0.82 or subset:
                out.append({"Name 1": a, "Name 2": b, "Similarity": round(ratio, 2),
                            "Why": "one name contains the other" if subset and ratio < 0.82 else "similar spelling"})
    out.sort(key=lambda r: r["Similarity"], reverse=True)
    return pd.DataFrame(out, columns=["Name 1", "Name 2", "Similarity", "Why"])


# ---------------- Name cleanup helpers ----------------
import difflib
from collections import defaultdict


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


def suggest_name_groups(names, threshold=0.85):
    """Cluster look-alike names. Returns groups (lists) of 2+ similar names to review."""
    names = [n for n in dict.fromkeys(names) if isinstance(n, str) and n.strip()]
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if _similar(names[i], names[j], threshold):
                parent[find(names[i])] = find(names[j])

    groups = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)
    return sorted([sorted(g) for g in groups.values() if len(g) > 1])


def suggest_similar_names(names, threshold=0.85):
    """Suggestions as a DataFrame for display: one row per group of look-alike names."""
    groups = suggest_name_groups(names, threshold)
    return pd.DataFrame([{"Possible same player": ", ".join(g)} for g in groups])
