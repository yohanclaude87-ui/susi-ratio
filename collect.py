# -*- coding: utf-8 -*-
"""수시 경쟁률 수집기.

사용법
  python collect.py                # 1회 수집 + 대시보드/이미지/엑셀 생성
  python collect.py --loop         # 접수 종료까지 interval_minutes 마다 반복
  python collect.py --render-only  # 수집 없이 저장된 이력으로 산출물만 재생성
  python collect.py --manual gwangju=812 --at "2026-09-08 17:00"   # 수동 값 기록(수집 실패 대비)

산출물 (이 폴더)
  dashboard.html            실시간 대시보드 (브라우저로 열어두면 5분마다 자동 새로고침)
  share.html / share.png    카톡 공유용 요약 카드(이미지)
  수시경쟁률_현황.xlsx        공유표 형식 엑셀 (17:00 기준 일별표 + 전년도 최종표 + 수집이력)
  data/history.jsonl        수집 이력 (1회 수집 = 1행, 전형별 수치 포함)
  data/latest.json          대학별 최신 정상 수집값 (모집단위별 상세 포함)
  data/raw/*.html.gz        수집 원문 HTML (증빙용, gzip)
"""
import argparse
import gzip
import json
import logging
import os
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

FROZEN = getattr(sys, "frozen", False)
BASE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", BASE)  # 실행파일에 내장된 리소스(config.json, ui.html) 위치
sys.path.insert(0, RES)
from parsers import decode_response, parse_ratio_page  # noqa: E402
import render  # noqa: E402

KST = timezone(timedelta(hours=9))
DATA = os.path.join(BASE, "data")
RAW = os.path.join(DATA, "raw")
HISTORY = os.path.join(DATA, "history.jsonl")
LATEST = os.path.join(DATA, "latest.json")
# exe 옆에 config.json 이 있으면 그것을 우선 사용(다음 해 갱신용), 없으면 내장본
CONFIG = os.path.join(BASE, "config.json") if os.path.exists(os.path.join(BASE, "config.json")) else os.path.join(RES, "config.json")
SAVE_RAW = not FROZEN and not os.environ.get("SUSI_NO_RAW")  # 실행파일·웹 모드에서는 원문 HTML 저장 생략
log = logging.getLogger("susi")


def _say(msg):
    """콘솔 출력 + 로그 파일 기록 (앱 모드에서는 콘솔이 없으므로 로그만 남음)."""
    print(msg)
    log.info(msg.strip())


def merge_history(path):
    """다른 PC에서 내보낸 기록(jsonl)을 현재 이력에 합침. (수집시각, 출처)가 같은 행은 건너뜀. → (추가 수, 전체 수)"""
    existing = load_history()
    keys = {(r["collected_at"], r.get("source")) for r in existing}
    added = 0
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or "collected_at" not in rec or not isinstance(rec.get("univs"), dict):
                continue
            k = (rec["collected_at"], rec.get("source"))
            if k in keys:
                continue
            keys.add(k)
            existing.append(rec)
            added += 1
    if added:
        existing.sort(key=lambda r: r["collected_at"])
        os.makedirs(DATA, exist_ok=True)
        tmp = HISTORY + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in existing:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, HISTORY)
    return added, len(existing)


def now_kst():
    return datetime.now(KST).replace(microsecond=0)


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def load_history():
    if not os.path.exists(HISTORY):
        return []
    out = []
    with open(HISTORY, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    out.sort(key=lambda r: r["collected_at"])
    return out


def append_history(rec):
    os.makedirs(DATA, exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_latest():
    if os.path.exists(LATEST):
        with open(LATEST, encoding="utf-8") as f:
            return json.load(f)
    return {"univs": {}}


def save_latest(latest):
    os.makedirs(DATA, exist_ok=True)
    tmp = LATEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LATEST)


def seed_manual(cfg, history):
    """config.manual_seed 의 수기 기록을 이력에 1회 주입 (같은 시각·출처가 이미 있으면 생략)."""
    existing = {(r["collected_at"], r.get("source")) for r in history}
    added = 0
    for seed in cfg.get("manual_seed", []):
        key = (seed["collected_at"], seed["source"])
        if key in existing:
            continue
        univs = {}
        for u in cfg["universities"]:
            v = seed["values"].get(u["key"])
            if v is None:
                continue
            q = seed.get("quota", {}).get(u["key"], u.get("expected_quota"))
            univs[u["key"]] = {"ok": True, "quota": q, "applicants": v,
                               "ratio": render.r2(v / q) if q else None,
                               "page_ts": seed["collected_at"][:16].replace("T", " "),
                               "source": "manual", "types": []}
        rec = {"collected_at": seed["collected_at"], "source": seed["source"], "univs": univs}
        append_history(rec)
        history.append(rec)
        added += 1
    if added:
        history.sort(key=lambda r: r["collected_at"])
    return added


def fetch_one(session, u, cfg, stamp):
    headers = {
        "User-Agent": cfg["user_agent"],
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": u.get("admission_site", ""),
        "Cache-Control": "no-cache",
    }
    last_err = None
    for attempt in range(3):
        try:
            proxy = os.environ.get("SUSI_PROXY")  # 예: https://.../api/proxy?url=
            target = proxy + urllib.parse.quote(u["url"], safe="") if proxy else u["url"]
            r = session.get(target, headers=headers, timeout=40)
            r.raise_for_status()
            header_cs = None
            ct = r.headers.get("Content-Type", "")
            if "charset=" in ct:
                header_cs = ct.split("charset=")[-1].split(";")[0].strip()
            html = decode_response(r.content, header_cs)
            if "경쟁률" not in html:
                raise ValueError("응답에 '경쟁률' 텍스트가 없음 (차단/오류 페이지 가능성)")
            if SAVE_RAW:
                os.makedirs(RAW, exist_ok=True)
                with gzip.open(os.path.join(RAW, f"{stamp}_{u['key']}.html.gz"), "wb") as f:
                    f.write(r.content)
            parsed = parse_ratio_page(html)
            parsed["http_status"] = r.status_code
            parsed["last_modified"] = r.headers.get("Last-Modified")
            return parsed
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 + attempt * 3)
    raise last_err


def collect_once(cfg, render_outputs=True):
    now = now_kst()
    stamp = now.strftime("%Y%m%d_%H%M")
    history = load_history()
    seed_manual(cfg, history)
    latest = load_latest()
    session = requests.Session()
    rec = {"collected_at": now.isoformat(), "source": "auto", "univs": {}}
    _say(f"[{now:%Y-%m-%d %H:%M:%S}] 수집 시작")
    for i, u in enumerate(cfg["universities"]):
        key = u["key"]
        if not u.get("auto", True):
            rec["univs"][key] = {"ok": False, "error": "auto=false (수동)", "skipped": True}
            _say(f"  - {u['name']}: 자동수집 비활성(auto=false)")
            continue
        if i:
            time.sleep(cfg.get("request_delay_seconds", 1.5))
        try:
            p = fetch_one(session, u, cfg, stamp)
            q, a = p["quota"], p["applicants"]
            ratio = render.r2(a / q) if (q and a is not None) else None
            entry = {
                "ok": True, "quota": q, "applicants": a, "ratio": ratio,
                "ratio_shown": p["ratio_shown"], "page_ts": p["page_ts"],
                "types": p["types"], "notes": p["notes"], "source": "auto",
                "last_modified": p.get("last_modified"),
            }
            if u.get("expected_quota") and q != u["expected_quota"]:
                entry["notes"].append(f"모집인원 {q} ≠ 기대값 {u['expected_quota']}")
            rec["univs"][key] = entry
            latest["univs"][key] = dict(entry, units=p["units"], collected_at=now.isoformat(), url=u["url"])
            flag = " ⚠ " + "; ".join(entry["notes"]) if entry["notes"] else ""
            _say(f"  - {u['name']}: 모집 {q:,} / 지원 {a:,} / 경쟁률 {ratio} (기준 {p['page_ts']}){flag}")
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            rec["univs"][key] = {"ok": False, "error": msg}
            prev = latest["univs"].get(key)
            if prev:
                prev["stale"] = True
                prev["last_error"] = msg
                prev["last_error_at"] = now.isoformat()
            _say(f"  - {u['name']}: 실패 - {msg}")
            log.debug("실패 상세 %s", u["url"], exc_info=True)
    latest["collected_at"] = now.isoformat()
    append_history(rec)
    history.append(rec)
    save_latest(latest)
    if render_outputs:
        render.render_all(cfg, history, latest, BASE)
    ok = sum(1 for v in rec["univs"].values() if v.get("ok"))
    _say(f"[{now_kst():%H:%M:%S}] 완료: {ok}/{len(cfg['universities'])}개교 정상")
    return rec


def add_manual(cfg, pairs, at):
    """--manual key=applicants[,quota] ... --at 'YYYY-MM-DD HH:MM'"""
    history = load_history()
    seed_manual(cfg, history)
    t = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=KST) if at else now_kst()
    univs = {}
    byk = {u["key"]: u for u in cfg["universities"]}
    for p in pairs:
        k, v = p.split("=")
        parts = v.split(",")
        a = int(parts[0].replace(",", "")) if len(parts) == 1 else int(parts[0])
        q = int(parts[1]) if len(parts) > 1 else byk[k].get("expected_quota")
        univs[k] = {"ok": True, "quota": q, "applicants": a, "ratio": render.r2(a / q) if q else None,
                    "page_ts": t.strftime("%Y-%m-%d %H:%M"), "source": "manual", "types": []}
    rec = {"collected_at": t.isoformat(), "source": "수동 입력", "univs": univs}
    append_history(rec)
    history.append(rec)
    history.sort(key=lambda r: r["collected_at"])
    latest = load_latest()
    render.render_all(cfg, history, latest, BASE)
    print("수동 기록 추가:", rec["collected_at"], univs)


def period_end(cfg):
    p = cfg["period"]
    return datetime.strptime(p["end"] + " " + p["end_time"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="접수 종료까지 주기 반복")
    ap.add_argument("--interval", type=int, help="반복 간격(분), 기본 config.interval_minutes")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--manual", nargs="*", help="key=지원자[,모집인원] ...")
    ap.add_argument("--at", help="수동 기록 시각 'YYYY-MM-DD HH:MM'")
    args = ap.parse_args()
    cfg = load_config()

    if args.manual:
        add_manual(cfg, args.manual, args.at)
        return
    if args.render_only:
        history = load_history()
        seed_manual(cfg, history)
        render.render_all(cfg, history, load_latest(), BASE)
        print("산출물 재생성 완료")
        return
    if not args.loop:
        collect_once(cfg)
        return

    interval = (args.interval or cfg.get("interval_minutes", 10)) * 60
    stop_at = period_end(cfg) + timedelta(hours=1)
    print(f"반복 수집 시작: {interval // 60}분 간격, 종료 예정 {stop_at:%Y-%m-%d %H:%M} (Ctrl+C 로 중단)")
    while True:
        try:
            collect_once(cfg)
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        if now_kst() > stop_at:
            print("접수 기간 종료 → 반복 수집 종료")
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("중단됨")
            break


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    main()
