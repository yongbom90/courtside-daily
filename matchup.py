"""상대 팀 성향(시즌 허용 기록) → 선수 항목별 매치업 등급(유리/보통/불리)과 이유.

순위 규칙: 1위 = 상대에게 가장 많이 허용(오버에 유리), 30위 = 가장 적게 허용(불리).
등급: 1~8위 유리, 9~22위 보통, 23~30위 불리. 오늘 결장하는 상대 핵심 빅맨이 있으면 한 단계 유리 쪽으로.
"""

GOOD, BAD = 8, 23
CATS = ("PTS", "REB", "AST", "3PM")


def _split(v):
    try:
        a, b = (v or "0-0").split("-")
        return int(a), int(b)
    except ValueError:
        return 0, 0


def team_lines(s):
    """경기 요약 → {팀: 그 팀 기록}. 박스가 없으면 빈 dict."""
    out = {}
    for t in s.get("boxscore", {}).get("teams", []):
        m = {x.get("name"): x.get("displayValue") for x in t.get("statistics", [])}
        fgm, fga = _split(m.get("fieldGoalsMade-fieldGoalsAttempted"))
        tpm, tpa = _split(m.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
        ftm, fta = _split(m.get("freeThrowsMade-freeThrowsAttempted"))
        if not fga:
            continue
        num = lambda k: int(float(m.get(k) or 0))
        d = dict(FGM=fgm, FGA=fga, TPM=tpm, TPA=tpa, FTM=ftm, FTA=fta, OREB=num("offensiveRebounds"),
                 DREB=num("defensiveRebounds"), REB=num("totalRebounds"), AST=num("assists"), STL=num("steals"),
                 BLK=num("blocks"), TOV=num("totalTurnovers") or num("turnovers"), PITP=num("pointsInPaint"))
        d["PTS"] = 2 * fgm + tpm + ftm
        d["POSS"] = fga - d["OREB"] + d["TOV"] + 0.44 * fta
        out[t["team"]["abbreviation"]] = d
    return out


# 지표: (키, 이름, 큰 값이 상대에게 유리하면 True)
METRICS = [
    ("drtg", "100회당 실점", True), ("pace", "페이스", True),
    ("a3pct", "3점 허용률", True), ("a3pa", "3점 시도 허용", True), ("a2pct", "2점 허용률", True),
    ("afta", "자유투 허용", True), ("apitp", "골밑 실점", True), ("areb", "리바운드 허용", True),
    ("miss", "상대 놓친 슛", True), ("oreb", "상대 공격리바율", False), ("aoreb", "공격리바 허용률", True),
    ("aast", "어시스트 허용", True), ("stl", "상대 스틸", False), ("blk", "상대 블록", False),
]
LABEL = {k: n for k, n, _ in METRICS}


def profiles(games):
    """games: [{팀: 기록}] (끝난 경기, 두 팀) → {팀: {지표: 값, "rk": {지표: 순위}, "G": 경기 수}}"""
    acc = {}
    for g in games:
        if len(g) != 2:
            continue
        a, b = list(g)
        for me, op in ((a, b), (b, a)):
            o, x = g[me], g[op]
            r = acc.setdefault(me, {"G": 0, "own": {}, "opp": {}})
            r["G"] += 1
            for k, v in o.items():
                r["own"][k] = r["own"].get(k, 0) + v
            for k, v in x.items():
                r["opp"][k] = r["opp"].get(k, 0) + v
    P = {}
    for t, r in acc.items():
        G, o, x = r["G"], r["own"], r["opp"]
        P[t] = {"G": G,
                "drtg": 100 * x["PTS"] / x["POSS"], "pace": (o["POSS"] + x["POSS"]) / 2 / G,
                "a3pct": 100 * x["TPM"] / max(x["TPA"], 1), "a3pa": x["TPA"] / G,
                "a2pct": 100 * (x["FGM"] - x["TPM"]) / max(x["FGA"] - x["TPA"], 1),
                "afta": x["FTA"] / G, "apitp": x["PITP"] / G, "areb": x["REB"] / G,
                "miss": (o["FGA"] - o["FGM"]) / G,
                "oreb": 100 * o["OREB"] / max(o["OREB"] + x["DREB"], 1),
                "aoreb": 100 * x["OREB"] / max(x["OREB"] + o["DREB"], 1),
                "aast": x["AST"] / G, "stl": o["STL"] / G, "blk": o["BLK"] / G,
                "ppg": o["PTS"] / G, "oppg": x["PTS"] / G}
    n = len(P)
    for k, _, up in METRICS:
        order = sorted(P, key=lambda t: -P[t][k] if up else P[t][k])
        for i, t in enumerate(order):
            P[t].setdefault("rk", {})[k] = i + 1
    for t in P:
        P[t]["n"] = n
        for k, _, _ in METRICS:
            P[t][k] = round(P[t][k], 1)
    return P


def _g(P, t, k):
    """0~1, 1 = 상대에게 가장 관대."""
    n = P[t]["n"]
    return (n - P[t]["rk"][k]) / max(n - 1, 1)


def player_mix(L):
    """선수 시즌 로그 → 득점 구성·리바 구성·시도 평균."""
    N = len(L)
    s = lambda k: sum(l.get(k, 0) for l in L)
    pts = max(s("PTS"), 1)
    reb = max(s("REB"), 1)
    return {"s3": 3 * s("3PM") / pts, "sft": s("FTM") / pts, "s2": max(0.0, 1 - (3 * s("3PM") + s("FTM")) / pts),
            "so": s("OREB") / reb, "P": s("PTS") / N, "R": s("REB") / N, "A": s("AST") / N}


def score(P, t, cat, mx):
    """상대 팀 t 를 만났을 때 선수 항목 점수(0~1, 높을수록 유리)."""
    g = lambda k: _g(P, t, k)
    if cat == "PTS":
        mixv = mx["s3"] * g("a3pct") + mx["sft"] * g("afta") + mx["s2"] * (g("a2pct") + g("apitp") + g("blk")) / 3
        return 0.35 * g("drtg") + 0.15 * g("pace") + 0.5 * mixv
    # 아래 세 식은 11/1~11/16 되짚어보기에서 적중률 차이가 가장 컸던 조합(허용률·리바 총량보다 '기회 수'가 중요)
    if cat == "3PM":
        return g("a3pa")
    if cat == "REB":
        return (g("miss") + g("oreb")) / 2
    if cat == "AST":
        return g("aast")
    parts = {"PRA": "PRA", "PR": "PR", "PA": "PA", "RA": "RA"}[cat]
    w = {"P": mx["P"], "R": mx["R"], "A": mx["A"]}
    tot = sum(w[c] for c in parts) or 1
    m = {"P": "PTS", "R": "REB", "A": "AST"}
    return sum(w[c] / tot * score(P, t, m[c], mx) for c in parts)


# 항목별 이유에 쓸 지표
WHY = {"PTS": ["drtg", "pace", "a3pct", "a2pct", "apitp", "afta", "blk"], "3PM": ["a3pa", "a3pct"],
       "REB": ["miss", "oreb", "areb"], "AST": ["aast", "pace", "stl"]}
UNIT = {"drtg": "", "pace": "회", "a3pct": "%", "a2pct": "%", "a3pa": "개", "afta": "개", "apitp": "점", "areb": "개",
        "miss": "개", "oreb": "%", "aoreb": "%", "aast": "개", "stl": "개", "blk": "개"}


def keymen(L_by_name, out_names):
    """상대 팀 로그 → 빅맨(리바 8+ 또는 공격리바 2.5+), 림 보호(블록 1.5+), 스틸(1.5+) 선수. 출전 20분+ , 5경기+."""
    res = {"big": [], "rim": [], "stl": []}
    for name, L in L_by_name.items():
        if len(L) < 5 or sum(l["min"] for l in L) / len(L) < 20:
            continue
        a = lambda k: sum(l.get(k, 0) for l in L) / len(L)
        out = name in out_names
        if a("REB") >= 8 or a("OREB") >= 2.5:
            res["big"].append((name, round(a("REB"), 1), round(a("OREB"), 1), out))
        if a("BLK") >= 1.5:
            res["rim"].append((name, round(a("BLK"), 1), out))
        if a("STL") >= 1.5:
            res["stl"].append((name, round(a("STL"), 1), out))
    for v in res.values():
        v.sort(key=lambda x: -x[1])
    return res


def short(n):
    return n.split(" ", 1)[-1] if " " in n else n


def grade(P, opp, cat, mx, km):
    """→ [등급, 순위, 이유들]. 등급 'good' | 'mid' | 'bad'."""
    if opp not in P:
        return None
    me = score(P, opp, cat, mx)
    rank = 1 + sum(score(P, t, cat, mx) > me + 1e-9 for t in P if t != opp)
    n = P[opp]["n"]
    gr = "good" if rank <= GOOD else "bad" if rank >= n - (30 - BAD) else "mid"
    why = []
    base = [cat] if cat in WHY else [c for c, k in (("PTS", "P"), ("REB", "R"), ("AST", "A")) if k in cat]
    # 결장 반영: 상대 리바 빅맨·림 보호 결장 → 한 단계 유리
    if any(c in ("REB",) for c in base):
        gone = [b for b in km["big"] if b[3]]
        if gone:
            why.append("상대 리바 " + ", ".join(f"{short(b[0])}({b[1]}R) 결장" for b in gone[:2]))
            gr = {"bad": "mid", "mid": "good"}.get(gr, gr)
            rank = max(1, rank - 8)
    if "PTS" in base:
        gone = [b for b in km["rim"] if b[2]]
        if gone and not any("결장" in w for w in why):
            why.append("상대 블록 " + ", ".join(f"{short(b[0])}({b[1]}) 결장" for b in gone[:2]))
            gr = {"bad": "mid", "mid": "good"}.get(gr, gr)
            rank = max(1, rank - 8)
    rk = P[opp]["rk"]
    for c in base:
        ms = WHY[c]
        if c == "PTS":
            ms = ["drtg", "pace"] + (["a3pct"] if mx["s3"] >= 0.3 else []) + (["afta"] if mx["sft"] >= 0.2 else []) \
                 + (["a2pct", "apitp", "blk"] if mx["s2"] >= 0.45 else [])
        if c == "REB":
            ms = ["miss", "oreb"]
        cand = []
        for k in ms:
            r = rk[k]
            if gr == "bad" and r >= n - 9 or gr == "good" and r <= 10 or gr == "mid" and (r <= 5 or r >= n - 4):
                cand.append((abs(r - (n + 1) / 2), k, r))
        cand.sort(reverse=True)
        for _, k, r in cand[:2]:
            up = next(u for k_, _, u in METRICS if k_ == k)
            why.append(f"{LABEL[k]} {P[opp][k]}{UNIT[k]}(많은 순 {r if up else n + 1 - r}위)")
        if c == "REB" and gr != "good":
            act = [b for b in km["big"] if not b[3]]
            if act:
                why.append("상대 빅맨 " + ", ".join(f"{short(b[0])}({b[1]}R·공리{b[2]})" for b in act[:3]))
        if c == "PTS" and gr != "good" and mx["s2"] >= 0.45:
            act = [b for b in km["rim"] if not b[2]]
            if act:
                why.append("림 보호 " + ", ".join(f"{short(b[0])}(블록 {b[1]})" for b in act[:1]))
        if c == "3PM" and gr == "bad":
            act = [b for b in km["stl"] if not b[2]]
            if act:
                why.append("외곽 수비 " + ", ".join(f"{short(b[0])}(스틸 {b[1]})" for b in act[:1]))
        if c == "AST" and gr == "bad":
            act = [b for b in km["stl"] if not b[2]]
            if act:
                why.append("스틸 " + ", ".join(f"{short(b[0])}({b[1]})" for b in act[:2]))
    if gr == "mid" and not any("결장" in w for w in why):
        why = why[:2]
    return [gr, rank, why[:4]]


# 오늘 확률 보정: logit(오늘) = logit(시즌 추정) + SLOPE × (15.5 − 순위)/14.5
# 11/1~16 리그 전체 선수-경기·모든 사다리 단계(약 3만 건)로 항목별 로지스틱 회귀. 80% 기준 1위 상대 ≈ +3~4%p, 30위 ≈ −3~5%p.
SLOPE = {"PTS": 0.218, "REB": 0.188, "AST": 0.2, "3PM": 0.062, "PRA": 0.292, "PR": 0.206, "PA": 0.214, "RA": 0.219}


def today(p, cat, rank):
    import math
    p = min(max(p, 0.01), 0.99)
    x = math.log(p / (1 - p)) + SLOPE.get(cat, 0.2) * (15.5 - rank) / 14.5
    return 1 / (1 + math.exp(-x))


def downgrade(rec):
    return {"top": "rec", "rec": "caution"}.get(rec, rec)
