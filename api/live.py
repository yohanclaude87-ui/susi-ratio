# -*- coding: utf-8 -*-
"""Vercel Python 함수(서울): 8개 대학 경쟁률 페이지를 지금 바로 읽어 총계·전형별·모집단위별 수치를 돌려줌.
웹페이지의 '새로고침' 버튼이 호출. 같은 인스턴스 안에서는 60초 캐시(대행사 부하 방지)."""
import json, os, sys, time, re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _parsers import decode_response, parse_ratio_page  # noqa: E402

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "_config.json"), encoding="utf-8") as f:
    CFG = json.load(f)
_CACHE = {"at": 0, "body": None}


def r2(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fetch_univ(u):
    try:
        req = urllib.request.Request(u["url"], headers={"User-Agent": CFG["user_agent"], "Accept-Language": "ko-KR,ko;q=0.9",
                                                          "Referer": u.get("admission_site", ""), "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            ct = r.headers.get("Content-Type", "")
        cs = ct.split("charset=")[-1].split(";")[0].strip() if "charset=" in ct else None
        p = parse_ratio_page(decode_response(raw, cs))
        q, a = p["quota"], p["applicants"]
        types = []
        for t in p["types"]:
            tq, ta = t.get("quota"), t.get("applicants")
            types.append(dict(t, ratio_calc=r2(ta / tq) if (tq and ta is not None) else None,
                              under=(tq is not None and ta is not None and ta < tq)))
        return u["key"], {"ok": True, "quota": q, "applicants": a, "ratio": r2(a / q) if (q and a is not None) else None,
                          "ratio_shown": p["ratio_shown"], "page_ts": p["page_ts"], "types": types, "units": p["units"],
                          "notes": p["notes"]}
    except Exception as e:  # noqa: BLE001
        return u["key"], {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def collect():
    now = time.time()
    if _CACHE["body"] and now - _CACHE["at"] < 60:
        return _CACHE["body"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = dict(ex.map(fetch_univ, CFG["universities"]))
    body = {"collected_at": datetime.now(KST).replace(microsecond=0).isoformat(), "live": True, "univs": results}
    _CACHE.update(at=now, body=body)
    return body


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = json.dumps(collect(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        except Exception as e:  # noqa: BLE001
            body = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
