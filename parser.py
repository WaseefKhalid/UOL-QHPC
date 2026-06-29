"""Parse a STUMPS match-report PDF into structured rows."""
import re
import pdfplumber

DISMISSAL_KEYS = {"b", "c", "lbw", "st", "run", "not", "retired"}


def _meta_value(lines, label):
    for l in lines:
        if l.startswith(label + " "):
            return l[len(label) + 1:].strip()
    return ""


def _parse_dismissal(prefix):
    tokens = prefix.split()
    split_at = len(tokens)
    for i, t in enumerate(tokens):
        if t in DISMISSAL_KEYS:
            split_at = i
            break
    player = " ".join(tokens[:split_at]).strip()
    d = " ".join(tokens[split_at:]).strip()

    if d.startswith("not out"):
        return player, "not out", "", ""
    if d.startswith("run out"):
        m = re.search(r"\(([^)]+)\)", d)
        return player, "run out", "", (m.group(1).strip() if m else "")
    if d.startswith("retired"):
        return player, "other", "", ""
    m = re.match(r"^c (?:and|&) b (.+)$", d)
    if m:
        return player, "caught & bowled", m.group(1).strip(), m.group(1).strip()
    m = re.match(r"^c (.+) b (.+)$", d)
    if m:
        return player, "caught", m.group(2).strip(), m.group(1).strip()
    m = re.match(r"^st (.+) b (.+)$", d)
    if m:
        return player, "stumped", m.group(2).strip(), m.group(1).strip()
    m = re.match(r"^lbw (?:b )?(.+)$", d)
    if m:
        return player, "lbw", m.group(1).strip(), ""
    m = re.match(r"^b (.+)$", d)
    if m:
        return player, "bowled", m.group(1).strip(), ""
    return player, "other", "", ""


def _is_num(x):
    return bool(re.fullmatch(r"\d+(\.\d+)?", x))


def _parse_batting(line, team):
    t = line.split()
    if len(t) < 6:
        return None
    tail = t[-5:]
    if not all(_is_num(x) for x in tail):
        return None
    player, how_out, bowler, fielder = _parse_dismissal(" ".join(t[:-5]))
    if not player:
        return None
    return {
        "team": team, "player": player, "how_out": how_out,
        "bowler": bowler, "fielder": fielder,
        "runs": int(float(tail[0])), "balls": int(float(tail[1])),
        "fours": int(float(tail[2])), "sixes": int(float(tail[3])), "sr": float(tail[4]),
    }


def _parse_bowling(line, team):
    t = line.split()
    if len(t) < 11:
        return None
    tail = t[-10:]
    if not all(_is_num(x) for x in tail):
        return None
    player = " ".join(t[:-10]).strip()
    if not player:
        return None
    return {
        "team": team, "player": player,
        "overs": float(tail[0]), "maidens": int(float(tail[1])), "runs": int(float(tail[2])),
        "wickets": int(float(tail[3])), "econ": float(tail[4]), "dots": int(float(tail[5])),
        "fours_conceded": int(float(tail[6])), "sixes_conceded": int(float(tail[7])),
        "wides": int(float(tail[8])), "no_balls": int(float(tail[9])),
    }


def parse_stumps_pdf(file_like):
    """file_like: path or a file/bytes buffer. Returns dict(meta, batting, bowling)."""
    text_parts = []
    with pdfplumber.open(file_like) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text(x_tolerance=1.5) or "")
    raw = "\n".join(text_parts)
    lines = [re.sub(r"\s+", " ", l).strip() for l in raw.split("\n")]

    # Teams + scores from the "Match Summary" header lines, e.g. "ZAMAN QHPC 190-6 (25.0)"
    teams = []
    for l in lines:
        m = re.match(r"^(.+?) (\d+(?:[-/]\d+)?) \((\d+(?:\.\d+)?)\)$", l)
        if m:
            teams.append((m.group(1).strip(), f"{m.group(2)} in {m.group(3)} overs"))
        if len(teams) == 2:
            break
    team_a, score_a = teams[0] if len(teams) > 0 else ("", "")
    team_b, score_b = teams[1] if len(teams) > 1 else ("", "")

    # Result: the line mentioning the outcome, trimmed to start at the winning team.
    result = ""
    for l in lines:
        if re.search(r"\b(won by|tied|match drawn|drawn|no result)\b", l, re.I):
            idxs = [l.index(t) for t in (team_a, team_b) if t and t in l]
            result = l[min(idxs):] if idxs else l
            break

    meta = {
        "match_id": _meta_value(lines, "Match ID"),
        "date": _meta_value(lines, "Date & Time"),
        "format": _meta_value(lines, "Match Format"),
        "overs": _meta_value(lines, "Overs"),
        "result": result,
        "toss": _meta_value(lines, "Toss"),
        "team_a": team_a, "team_b": team_b,
        "score_a": score_a, "score_b": score_b,
    }

    batting, bowling = [], []
    mode, batting_team = None, ""
    for line in lines:
        if re.search(r" R B 4s 6s SR$", line):
            batting_team = re.sub(r" R B 4s 6s SR$", "", line).strip()
            mode = "bat"
            continue
        if line.startswith("Bowler O M R W Eco"):
            mode = "bowl"
            continue
        if re.match(r"^(Extras|Total|Fall Of Wickets|Over Comparison)", line) or line == "":
            if re.match(r"^(Fall Of Wickets|Over Comparison)", line):
                mode = None
            continue
        if mode == "bat":
            row = _parse_batting(line, batting_team)
            if row:
                batting.append(row)
        elif mode == "bowl":
            bowl_team = team_b if batting_team == team_a else team_a
            row = _parse_bowling(line, bowl_team)
            if row:
                bowling.append(row)

    return {"meta": meta, "batting": batting, "bowling": bowling}


# ---------------- Summary screenshot (OCR) ----------------
def _ocr_image(file_like):
    import pytesseract
    from PIL import Image, ImageOps
    img = Image.open(file_like).convert("RGB")
    g = ImageOps.grayscale(img)
    g = ImageOps.invert(g)                       # summary is light text on a dark background
    g = g.resize((g.width * 2, g.height * 2))    # upscale helps OCR
    return pytesseract.image_to_string(g, config="--psm 6")


def parse_summary_image(file_like):
    """Read a STUMPS summary screenshot and return the same structure as parse_stumps_pdf
    (only the top batters/bowlers shown in the summary are captured)."""
    raw = _ocr_image(file_like)
    lines = [re.sub(r"\s+", " ", l).strip() for l in raw.split("\n") if l.strip()]

    match_id = ""
    for l in lines:
        m = re.search(r"#\s*([A-Za-z0-9]{5,})", l)
        if m:
            match_id = m.group(1)
            break

    result = ""
    for l in lines:
        if re.search(r"\b(won by|tied|match drawn|drawn|no result)\b", l, re.I):
            result = re.sub(r"\s*[;:.\-_,]+\s*$", "", l).strip()
            break

    header_re = re.compile(r"^(.+?)\s+Overs\s+([\d.]+)\s+(\d+-\d+)\b", re.I)
    row_re = re.compile(
        r"^(?P<bat>.+?)\s+(?P<runs>\d+)(?P<no>\*?)\s*\((?P<balls>\d+)\)\s+"
        r"(?P<bowl>.+?)\s+(?P<w>\d+)\s*-\s*(?P<r>\d+)\b")

    teams, scores = [], []
    blocks = []          # list of (batting_team_index, [rows])
    cur = None
    for l in lines:
        h = header_re.match(l)
        if h:
            teams.append(h.group(1).strip())
            scores.append(f"{h.group(3)} in {h.group(2)} overs")
            cur = {"team_idx": len(teams) - 1, "rows": []}
            blocks.append(cur)
            continue
        r = row_re.match(l)
        if r and cur is not None:
            cur["rows"].append(r.groupdict())

    team_a = teams[0] if len(teams) > 0 else ""
    team_b = teams[1] if len(teams) > 1 else ""
    score_a = scores[0] if len(scores) > 0 else ""
    score_b = scores[1] if len(scores) > 1 else ""

    if not match_id:                              # no readable id -> stable id from the scoreline
        import hashlib
        key = f"{team_a}{score_a}{team_b}{score_b}{result}".lower()
        match_id = "ss-" + hashlib.md5(key.encode()).hexdigest()[:8]

    batting, bowling = [], []
    for blk in blocks:
        bat_team = teams[blk["team_idx"]] if blk["team_idx"] < len(teams) else ""
        bowl_team = team_b if bat_team == team_a else team_a
        for d in blk["rows"]:
            runs, balls = int(d["runs"]), int(d["balls"])
            batting.append({
                "team": bat_team, "player": d["bat"].strip(),
                "how_out": "not out" if d["no"] else "other", "bowler": "", "fielder": "",
                "runs": runs, "balls": balls, "fours": 0, "sixes": 0,
                "sr": round(runs / balls * 100, 2) if balls else 0})
            bowling.append({
                "team": bowl_team, "player": d["bowl"].strip(),
                "overs": 0.0, "maidens": 0, "runs": int(d["r"]), "wickets": int(d["w"]),
                "econ": 0, "dots": 0, "fours_conceded": 0, "sixes_conceded": 0,
                "wides": 0, "no_balls": 0})

    meta = {"match_id": match_id, "date": "", "format": "", "overs": "", "result": result,
            "toss": "", "team_a": team_a, "team_b": team_b, "score_a": score_a, "score_b": score_b}
    return {"meta": meta, "batting": batting, "bowling": bowling}
