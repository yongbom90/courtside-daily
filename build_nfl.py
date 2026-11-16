"""NFL 자동 데이터(nfl_auto.json): 이번 실행 구간 경기·라인·부상·날씨·팀 스탯 + 선수 prop 라인 대비 시즌 기록 + 지난 경기 결과.
usage: python build_nfl.py YYYYMMDD out.json
       python build_nfl.py YYYYMMDD --count   (오늘 예정 경기 수. 0이면 실행할 필요 없음)
       python build_nfl.py YYYYMMDD --tips    (오늘 남은 킥오프 시각, 자정부터 분)
데이터형 위젯: 픽·방향은 만들지 않는다. 숫자는 전부 ESPN(라인은 DraftKings via ESPN)에서 온 값.
분석 문장(경기 맥락, 팀 카드, 리뷰 원인)은 Claude가 nfl_analysis.json에 따로 쓴다.
"""
import glob, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
B = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
os.makedirs(CACHE, exist_ok=True)


def get(url, cache_key=None):
    p = os.path.join(CACHE, cache_key) if cache_key else None
    if p and os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if p:
        json.dump(d, open(p, "w", encoding="utf-8"))
    return d


def summary(eid, final=True):
    # 끝난 경기만 캐시 (예정 경기는 부상·라인이 계속 바뀜)
    return get(f"{B}/summary?event={eid}", f"nfl_sum_{eid}.json" if final else None)


def et(iso):
    """ESPN UTC 시각 → 미국 동부 시각(naive). zoneinfo 없이 DST 규칙으로 계산(Windows tzdata 문제 회피)."""
    u = datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
    y = u.year
    mar = datetime(y, 3, 8 + (6 - datetime(y, 3, 8).weekday()) % 7, 7)   # 3월 둘째 일요일 2시 EST = 7시 UTC
    nov = datetime(y, 11, 1 + (6 - datetime(y, 11, 1).weekday()) % 7, 6)  # 11월 첫째 일요일 2시 EDT = 6시 UTC
    return u - timedelta(hours=4 if mar <= u < nov else 5)


def windows(date):
    """(이번 슬레이트 날짜들, 결과 리뷰 날짜들). 매일 아침·오후 실행: 슬레이트 = 오늘 경기,
    리뷰 = 오늘 이전 가장 최근 경기일(최대 7일 전까지, 예: 일→목, 월→일, 목→월)."""
    d = datetime.strptime(date, "%Y%m%d")
    f = lambda k: (d + timedelta(days=k)).strftime("%Y%m%d")
    back = []
    for k in range(1, 8):
        if any(e["competitions"][0]["status"]["type"]["state"] == "post"
               for e in get(f"{B}/scoreboard?dates={f(-k)}")["events"]):
            back = [f(-k)]
            break
    return [date], back


def tips(date):
    """오늘 아직 시작 안 한 NFL 킥오프 시각들(ET, 자정부터 분)."""
    out = set()
    for e in get(f"{B}/scoreboard?dates={date}")["events"]:
        if e["competitions"][0]["status"]["type"]["state"] == "pre":
            t = et(e["date"])
            out.add(t.hour * 60 + t.minute)
    return sorted(out)


# prop 종류 → (짧은 코드, 한국어)
PROP = {
    "Total Passing Yards": ("PYD", "패스 야드"), "Total Pass Completions": ("CMP", "패스 성공"),
    "Total Passing Attempts": ("ATT", "패스 시도"), "Total Passing Touchdowns": ("PTD", "패스 TD"),
    "Total Passing Interceptions": ("INT", "인터셉션"), "Total Carries": ("CAR", "캐리"),
    "Total Rushing Yards": ("RYD", "러시 야드"), "Total Receptions": ("REC", "리셉션"),
    "Total Receiving Yards": ("REYD", "리시빙 야드"), "Total Rushing Plus Receiving Yards": ("RRY", "러시+리시빙 야드"),
    "Total Passing Plus Rushing Yards": ("PRY", "패스+러시 야드"),
}
PASS_CATS, REC_CATS = {"PYD", "CMP", "ATT", "PTD", "INT", "PRY"}, {"REC", "REYD"}

# 80~100% 기준선(NBA s80 과 같은 방식): 포지션별 카테고리와 기준선 후보. 판정은 '기준선 이상'(오버 쪽)만.
THR = {"PYD": [150, 175, 200, 225, 250, 275, 300], "CMP": [15, 18, 20, 22, 25], "PTD": [1, 2, 3],
       "RYD": [30, 40, 50, 60, 70, 80, 100], "CAR": [8, 10, 12, 15, 18, 20],
       "REC": [2, 3, 4, 5, 6, 7, 8], "REYD": [25, 30, 40, 50, 60, 70, 80, 100], "TD": [1]}
POS_CATS = {"QB": ["PYD", "PTD"], "RB": ["RYD", "REC", "REYD", "TD"], "WR": ["REC", "REYD", "TD"], "TE": ["REC", "REYD", "TD"]}
# 위젯에 보여줄 라인 종류(사용자 지정): 패스 야드·패스 TD·러시 야드·리시빙 야드·리셉션 (+ 애니타임 TD 는 별도 탭)
FOCUS = {"PYD", "PTD", "RYD", "REYD", "REC"}


# 🪜 선수별 묶어보기: 고정 간격 단계(FanDuel 대체 라인처럼). 추정 = (시즌 보정 + 최근5 보정) / 2
LADDER_NFL = {"PYD": [150, 175, 200, 225, 250, 275, 300], "PTD": [1, 2, 3], "ATT": [25, 30, 35, 40],
              "RYD": [25, 40, 50, 60, 75, 100], "CAR": [10, 12, 15, 18, 20],
              "REYD": [25, 40, 50, 60, 75, 100], "REC": [2, 3, 4, 5, 6, 7, 8], "TGT": [3, 4, 5, 6, 7, 8, 10],
              "RRY": [40, 50, 60, 75, 100, 125], "TD": [1, 2]}
LAD_POS = {"QB": ["PYD", "PTD", "ATT", "RYD"], "RB": ["RYD", "CAR", "RRY", "REC", "REYD", "TGT", "TD"],
           "WR": ["REYD", "REC", "TGT", "TD"], "TE": ["REYD", "REC", "TGT", "TD"]}
QKEYS = ("PYD", "PTD", "ATT", "RYD", "CAR", "REC", "REYD", "TGT", "TD")


def ctx_flags(g, ab, aid, ps, cat, th, a5, inj, qb_out, qb_chg, fav):
    """판정 경고(부상·백업 QB·QB 교체·큰 점수차·돌풍). 80~100% 판정과 선수별 묶어보기가 같이 씀."""
    f = []
    if aid in inj:
        f.append(f"부상 리포트 {inj[aid]['s']}")
    if qb_out.get(ab) and ps != "QB" and cat in ("REC", "REYD", "TGT"):
        f.append(f"백업 QB 가능성(결장: {', '.join(qb_out[ab])})")
    if qb_out.get(ab) and ps == "QB":
        f.append("QB 결장자 있음 → 선발 확인 필요")
    if qb_chg.get(ab) and (ps == "QB" or cat in ("REC", "REYD", "TGT")):
        f.append(f"선발 QB 교체({qb_chg[ab]})")
    if g["abs"] >= 10 and a5 < th * 1.25:
        if ab == fav and cat in ("PYD", "CMP", "PTD", "REC", "REYD"):
            f.append(f"스프레드 {g['abs']} 큰 우세 → 후반 패스 감소 위험")
        elif ab != fav and cat in ("RYD", "CAR"):
            f.append(f"스프레드 {g['abs']} 열세 → 끌려가면 러시 감소 위험")
    wx = g["wx"] or {}
    if cat in ("PYD", "CMP", "PTD", "REC", "REYD") and (wx.get("gust") or 0) >= 15:
        f.append(f"돌풍 {wx['gust']}mph 예보")
    return f


def fair_am(p):
    """확률 → 적정 미국식 배당."""
    p = min(max(p, 0.01), 0.99)
    return -round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def s80(L, cat, ctx):
    """시즌 로그(최신순)에서 80%+ 적중하는 가장 높은 기준선 + 판정(top/rec/caution). 판정 규칙은 NBA 위젯과 같음."""
    vals, N = [r[cat] for r in L], len(L)
    if N < 5:
        return None
    best = None
    for th in THR[cat]:
        hit = sum(v >= th for v in vals)
        if hit / N >= 0.8:
            best = (th, hit)
    if not best:
        return None
    th, hit = best
    last = vals[:5]
    h5 = sum(v >= th for v in last)
    avg, a5 = round(sum(vals) / N, 1), round(sum(last) / len(last), 1)
    rate = round(100 * hit / N)
    flags = []
    if a5 < avg * 0.9:
        flags.append("최근 폼 하락")
    if h5 < 4:
        flags.append(f"최근 5경기 {h5}/5")
    flags += ctx(cat, th, a5)
    if rate >= 90 and h5 == 5 and a5 >= th * 1.25 and not flags:
        rec = "top"
    elif rate >= 80 and h5 >= 4 and not flags:
        rec = "rec"
    else:
        rec = "caution"
    return {"cat": cat, "th": th, "hit": hit, "N": N, "rate": rate, "h5": h5, "avg": avg, "a5": a5,
            "last": [int(v) for v in last], "rec": rec, "flags": flags}


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def box_rows(s):
    """경기 박스 → {팀: {선수id: 기록}}. 패스·러시·리시빙 중 하나라도 기록이 있으면 출전으로 본다."""
    out = {}
    for t in s.get("boxscore", {}).get("players", []):
        ab = t["team"]["abbreviation"]
        rows = out.setdefault(ab, {})
        for g in t.get("statistics", []):
            if g["name"] not in ("passing", "rushing", "receiving"):
                continue
            for a in g.get("athletes", []):
                d = dict(zip(g["keys"], a.get("stats", [])))
                r = rows.setdefault(a["athlete"]["id"], {"n": a["athlete"]["displayName"]})
                if g["name"] == "passing":
                    c, at = (d.get("completions/passingAttempts") or "0/0").split("/")
                    r.update(CMP=num(c), ATT=num(at), PYD=num(d.get("passingYards")), PTD=num(d.get("passingTouchdowns")),
                             INT=num(d.get("interceptions")))
                elif g["name"] == "rushing":
                    r.update(CAR=num(d.get("rushingAttempts")), RYD=num(d.get("rushingYards")), RTD=num(d.get("rushingTouchdowns")))
                else:
                    r.update(REC=num(d.get("receptions")), REYD=num(d.get("receivingYards")), RETD=num(d.get("receivingTouchdowns")),
                             TGT=num(d.get("receivingTargets")))
    for rows in out.values():
        for r in rows.values():
            for k in ("CMP", "ATT", "PYD", "PTD", "INT", "CAR", "RYD", "RTD", "REC", "REYD", "RETD", "TGT"):
                r.setdefault(k, 0.0)
            r["RRY"], r["PRY"], r["TD"] = r["RYD"] + r["REYD"], r["PYD"] + r["RYD"], r["RTD"] + r["RETD"]
    return out


def ats(details, total, score):
    """마감 라인 대비 결과 문자열. details 예: 'KC -3', 'EVEN'."""
    res = []
    try:
        fav, sp = details.rsplit(" ", 1)
        other = next(t for t in score if t != fav)
        margin = score[fav] - score[other] + float(sp)
        res.append(f"{fav} {sp} " + ("커버" if margin > 0 else "푸시" if margin == 0 else "미커버"))
    except (ValueError, StopIteration, AttributeError, KeyError):
        pass
    if total:
        pts = sum(score.values())
        res.append(f"합계 {pts} " + ("오버" if pts > total else "언더" if pts < total else "푸시") + f" ({total})")
    return " · ".join(res)


_PITEMS = {}


def prop_all(eid):
    """DraftKings prop 원본 전체(ESPN core API, 1000개씩 여러 페이지)."""
    if eid not in _PITEMS:
        items, page = [], 1
        try:
            while True:
                d = get(f"{CORE}/events/{eid}/competitions/{eid}/odds/100/propBets?limit=1000&page={page}")
                items += d.get("items", [])
                if page >= (d.get("pageCount") or 1):
                    break
                page += 1
        except Exception as e:  # prop 없는 경기(라인 미오픈)
            print(f"  props {eid}: {e}", file=sys.stderr)
        _PITEMS[eid] = items
    return _PITEMS[eid]


# DraftKings 단계별(마일스톤) 라인 — NFL 은 ESPN 에 배당이 없어서 '그 단계가 열려 있는지'만 씀
MILE_NFL = {"Passing Yards Milestones": "PYD", "Rushing Yards Milestones": "RYD", "Receiving Yards Milestones": "REYD",
            "Receptions Milestones": "REC", "Rushing + Receiving Yards Milestones": "RRY", "Rushing Attempts Milestones": "CAR",
            "Passing Touchdowns Milestones": "PTD"}


def miles_nfl(eid):
    out = {}
    for i in prop_all(eid):
        cat = MILE_NFL.get(i.get("type", {}).get("name", ""))
        aid = i.get("athlete", {}).get("$ref", "").split("/athletes/")[-1].split("?")[0]
        t = (i.get("current") or {}).get("target", {}).get("value")
        if cat and aid and t is not None:
            out.setdefault((aid, cat), set()).add(float(t))
    return out


def props_for(eid):
    items = prop_all(eid)
    if not items:
        return {}, set()
    lines, td = {}, set()
    for i in items:
        name = i.get("type", {}).get("name", "").replace(" (incl. overtime)", "")
        aid = i.get("athlete", {}).get("$ref", "").split("/athletes/")[-1].split("?")[0]
        if not aid:
            continue
        if name == "Anytime Touchdown Scorer":
            td.add(aid)
        elif name in PROP and (i.get("current") or {}).get("target"):
            lines[(aid, PROP[name][0])] = (i["current"]["target"]["value"], (i.get("open") or {}).get("target", {}).get("value"))
    return lines, td


SUFFIX = re.compile(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$")


def abbr(full):
    """'Amon-Ra St. Brown' → 'A.St. Brown' (ESPN 플레이 문장 표기)."""
    f = SUFFIX.sub("", full.strip()).split(" ")
    return f[0][0] + "." + " ".join(f[1:]) if len(f) > 1 else full


def qparse(s):
    """경기 플레이 문장 → {선수id: {PYD,PTD,CMP,RYD,CAR,RTD,REC,REYD,RETD: [1Q,2Q,3Q,4Q,OT]}}.
    야드는 문장의 'for N yards'(벌칙 야드 제외), 이름은 'J.Goff'·'Bi.Robinson'·'A.St. Brown' 같은 줄임 표기."""
    full, tmof = {}, {}
    for t in s.get("boxscore", {}).get("players", []):
        for g in t.get("statistics", []):
            for a in g.get("athletes", []):
                full[a["athlete"]["id"]] = SUFFIX.sub("", a["athlete"]["displayName"].strip())
                tmof[a["athlete"]["id"]] = t["team"]["abbreviation"]
    by_rest = {}
    for aid, n in full.items():
        f = n.split(" ")
        if len(f) > 1:
            by_rest.setdefault(" ".join(f[1:]), []).append((f[0], aid))
    if not by_rest:
        return {}
    NM = r"[A-Z][a-z]{0,2}\.\s?(?:" + "|".join(re.escape(r) for r in sorted(by_rest, key=len, reverse=True)) + r")\b"
    def who(tok, off=None):
        pre, rest = tok.split(".", 1)
        c = by_rest.get(rest.strip(), [])
        if off and len(c) > 1 and any(tmof[a] == off for _, a in c):
            c = [x for x in c if tmof[x[1]] == off]
        m = [aid for f, aid in c if f.startswith(pre)] or (c and len(c) == 1 and [c[0][1]]) or []
        return m[0] if len(m) == 1 else None
    YD = r"for (-?\d+) yards?|for no gain"
    pas = re.compile(r"(" + NM + r") pass (?:(?!incomplete)[^.]|\.(?=[A-Z][a-z]))*?to (" + NM + r").*?(?:" + YD + r")")
    rus = re.compile(r"(" + NM + r") (?!pass|sacked|reported|punts|kicks|extra|spiked)(?:[^.(]|\.(?=[A-Z]))*?(?:" + YD + r")")
    out = {}
    def add(aid, k, q, v):
        if aid:
            out.setdefault(aid, {c: [0] * 5 for c in ("PYD", "PTD", "CMP", "ATT", "RYD", "CAR", "RTD", "REC", "REYD", "RETD", "TGT")})[k][min(q, 5) - 1] += v
    for d in (s.get("drives") or {}).get("previous") or []:
        off = (d.get("team") or {}).get("abbreviation")
        for p in d.get("plays", []):
            t = p.get("text", "").split("TWO-POINT CONVERSION")[0]
            ty = (p.get("type") or {}).get("text", "")
            q = (p.get("period") or {}).get("number") or 0
            if not q or "NULLIFIED" in t.upper() or "No Play" in t or "Sack" in ty:
                continue
            m = (re.search(r"(" + NM + r") pass incomplete(?:[^.]|\.(?=[A-Z][a-z]))*?to (" + NM + r")()", t)
                 or re.search(r"(" + NM + r") pass (?:[^.]|\.(?=[A-Z][a-z]))*?intended for (" + NM + r")()", t)
                 or re.search(r"(" + NM + r") pass incomplete()()", t)) if ("incomplete" in t or "INTERCEPTED" in t) else None
            if m and ("Incompletion" in ty or "Interception" in ty or "Penalty" in ty):
                add(who(m.group(1), off), "ATT", q, 1)
                tg = m.group(2)
                if tg:
                    add(who(tg, off), "TGT", q, 1)
                continue
            if "INTERCEPTED" in t:
                continue
            td = "TOUCHDOWN" in t and "FUMBLE" not in t.upper()
            ok = ty in ("Pass Reception", "Passing Touchdown", "Rush", "Rushing Touchdown") or "Kneel" in ty or "Penalty" in ty or ("Fumble" in ty and "Return" not in ty)
            if not ok:
                continue
            m = pas.search(t)
            if m:
                qb, rc, y = who(m.group(1), off), who(m.group(2), off), int(m.group(3) or 0)
                add(qb, "PYD", q, y); add(qb, "CMP", q, 1); add(rc, "REC", q, 1); add(rc, "REYD", q, y)
                add(qb, "ATT", q, 1); add(rc, "TGT", q, 1)
                if td:
                    add(qb, "PTD", q, 1); add(rc, "RETD", q, 1)
                continue
            if " pass " in t:
                continue
            m = rus.search(t)
            if m:
                r = who(m.group(1), off)
                add(r, "CAR", q, 1); add(r, "RYD", q, int(m.group(2) or 0))
                if td:
                    add(r, "RTD", q, 1)
    return out


def drive_kind(r):
    """드라이브 결과 → TD / FG / PUNT / TO(턴오버) / DOWNS / MISS(필드골 실패) / END(전후반 종료 등)"""
    r = (r or "").upper()
    if r == "TD":
        return "TD"
    if r == "FG":
        return "FG"
    if "INT" in r or "FUMBLE" in r:
        return "TO"
    if "FG" in r or "MISSED" in r:
        return "MISS" if "DOWNS" not in r else "DOWNS"
    if "DOWNS" in r:
        return "DOWNS"
    if "PUNT" in r:
        return "PUNT"
    return "END"


def early(ab, evs, sums):
    """팀의 이번 시즌 경기별 첫 3드라이브(공격)·상대 첫 3드라이브(수비)·첫 TD 쿼터."""
    off, dfn, games = {}, {}, []
    for d, e in sorted(evs, reverse=True):
        s = sums[e]
        dr = (s.get("drives") or {}).get("previous") or []
        if not dr:
            continue
        mine = [drive_kind(x.get("result")) for x in dr if x["team"]["abbreviation"] == ab][:3]
        opp = [drive_kind(x.get("result")) for x in dr if x["team"]["abbreviation"] != ab][:3]
        oab = next((x["team"]["abbreviation"] for x in dr if x["team"]["abbreviation"] != ab), "")
        tdq = next((p["period"]["number"] for p in s.get("scoringPlays", [])
                    if p["team"]["abbreviation"] == ab and (p.get("type") or {}).get("abbreviation") == "TD"), None)
        for k in mine:
            off[k] = off.get(k, 0) + 1
        for k in opp:
            dfn[k] = dfn.get(k, 0) + 1
        games.append({"d": d[:10], "opp": oab, "o": mine, "x": opp, "tdq": tdq})
    if not games:
        return None
    no, nd, G = sum(off.values()), sum(dfn.values()), len(games)
    q = {str(k): sum(g["tdq"] == k for g in games) for k in (1, 2, 3, 4)}
    q["OT"] = sum((g["tdq"] or 0) > 4 for g in games)
    q["none"] = sum(g["tdq"] is None for g in games)
    return {"G": G,
            "off": off, "offN": no, "offScore": round(100 * (off.get("TD", 0) + off.get("FG", 0)) / no) if no else 0,
            "offTD": round(100 * off.get("TD", 0) / no) if no else 0,
            "offGame": sum(any(k in ("TD", "FG") for k in g["o"]) for g in games),
            "def": dfn, "defN": nd, "defStop": round(100 * (nd - dfn.get("TD", 0) - dfn.get("FG", 0)) / nd) if nd else 0,
            "defTDallow": round(100 * dfn.get("TD", 0) / nd) if nd else 0,
            "defGame": sum(not any(k in ("TD", "FG") for k in g["x"]) for g in games),
            "tdq": q, "q1": round(100 * q["1"] / G), "h1": round(100 * (q["1"] + q["2"]) / G),
            "games": games[:6]}

NM = r"([A-Z][A-Za-z'.\-]*\.[A-Za-z'\-]+(?: (?:Jr|Sr|II|III)\.?)?)"


def int_play(p, off, dfn, nxt):
    """인터셉션 플레이 1개 → 상세. off=던진 팀, dfn=잡은 팀, nxt=다음 드라이브(잡은 팀 공격)."""
    t = p.get("text", "")
    g = lambda rx: (re.search(rx, t) or [None, None])[1]
    after = t.split("INTERCEPTED", 1)[-1]
    td = "TOUCHDOWN" in after.upper()
    ret = re.search(r"for (-?\d+) yards?", after)
    if td:
        nx = "PICK6"
    elif nxt and nxt["team"]["abbreviation"] == dfn:
        nx = drive_kind(nxt.get("result"))
    else:
        nx = "END"
    return {"q": p.get("period", {}).get("number"), "clk": (p.get("clock") or {}).get("displayValue", ""),
            "qb": g(NM + r" pass"), "tgt": g(r"intended for " + NM), "by": g(r"INTERCEPTED by " + NM),
            "depth": g(r"pass (short|deep)"), "dir": g(r"pass (?:short |deep )?(left|middle|right)"),
            "ret": int(ret[1]) if ret else (0 if "no gain" in after else None), "nx": nx, "txt": t[:260]}


def game_ints(s):
    """한 경기 → {던진 팀: [인터셉션 상세]}."""
    dr = (s.get("drives") or {}).get("previous") or []
    tms = [t["team"]["abbreviation"] for t in s.get("boxscore", {}).get("teams", [])]
    out = {t: [] for t in tms}
    for i, x in enumerate(dr):
        for p in x.get("plays", []):
            txt = p.get("text", "")
            if "INTERCEPTED" not in txt or "NULLIFIED" in txt.upper() or "NO PLAY" in txt.upper():
                continue
            off = x["team"]["abbreviation"]
            dfn = next((t for t in tms if t != off), "")
            out.setdefault(off, []).append(int_play(p, off, dfn, dr[i + 1] if i + 1 < len(dr) else None))
    return out


def ints(ab, evs, sums):
    """팀의 이번 시즌 인터셉션: 던진 것(공격)·잡은 것(수비) 경기별 상세 + 집계."""
    games, qbs, dfs = [], {}, {}
    for d, e in sorted(evs, reverse=True):
        s = sums[e]
        dr = (s.get("drives") or {}).get("previous") or []
        opp = next((t["team"]["abbreviation"] for t in s.get("boxscore", {}).get("teams", []) if t["team"]["abbreviation"] != ab), "")
        gi = game_ints(s)
        thr, made = gi.get(ab, []), [r for t, L in gi.items() if t != ab for r in L]
        for t in s.get("boxscore", {}).get("players", []):
            if t["team"]["abbreviation"] != ab:
                continue
            for g in t.get("statistics", []):
                for a in g.get("athletes", []):
                    st = dict(zip(g["keys"], a.get("stats", [])))
                    nm = a["athlete"]["displayName"]
                    if g["name"] == "passing":
                        q = qbs.setdefault(nm, {"n": nm, "G": 0, "INT": 0, "ATT": 0, "g1": 0})
                        att = num((st.get("completions/passingAttempts") or "0/0").split("/")[1])
                        if att:
                            k = int(num(st.get("interceptions")))
                            q.update(G=q["G"] + 1, INT=q["INT"] + k, ATT=q["ATT"] + att, g1=q["g1"] + (k > 0))
                    elif g["name"] == "interceptions":
                        f = dfs.setdefault(nm, {"n": nm, "INT": 0, "YDS": 0, "TD": 0})
                        f.update(INT=f["INT"] + int(num(st.get("interceptions"))), YDS=f["YDS"] + int(num(st.get("interceptionYards"))),
                                 TD=f["TD"] + int(num(st.get("interceptionTouchdowns"))))
        if dr:
            games.append({"d": d[:10], "opp": opp, "thr": thr, "made": made})
    if not games:
        return None
    G = len(games)
    T = [r for g in games for r in g["thr"]]
    M = [r for g in games for r in g["made"]]
    cnt = lambda L, k, vals: {v: sum(r[k] == v for r in L) for v in vals}
    after = lambda L: {k: sum(r["nx"] == k for r in L) for k in ("PICK6", "TD", "FG", "PUNT", "TO", "DOWNS", "MISS", "END")}
    tg = {}
    for r in T:
        if r["tgt"]:
            tg[r["tgt"]] = tg.get(r["tgt"], 0) + 1
    for q in qbs.values():
        q["rate"] = round(100 * q["INT"] / q["ATT"], 1) if q["ATT"] else 0
    return {"G": G,
            "thr": len(T), "thrPG": round(len(T) / G, 2), "thrGames": sum(bool(g["thr"]) for g in games),
            "made": len(M), "madePG": round(len(M) / G, 2), "madeGames": sum(bool(g["made"]) for g in games),
            "thrQ": cnt(T, "q", (1, 2, 3, 4)), "madeQ": cnt(M, "q", (1, 2, 3, 4)),
            "thrOT": sum((r["q"] or 0) > 4 for r in T), "madeOT": sum((r["q"] or 0) > 4 for r in M),
            "thrDepth": cnt(T, "depth", ("short", "deep")), "thrDir": cnt(T, "dir", ("left", "middle", "right")),
            "madeDepth": cnt(M, "depth", ("short", "deep")), "madeDir": cnt(M, "dir", ("left", "middle", "right")),
            "thrAfter": after(T), "madeAfter": after(M),
            "tgts": sorted(tg.items(), key=lambda x: -x[1])[:5],
            "qbs": sorted(qbs.values(), key=lambda q: -q["ATT"]),
            "dfs": sorted(dfs.values(), key=lambda f: (-f["INT"], -f["YDS"])),
            "last": [[len(g["thr"]), len(g["made"])] for g in games[:5]],
            "games": games}


def int_ranks(season):
    """리그 32팀 인터셉션 순위(경기당): 던진 INT 적은 순, 잡은 INT 많은 순. 실패하면 빈 dict."""
    try:
        teams = [t["team"] for t in get(f"{B}/teams")["sports"][0]["leagues"][0]["teams"]]
        def one(t):
            d = get(f"{CORE}/seasons/{season}/types/2/teams/{t['id']}/statistics")
            st = {(c["name"], x["name"]): x.get("value") or 0 for c in d["splits"]["categories"] for x in c["stats"]}
            g = st.get(("general", "gamesPlayed")) or 1
            return t["abbreviation"], st.get(("passing", "interceptions"), 0) / g, st.get(("defensiveInterceptions", "interceptions"), 0) / g, \
                st.get(("miscellaneous", "turnOverDifferential"), 0)
        with ThreadPoolExecutor(8) as ex:
            rows = list(ex.map(one, teams))
    except Exception as e:
        print(f"  int_ranks: {e}", file=sys.stderr)
        return {}
    rk = lambda key, rev: {r[0]: 1 + sum((x[key] > r[key]) if rev else (x[key] < r[key]) for x in rows) for r in rows}
    a, b, c = rk(1, False), rk(2, True), rk(3, True)
    return {r[0]: {"thrRk": a[r[0]], "madeRk": b[r[0]], "toDiff": int(r[3]), "toRk": c[r[0]]} for r in rows}


def qb_int(aid, line, opn):
    """오늘 INT 라인이 열린 QB의 이번 시즌 경기별 INT(트레이드 전 팀 포함, ESPN 개인 경기 로그)."""
    try:
        g = get(f"https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{aid}/gamelog")
        ath = get(f"https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{aid}")["athlete"]
        nm, cur = ath["displayName"], (ath.get("team") or {}).get("abbreviation", "")
    except Exception as e:
        print(f"  qb_int {aid}: {e}", file=sys.stderr)
        return None
    names = g.get("names", [])
    st = next((x for x in g.get("seasonTypes", []) if "Regular" in x.get("displayName", "")), None)
    rows = []
    for c in (st or {}).get("categories", []):
        for e in c.get("events", []):
            d = dict(zip(names, e["stats"]))
            ev = g["events"].get(e["eventId"], {})
            if num(d.get("passingAttempts")) > 0:
                rows.append({"d": ev.get("gameDate", "")[:10], "opp": (ev.get("opponent") or {}).get("abbreviation", ""),
                             "tm": (ev.get("team") or {}).get("abbreviation", ""), "INT": int(num(d.get("interceptions"))),
                             "ATT": int(num(d.get("passingAttempts")))})
    rows.sort(key=lambda r: r["d"], reverse=True)
    if not rows:
        return {"id": aid, "n": nm, "tm": cur, "line": line, "open": opn, "G": 0}
    G, I, A = len(rows), sum(r["INT"] for r in rows), sum(r["ATT"] for r in rows)
    hit = sum(r["INT"] > line for r in rows)
    return {"id": aid, "n": nm, "tm": cur, "line": line, "open": opn, "G": G, "INT": I, "ATT": A, "rate": round(100 * I / A, 1),
            "hit": hit, "hitPct": round(100 * hit / G), "l5": rows[:5], "teams": sorted({r["tm"] for r in rows})}


def main(date, out):
    slate_days, back_days = windows(date)
    games, teams = [], {}
    for day in slate_days:
        for ev in get(f"{B}/scoreboard?dates={day}")["events"]:
            c = ev["competitions"][0]
            if c["status"]["type"]["state"] != "pre" or any(g["id"] == ev["id"] for g in games):
                continue
            s = summary(ev["id"], final=False)
            home = next(t for t in c["competitors"] if t["homeAway"] == "home")
            away = next(t for t in c["competitors"] if t["homeAway"] == "away")
            A, H = away["team"]["abbreviation"], home["team"]["abbreviation"]
            pc = (s.get("pickcenter") or [{}])[0]
            pr = s.get("predictor") or {}
            gi = s.get("gameInfo", {})
            w = gi.get("weather") or {}
            k = et(ev["date"])
            g = {"id": ev["id"], "m": f"{A} @ {H}", "away": A, "home": H, "kick": k.strftime("%Y-%m-%d %H:%M"),
                 "time": k.strftime("%a %-I:%M%p") if os.name != "nt" else k.strftime("%a %I:%M%p"),
                 "rec": {t["team"]["abbreviation"]: (t.get("records") or [{}])[0].get("summary", "") for t in c["competitors"]},
                 "sp": pc.get("details"), "tot": pc.get("overUnder"), "abs": abs(pc.get("spread") or 0),
                 "ml": {A: pc.get("awayTeamOdds", {}).get("moneyLine"), H: pc.get("homeTeamOdds", {}).get("moneyLine")},
                 "bpi": {H: pr.get("homeTeam", {}).get("gameProjection"), A: pr.get("awayTeam", {}).get("gameProjection")},
                 "venue": gi.get("venue", {}).get("fullName", ""), "indoor": bool(c.get("venue", {}).get("indoor")),
                 "grass": gi.get("venue", {}).get("grass"),
                 "wx": None if c.get("venue", {}).get("indoor") else {"temp": w.get("temperature"), "gust": w.get("gust"), "prec": w.get("precipitation")},
                 "inj": {A: [], H: []}, "l5": {}, "stats": {}, "tid": {A: away["team"]["id"], H: home["team"]["id"]}}
            for inj in s.get("injuries", []):
                g["inj"][inj["team"]["abbreviation"]] = [
                    {"n": i["athlete"]["displayName"], "id": i["athlete"].get("id"), "s": i["status"],
                     "pos": i["athlete"].get("position", {}).get("abbreviation", ""), "d": (i.get("details") or {}).get("type", "")}
                    for i in inj.get("injuries", [])]
            for l5 in s.get("lastFiveGames", []):
                g["l5"][l5["team"]["abbreviation"]] = [
                    {"r": e["gameResult"], "sc": e["score"], "at": e["atVs"], "opp": e["opponent"]["abbreviation"], "d": e["gameDate"][:10]}
                    for e in l5["events"]]
            for t in s.get("boxscore", {}).get("teams", []):
                g["stats"][t["team"]["abbreviation"]] = {x.get("name"): num(x.get("displayValue")) for x in t.get("statistics", [])}
            games.append(g)
            for ab, t in ((A, away), (H, home)):
                teams[ab] = (t["team"]["id"], g)
    games.sort(key=lambda g: g["kick"])

    # 팀별 이번 시즌 완료 경기 → 선수 경기 로그(최신순)
    ev_by_team = {}
    for ab, (tid, _) in teams.items():
        sch = get(f"{B}/teams/{tid}/schedule")
        ev_by_team[ab] = [(e["date"], e["id"]) for e in sch["events"] if e["competitions"][0]["status"]["type"]["completed"]]
    all_ev = sorted({e for v in ev_by_team.values() for _, e in v})
    with ThreadPoolExecutor(8) as ex:
        sums = dict(zip(all_ev, ex.map(summary, all_ev)))
        prop_res = dict(zip([g["id"] for g in games], ex.map(props_for, [g["id"] for g in games])))
    early_by = {ab: early(ab, evs, sums) for ab, evs in ev_by_team.items()}
    int_by = {ab: ints(ab, evs, sums) for ab, evs in ev_by_team.items()}
    rk = int_ranks(date[:4] if date[4:6] > "02" else str(int(date[:4]) - 1))
    for ab, v in int_by.items():
        if v:
            v.update(rk.get(ab, {}))
    int_qb = {}
    for g in games:
        L = [qb_int(aid, ln[0], ln[1]) for (aid, cat), ln in prop_res[g["id"]][0].items() if cat == "INT"]
        int_qb[g["m"]] = [q for q in L if q]
    logs, who = {}, {}
    for ab, evs in ev_by_team.items():
        for _, e in sorted(evs, reverse=True):
            for aid, row in box_rows(sums[e]).get(ab, {}).items():
                row["e"] = e
                logs.setdefault((ab, aid), []).append(row)
                who.setdefault(aid, (ab, row["n"]))

    # 로스터: 포지션(+로그가 없는 선수 이름)
    pos = {}
    with ThreadPoolExecutor(8) as ex:
        rosters = dict(zip(teams, ex.map(lambda ab: get(f"{B}/teams/{teams[ab][0]}/roster"), teams)))
    for ab, r in rosters.items():
        for grp in r.get("athletes", []):
            for a in grp.get("items", []):
                pos[a["id"]] = (ab, a["displayName"], a.get("position", {}).get("abbreviation", ""))

    # 트레이드·이적 선수: 오늘 팀 경기만으로는 기록이 모자라니 이전 팀 경기도 합친다(ESPN 개인 경기 로그)
    gdate = lambda e: ((sums[e].get("header") or {}).get("competitions") or [{}])[0].get("date", "")
    cand = set()
    for g in games:
        lines0, td0 = prop_res.get(g["id"], ({}, set()))
        for aid in {a for (a, _) in lines0} | td0 | {a for (a, _) in miles_nfl(g["id"])}:
            ab = pos.get(aid, (None,))[0]
            if ab in (g["away"], g["home"]) and len(logs.get((ab, aid), [])) < len(ev_by_team.get(ab, [])) - 1:
                cand.add((ab, aid))
    def glog(aid):
        try:
            gl = get(f"https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{aid}/gamelog")
        except Exception:
            return []
        st = next((x for x in gl.get("seasonTypes", []) if "Regular" in x.get("displayName", "")), None)
        return [e["eventId"] for c in (st or {}).get("categories", []) for e in c.get("events", [])]
    with ThreadPoolExecutor(8) as ex:
        gl_by = dict(zip(cand, ex.map(lambda k: glog(k[1]), cand)))
    need = sorted({e for k, evs in gl_by.items() for e in evs if e not in sums})
    with ThreadPoolExecutor(8) as ex:
        sums.update(dict(zip(need, ex.map(summary, need))))
    traded = {}
    for (ab, aid), evs in gl_by.items():
        have = {r["e"] for r in logs.get((ab, aid), [])}
        add = []
        for e in set(evs) - have:
            if e not in sums:
                continue
            for tm, rows in box_rows(sums[e]).items():
                if aid in rows and tm != ab:
                    add.append({**rows[aid], "e": e, "tm": tm})
        if add:
            L = logs.get((ab, aid), []) + add
            L.sort(key=lambda r: gdate(r["e"]), reverse=True)
            logs[(ab, aid)] = L
            traded[aid] = sorted({r["tm"] for r in add})
    if traded:
        print(f"  이적 선수 이전 팀 기록 합침: {len(traded)}명", file=sys.stderr)

    # 이번 슬레이트 평균(상대 허용 수치 비교용)
    allow = {k: [g["stats"][t].get(k) for g in games for t in g["stats"] if g["stats"][t].get(k)]
             for k in ("passingYardsPerGameAllowed", "rushingYardsPerGameAllowed")}
    avg_allow = {k: round(sum(v) / len(v), 1) if v else None for k, v in allow.items()}

    # 팀별 직전 경기 선발 QB(패스 시도 최다)
    last_qb = {}
    for ab, evs in ev_by_team.items():
        if evs:
            rows = box_rows(sums[max(evs)[1]]).get(ab, {})
            if rows:
                aid, r = max(rows.items(), key=lambda kv: kv[1]["ATT"])
                if r["ATT"] > 0:
                    last_qb[ab] = (aid, r["n"])

    props, tds, stat80 = [], [], []
    gctx = {}
    for g in games:
        lines, td_ids = prop_res.get(g["id"], ({}, set()))
        inj = {i["id"]: i for v in g["inj"].values() for i in v}
        qb_out = {t: [i["n"] for i in v if i["pos"] == "QB" and i["s"] in ("Out", "Doubtful")] for t, v in g["inj"].items()}
        qb_chg = {}
        # 이번 경기 QB = 패스 야드 라인이 열린 QB. 직전 경기 선발과 다르면 QB 교체로 본다(트레이드·벤치 결정 포함)
        for ab in (g["away"], g["home"]):
            now = next((pos[a][1] for (a, c) in lines if c == "PYD" and pos.get(a, ("",))[0] == ab), None)
            prev = last_qb.get(ab)
            if now and prev and prev[1] != now:
                qb_chg[ab] = f"{prev[1]} → {now}"

        # 80~100% 기준선: 이 경기에 DraftKings prop 이 하나라도 열린 선수만(실제 출전 예상 선수)
        fav = (g["sp"] or "").split(" ")[0] if g["sp"] and g["sp"] != "EVEN" else None
        gctx[g["id"]] = (inj, qb_out, qb_chg, fav)
        active = {aid for (aid, _) in lines} | td_ids
        for aid in active:
            ab, name, ps = pos.get(aid, (None, None, ""))
            if ab not in (g["away"], g["home"]) or ps not in POS_CATS:
                continue
            if aid in inj and inj[aid]["s"] in ("Out", "Doubtful"):
                continue
            L = logs.get((ab, aid), [])

            def ctx(cat, th, a5, ab=ab, aid=aid, ps=ps):
                return ctx_flags(g, ab, aid, ps, cat, th, a5, inj, qb_out, qb_chg, fav)
            for cat in POS_CATS[ps]:
                r = s80(L, cat, ctx)
                if r:
                    dk = lines.get((aid, cat))
                    stat80.append({"id": aid, "n": name, "t": ab, "pos": ps, "g": g["m"], "gid": g["id"],
                                   "dk": dk[0] if dk else None, **r})
        for (aid, cat), (line, opn) in lines.items():
            if cat not in FOCUS:
                continue
            ab, name, ps = pos.get(aid, (None, None, ""))
            if ab not in (g["away"], g["home"]):
                continue
            if aid in inj and inj[aid]["s"] in ("Out", "Doubtful"):
                continue
            L = logs.get((ab, aid), [])
            opp = g["home"] if ab == g["away"] else g["away"]
            ok = "rushingYardsPerGameAllowed" if cat in ("CAR", "RYD") else "passingYardsPerGameAllowed"
            row = {"id": aid, "n": name, "t": ab, "pos": ps, "g": g["m"], "gid": g["id"], "cat": cat, "line": line, "open": opn,
                   "opp": opp, "oppAllow": g["stats"].get(opp, {}).get(ok), "slateAllow": avg_allow.get(ok), "N": len(L)}
            vals = [r[cat] for r in L]
            flags = []
            if vals:
                hit = sum(v > line for v in vals)
                last = vals[:5]
                row.update(hit=hit, rate=round(100 * hit / len(vals)), last=last, h5=sum(v > line for v in last),
                           avg=round(sum(vals) / len(vals), 1), a5=round(sum(last) / len(last), 1), mx=max(vals), mn=min(vals))
                if len(vals) >= 5 and row["a5"] < row["avg"] * 0.85:
                    flags.append("최근 5경기 평균이 시즌보다 15%↓")
                elif len(vals) >= 5 and row["a5"] > row["avg"] * 1.15:
                    flags.append("최근 5경기 평균이 시즌보다 15%↑")
            if len(vals) < 4:
                flags.append(f"표본 적음({len(vals)}경기)")
            if opn is not None and opn != line:
                flags.append(f"라인 이동 {opn} → {line}")
            if aid in inj:
                flags.append(f"부상 리포트 {inj[aid]['s']}")
            if cat in PASS_CATS | REC_CATS | {"RRY"} and qb_out.get(ab) and ps != "QB":
                flags.append(f"팀 QB 결장: {', '.join(qb_out[ab])}")
            if qb_chg.get(ab) and (ps == "QB" or cat in REC_CATS | {"RRY"}):
                flags.append(f"선발 QB 교체({qb_chg[ab]})")
            wx = g["wx"] or {}
            if cat in PASS_CATS | REC_CATS and (wx.get("gust") or 0) >= 15:
                flags.append(f"돌풍 {wx['gust']}mph 예보")
            if wx.get("temp") is not None and wx["temp"] <= 32:
                flags.append(f"기온 {wx['temp']}°F")
            row["flags"] = flags
            props.append(row)
        for aid in td_ids:
            ab, name, ps = pos.get(aid, (None, None, ""))
            if ab not in (g["away"], g["home"]) or ps in ("QB",) or (aid in inj and inj[aid]["s"] in ("Out", "Doubtful")):
                continue
            L = logs.get((ab, aid), [])
            if not L:
                continue
            t = [int(r["TD"]) for r in L]
            tds.append({"id": aid, "n": name, "t": ab, "pos": ps, "g": g["m"], "N": len(t), "hit": sum(x > 0 for x in t),
                        "rate": round(100 * sum(x > 0 for x in t) / len(t)), "tot": sum(t), "last": t[:5],
                        "q": inj[aid]["s"] if aid in inj else ""})

    # 역할(사용량) 변화: 최근 3경기 타깃·캐리 vs 시즌
    usage = []
    prop_ids = {(p["t"], p["id"]) for p in props}
    for (ab, aid), L in logs.items():
        if (ab, aid) not in prop_ids or len(L) < 4:
            continue
        ps = pos.get(aid, ("", "", ""))[2]
        if ps not in ("RB", "WR", "TE"):
            continue
        tg, cr = [r["TGT"] for r in L], [r["CAR"] for r in L]
        u = {"n": L[0]["n"], "t": ab, "pos": ps, "g": teams[ab][1]["m"], "N": len(L),
             "tgt": round(sum(tg) / len(tg), 1), "tgt3": round(sum(tg[:3]) / 3, 1),
             "car": round(sum(cr) / len(cr), 1), "car3": round(sum(cr[:3]) / 3, 1),
             "lastT": tg[:5], "lastC": cr[:5]}
        u["d"] = round((u["tgt3"] - u["tgt"]) + (u["car3"] - u["car"]), 1)
        usage.append(u)
    usage.sort(key=lambda u: -abs(u["d"]))

    # 지난 경기 결과 + 지난 실행의 prop 라인 vs 실제 기록
    results, final_box = [], {}
    for day in back_days:
        for ev in get(f"{B}/scoreboard?dates={day}")["events"]:
            c = ev["competitions"][0]
            if not c["status"]["type"]["completed"]:
                continue
            s = summary(ev["id"])
            pc = (s.get("pickcenter") or [{}])[0]
            score = {t["team"]["abbreviation"]: int(t["score"]) for t in c["competitors"]}
            results.append({"id": ev["id"], "m": ev["shortName"], "close": f"{pc.get('details', '')} / {pc.get('overUnder', '')}",
                            "res": ats(pc.get("details"), pc.get("overUnder"), score), "score": score,
                            "q": {t["team"]["abbreviation"]: [int(q["value"]) for q in t.get("linescores", [])] for t in c["competitors"]},
                            "headline": (s.get("article") or {}).get("headline", ""), "ints": game_ints(s)})
            final_box[ev["id"]] = {aid: r for rows in box_rows(s).values() for aid, r in rows.items()}
    prop_review = []
    prev_files = sorted(f for f in glob.glob(os.path.join(HERE, "archive", "nfl_*_props.json")) if os.path.basename(f)[4:12] < date)
    if prev_files:
        for p in json.load(open(prev_files[-1], encoding="utf-8")):
            box = final_box.get(p["gid"])
            if box is None:
                continue
            act = box.get(p["id"], {}).get(p["cat"], 0.0) if p["id"] in box else None
            if act is None:
                prop_review.append({**{k: p[k] for k in ("n", "t", "cat", "line", "g")}, "act": None, "r": "-", "rate": p.get("rate")})
            else:
                prop_review.append({**{k: p[k] for k in ("n", "t", "cat", "line", "g")}, "act": act,
                                    "r": "O" if act > p["line"] else "U", "rate": p.get("rate")})

    # 직전 위젯 픽(⭐·✅) 채점: 기준선 이상이면 적중
    grades, grade_from = [], None
    pick_files = sorted(f for f in glob.glob(os.path.join(HERE, "archive", "nfl_*_picks.json")) if os.path.basename(f)[4:12] < date)
    if pick_files:
        grade_from = os.path.basename(pick_files[-1])[4:12]
        for p in json.load(open(pick_files[-1], encoding="utf-8")):
            box = final_box.get(p["gid"])
            if box is None:
                continue  # 아직 안 끝난 경기
            act = box[p["id"]].get(p["cat"]) if p["id"] in box else None
            grades.append({**{k: p[k] for k in ("n", "t", "g", "cat", "th", "rec")}, "act": act,
                           "r": "-" if act is None else ("O" if act >= p["th"] else "X")})

    for g in games:
        g.pop("tid", None)
    # 🪜 선수별 묶어보기 + 쿼터별 평균(플레이 문장 집계, 연장은 OT 칸)
    miles = {}
    for g in games:
        for k, v in miles_nfl(g["id"]).items():
            miles[k] = v
    qp = {}
    ladders, qtr = [], {}
    for g in games:
        lines, td_ids = prop_res.get(g["id"], ({}, set()))
        inj = {i["id"]: i for v in g["inj"].values() for i in v}
        act = {a for (a, _) in lines} | td_ids | {a for (a, _) in miles}
        for aid in act:
            ab, name, ps = pos.get(aid, (None, None, ""))
            if ab not in (g["away"], g["home"]) or ps not in LAD_POS or (aid in inj and inj[aid]["s"] in ("Out", "Doubtful")):
                continue
            L = logs.get((ab, aid), [])
            if len(L) < 3:
                continue
            fl = ([f"부상 리포트 {inj[aid]['s']}"] if aid in inj else []) + ([f"표본 적음({len(L)}경기)"] if len(L) < 5 else [])
            if aid in traded:
                fl.append(f"이전 팀({'/'.join(traded[aid])}) 경기 {sum(1 for r in L if r.get('tm'))}개 포함")
            cats = [c for c in LAD_POS[ps] if c != "RYD" or ps != "QB" or (aid, "RYD") in lines or sum(r["RYD"] for r in L) / len(L) >= 15]
            for cat in cats:
                vals = [r[cat] for r in L]
                if not any(vals):
                    continue
                last = vals[:5]
                N, rows, zero = len(vals), [], False
                for th in LADDER_NFL[cat]:
                    hit, h5 = sum(v >= th for v in vals), sum(v >= th for v in last)
                    ps_, p5 = (hit + 1) / (N + 2), (h5 + 1) / (len(last) + 2)
                    p = (ps_ + p5) / 2
                    if p > 0.97 or (hit == 0 and zero):
                        continue
                    zero = zero or hit == 0
                    rec, rf = None, []
                    if N >= 5 and hit / N >= 0.8:
                        a5v = sum(last) / len(last)
                        rf = (["최근 폼 하락"] if a5v < (sum(vals) / N) * 0.9 else []) + ([f"최근 5경기 {h5}/5"] if h5 < 4 else [])
                        rf += ctx_flags(g, ab, aid, ps, cat, th, a5v, *gctx.get(g["id"], ({}, {}, {}, None)))
                        rec = "top" if hit / N >= 0.9 and h5 == 5 and a5v >= th * 1.25 and not rf else "rec" if h5 >= 4 and not rf else "caution"
                    rows.append([th, round(100 * p), hit, h5, th in miles.get((aid, cat), set()), fair_am(p), rec, rf])
                if not rows:
                    continue
                dk = lines.get((aid, cat))
                if cat == "TD" and aid in td_ids:
                    dk = (0.5, None)
                ladders.append({"id": aid, "n": name, "t": ab, "pos": ps, "g": g["m"], "cat": cat, "N": N, "last": last,
                                "avg": round(sum(vals) / N, 1), "a5": round(sum(last) / len(last), 1), "mx": max(vals), "mn": min(vals),
                                "dk": dk[0] if dk else None, "dkHit": sum(v > dk[0] for v in vals) if dk else None,
                                "dkH5": sum(v > dk[0] for v in last) if dk else None,
                                "dkMiles": sorted(miles.get((aid, cat), set())), "fl": fl, "r": rows})
            # 쿼터별: 이 선수가 뛴 경기마다 플레이 집계
            Q = {k: [0.0] * 5 for k in QKEYS}
            otG, q1z, l5 = 0, 0, []
            for r in L:
                if r["e"] not in qp:
                    qp[r["e"]] = qparse(sums[r["e"]])
                x = qp[r["e"]].get(aid)
                if not x:
                    continue
                x = {**x, "TD": [a + b for a, b in zip(x["RTD"], x["RETD"])]}
                main_k = "PYD" if ps == "QB" else "RYD" if ps == "RB" else "REYD"
                otG += any(x[k][4] for k in QKEYS)
                q1z += x[main_k][0] <= 0
                if len(l5) < 5:
                    l5.append(x[main_k])
                for k in QKEYS:
                    for i in range(5):
                        Q[k][i] += x[k][i]
            G = len(L)
            qtr[aid] = {"G": G, "otG": otG, "q1z": q1z, "main": "PYD" if ps == "QB" else "RYD" if ps == "RB" else "REYD", "l5": l5,
                        **{k: [round(v / G, 1) for v in Q[k]] for k in QKEYS}}

    A = {"date": date, "slate": slate_days, "back": back_days, "games": games, "props": props, "td": tds, "usage": usage[:30],
         "stat80": stat80, "early": early_by, "ints": int_by, "intQB": int_qb, "grades": grades, "gradeFrom": grade_from,
         "results": results, "propReview": prop_review, "propReviewFrom": os.path.basename(prev_files[-1])[4:12] if prev_files else None,
         "cats": {**{v[0]: v[1] for v in PROP.values()}, "TD": "애니타임 TD"}, "avgAllow": avg_allow,
         "built": datetime.now().strftime("%Y-%m-%d %H:%M"), "phase": os.environ.get("CS_PHASE", ""), "ladders": ladders, "qtr": qtr}
    json.dump(A, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    rc = Counter(r["rec"] for r in stat80)
    print(f"games={len(games)} props={len(props)} td={len(tds)} stat80={len(stat80)} (top={rc['top']} rec={rc['rec']} "
          f"caution={rc['caution']}) usage={len(usage)} results={len(results)} grades={len(grades)} "
          f"propReview={len(prop_review)} events={len(all_ev)}")


def count(date):
    return len([e for e in get(f"{B}/scoreboard?dates={date}")["events"]
                if e["competitions"][0]["status"]["type"]["state"] == "pre"])


def snapshot(date, auto):
    """게시한 라인을 archive/nfl_D_props.json 에 남김 → 다음 실행의 '라인 vs 실제 기록' 리뷰에 쓰임."""
    A = json.load(open(auto, encoding="utf-8"))
    P = A["props"]
    picks = [s for s in A.get("stat80", []) if s["rec"] in ("top", "rec")]
    os.makedirs(os.path.join(HERE, "archive"), exist_ok=True)
    out = os.path.join(HERE, "archive", f"nfl_{date}_props.json")
    json.dump([{k: p.get(k) for k in ("id", "n", "t", "g", "gid", "cat", "line", "rate")} for p in P],
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    out2 = os.path.join(HERE, "archive", f"nfl_{date}_picks.json")
    json.dump([{k: p.get(k) for k in ("id", "n", "t", "g", "gid", "cat", "th", "rec")} for p in picks],
              open(out2, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"snapshot {len(P)} props → {os.path.relpath(out, HERE)}; {len(picks)} picks → {os.path.relpath(out2, HERE)}")


if __name__ == "__main__":
    if sys.argv[2] == "--count":
        print(count(sys.argv[1]))
    elif sys.argv[2] == "--tips":
        print(" ".join(str(t) for t in tips(sys.argv[1])))
    elif sys.argv[2] == "--snapshot":
        snapshot(sys.argv[1], sys.argv[3])
    else:
        main(sys.argv[1], sys.argv[2])
