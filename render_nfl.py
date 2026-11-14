"""nfl_auto.json + nfl_analysis.json → nfl_MMDD.html (단일 파일). 공통 스타일은 template_head.html 을 같이 쓴다.
usage: python render_nfl.py nfl_auto.json nfl_analysis.json out.html"""
import json, os, sys

sys.stdout.reconfigure(encoding="utf-8")
here = os.path.dirname(os.path.abspath(__file__))
auto, an, out = sys.argv[1:4]
A = json.load(open(auto, encoding="utf-8"))
N = json.load(open(an, encoding="utf-8"))
js = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
head = open(os.path.join(here, "template_head.html"), encoding="utf-8").read().replace("<title>NBA 데일리 위젯</title>", "<title>NFL 데일리 위젯</title>")
html = head + open(os.path.join(here, "template_nfl_body.html"), encoding="utf-8").read()
html = html.replace("__AUTO__", js(A), 1).replace("__AN__", js(N), 1)


# render.py 와 같은 방식: 파일 전체를 ASCII로 (스크립트는 \uXXXX, HTML은 &#..;)
def js_esc(s):
    return "".join(c if ord(c) < 128 else "".join(f"\\u{u:04x}" for u in
                   ((ord(c),) if ord(c) < 0x10000 else (0xD800 + ((ord(c) - 0x10000) >> 10), 0xDC00 + ((ord(c) - 0x10000) & 0x3FF))))
                   for c in s)


parts = html.split("<script>")
html = parts[0].encode("ascii", "xmlcharrefreplace").decode() + "".join(
    "<script>" + js_esc(p.split("</script>", 1)[0]) + "</script>" + p.split("</script>", 1)[1].encode("ascii", "xmlcharrefreplace").decode()
    for p in parts[1:])
open(out, "w", encoding="ascii").write(html)
missing = [g["m"] for g in A["games"] if not (N.get("games", {}).get(g["m"]) or {}).get("ctx")]
no_team = sorted({t for g in A["games"] for t in (g["away"], g["home"])} - set(N.get("teams", {})))
print(f"wrote {out} ({len(html)//1024}KB); 분석 없는 경기: {missing or '없음'}; 팀 카드 없는 팀: {no_team or '없음'}")
