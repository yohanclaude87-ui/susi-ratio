# -*- coding: utf-8 -*-
"""산출물 생성: dashboard.html / share.html + share.png / 수시경쟁률_현황.xlsx"""
import html as html_mod
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

KST = timezone(timedelta(hours=9))
WD = "월화수목금토일"


def r2(x):
    """엑셀 ROUND 와 같은 반올림(사사오입) 소수 둘째 자리."""
    if x is None:
        return None
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_iso(s):
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)
    return d.astimezone(KST)


def day_label(d):
    return f"{d.month}월 {d.day:02d}일({WD[d.weekday()]})"


# ----------------------------------------------------------------------------
# 모델
# ----------------------------------------------------------------------------
def build_model(cfg, history, latest):
    now = datetime.now(KST).replace(microsecond=0)
    univs = cfg["universities"]
    keys = [u["key"] for u in univs]
    p = cfg["period"]
    start, end = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
    end_dt = datetime.combine(end, time.fromisoformat(p["end_time"])).replace(tzinfo=KST)
    cutoff_t = time.fromisoformat(cfg["daily_cutoff"])
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    prev = cfg["prev_year"]

    # --- 시계열 (정상 수집값만, 수기 기록 포함)
    series = {k: [] for k in keys}
    for rec in history:
        t = parse_iso(rec["collected_at"])
        for k in keys:
            d = rec["univs"].get(k)
            if d and d.get("ok") and d.get("applicants") is not None:
                series[k].append({"t": t, "a": d["applicants"], "q": d.get("quota"), "src": d.get("source", "auto"),
                                  "pts": d.get("page_ts")})
    for k in keys:
        series[k].sort(key=lambda e: e["t"])

    def quota_of(k):
        L = latest.get("univs", {}).get(k) or {}
        if L.get("quota"):
            return L["quota"]
        u = next(x for x in univs if x["key"] == k)
        return u.get("expected_quota")

    def pick(entries, d, take_last):
        day = [e for e in entries if e["t"].date() == d]
        if not day:
            return None
        cut = datetime.combine(d, cutoff_t).replace(tzinfo=KST)
        # 1순위: 대행사 페이지 기준시각(page_ts)이 컷오프 이하인 것 중 가장 늦은 것
        #        (진학은 10분 단위 갱신이라 17:00 페이지값은 17:00~17:05 수집분에 실림)
        cut_s = cut.strftime("%Y-%m-%d %H:%M")
        window = [e for e in day if cut - timedelta(minutes=60) <= e["t"] <= cut + timedelta(minutes=60)]
        with_pts = [e for e in window if e.get("pts") and e["pts"][:16] <= cut_s and e["pts"][:10] == d.isoformat()]
        if with_pts:
            best = max(with_pts, key=lambda e: (e["pts"][:16], e["t"]))
            if best["pts"][:16] >= (cut - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M"):
                return best
        before = [e for e in day if e["t"] <= cut + timedelta(minutes=5)]
        if before and before[-1]["t"] >= cut - timedelta(minutes=60):
            return before[-1]
        after = [e for e in day if cut + timedelta(minutes=5) < e["t"] <= cut + timedelta(minutes=60)]
        if after:
            return after[0]
        return None

    # --- 일별 17:00 기준표 (올해)
    daily = []
    for i, d in enumerate(dates):
        take_last = False  # 대행사 경쟁률 서비스는 마지막 날 17:00 전후 종료 → 마지막 날도 17:00 기준값 사용
        row = {"date": d.isoformat(), "label": day_label(d), "day_index": i + 1,
               "is_final": False, "values": {}}
        for k in keys:
            e = pick(series[k], d, take_last)
            if e:
                q = e["q"] or quota_of(k)
                row["values"][k] = {"applicants": e["a"], "quota": q, "ratio": r2(e["a"] / q) if q else None,
                                    "at": e["t"].isoformat(), "src": e["src"]}
        daily.append(row)

    # --- 전년도 최종표
    prev_rows = []
    for i, ds in enumerate(prev["dates"]):
        d = date.fromisoformat(ds)
        row = {"date": ds, "label": day_label(d), "day_index": i + 1, "values": {}}
        for k in keys:
            v, q = prev["daily"][k][i], prev["quota"][k]
            row["values"][k] = {"applicants": v, "quota": q, "ratio": r2(v / q)}
        prev_rows.append(row)

    # --- 현재값
    current = {}
    for u in univs:
        k = u["key"]
        L = latest.get("univs", {}).get(k)
        if L and L.get("applicants") is not None:
            units = L.get("units", [])
            valid = [x for x in units if x.get("quota") is not None and x.get("applicants") is not None]
            under = [x for x in valid if x["applicants"] < x["quota"]]
            types = []
            for t in L.get("types", []):
                tq, ta = t.get("quota"), t.get("applicants")
                types.append(dict(t, ratio_calc=r2(ta / tq) if (tq and ta is not None) else None,
                                  under=(tq is not None and ta is not None and ta < tq)))
            current[k] = {
                "ok": bool(L.get("ok", True)) and not L.get("stale"),
                "stale": bool(L.get("stale")),
                "error": L.get("last_error"),
                "quota": L["quota"], "applicants": L["applicants"], "ratio": L.get("ratio"),
                "ratio_shown": L.get("ratio_shown"), "page_ts": L.get("page_ts"),
                "collected_at": L.get("collected_at"), "source": L.get("source", "auto"),
                "notes": L.get("notes", []), "types": types,
                "units_total": len(valid), "units_under": len(under),
                "units_under_list": sorted(under, key=lambda x: (x["applicants"] / x["quota"]) if x["quota"] else 0),
                "units": units,
            }
        else:
            current[k] = {"ok": False, "error": (L or {}).get("last_error") or "수집 이력 없음", "types": [], "units": []}

    # --- 비교 지표
    today = now.date()
    if start <= today <= end:
        N = (today - start).days + 1
    elif today > end:
        N = len(dates)
    else:
        N = None
    after_cut = now.time() > cutoff_t
    for k in keys:
        c = current[k]
        a = c.get("applicants")
        if a is None:
            continue
        s = series[k]
        # 최근 1시간 증가: (현재-60분) ±20분 안의 표본이 있을 때만
        target = now - timedelta(minutes=60)
        ref = [e for e in s if abs((e["t"] - target).total_seconds()) <= 20 * 60]
        if ref:
            ref.sort(key=lambda e: abs((e["t"] - target).total_seconds()))
            c["delta_1h"] = a - ref[0]["a"]
        else:
            c["delta_1h"] = None
        today_cut = daily[N - 1]["values"].get(k) if N else None
        ycut = daily[N - 2]["values"].get(k) if (N and N >= 2) else None
        if today_cut and after_cut:
            c["delta_cut"] = {"since": f"오늘 {cfg['daily_cutoff']}", "value": a - today_cut["applicants"]}
        elif ycut:
            c["delta_cut"] = {"since": f"어제 {cfg['daily_cutoff']}", "value": a - ycut["applicants"]}
        else:
            c["delta_cut"] = None
        # 작년 동일 차수(D+N, 17:00) 대비
        if N and N <= len(prev["dates"]):
            pv, pq = prev["daily"][k][N - 1], prev["quota"][k]
            basis = today_cut if (today_cut and after_cut) else None
            this_a = basis["applicants"] if basis else a
            c["vs_prev"] = {"day_index": N, "prev_applicants": pv, "prev_ratio": r2(pv / pq),
                            "this_applicants": this_a,
                            "basis": f"{cfg['daily_cutoff']} 기준" if basis else "현재값(작년은 17:00 기준)",
                            "diff": this_a - pv, "pct": r2((this_a / pv - 1) * 100) if pv else None}
        # 작년 추세 적용 예상 최종: 가장 최근의 17:00 스냅샷(D+M) × 작년최종/작년D+M
        pf, pq = prev["daily"][k][-1], prev["quota"][k]
        proj = None
        for M in range(min(N or 0, len(prev["dates"])), 0, -1):
            snap = daily[M - 1]["values"].get(k)
            if snap and (M < N or after_cut):
                pm = prev["daily"][k][M - 1]
                if pm:
                    est = snap["applicants"] * pf / pm
                    proj = {"day_index": M, "applicants": round(est),
                            "ratio": r2(est / (c["quota"] or quota_of(k))),
                            "prev_final_applicants": pf, "prev_final_ratio": r2(pf / pq)}
                break
        c["projection"] = proj
    ranked = sorted([k for k in keys if current[k].get("ratio") is not None],
                    key=lambda k: (-current[k]["ratio"], -current[k]["applicants"]))
    for i, k in enumerate(ranked):
        current[k]["rank"] = i + 1
    ranked_a = sorted([k for k in keys if current[k].get("applicants") is not None],
                      key=lambda k: -current[k]["applicants"])
    for i, k in enumerate(ranked_a):
        current[k]["rank_applicants"] = i + 1

    page_ts = [c["page_ts"] for c in current.values() if c.get("page_ts")]
    return {
        "generated_at": now.isoformat(),
        "collected_at": latest.get("collected_at"),
        "title": cfg["title"], "subtitle": cfg["subtitle"],
        "year_label": cfg["year_label"], "prev_year_label": cfg["prev_year_label"],
        "period": {"start": start.isoformat(), "end": end.isoformat(), "end_dt": end_dt.isoformat(),
                   "end_time": p["end_time"], "day_index": N, "days": [d.isoformat() for d in dates]},
        "cutoff": cfg["daily_cutoff"],
        "interval_minutes": cfg.get("interval_minutes", 10),
        "univs": [{"key": u["key"], "name": u["name"], "full": u["full"], "agency": u["agency"],
                   "url": u["url"], "admission_site": u.get("admission_site"),
                   "expected_quota": u.get("expected_quota"), "highlight": bool(u.get("highlight")),
                   "auto": u.get("auto", True)} for u in univs],
        "current": current,
        "series": {k: [[e["t"].isoformat(), e["a"], e["src"]] for e in series[k]] for k in keys},
        "daily": daily,
        "prev": {"label": prev["label"], "rows": prev_rows, "quota": prev["quota"],
                 "dates": prev["dates"], "daily": prev["daily"]},
        "page_ts_range": [min(page_ts), max(page_ts)] if page_ts else None,
        "ok_count": sum(1 for c in current.values() if c.get("ok")),
    }


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------
def _json_for_script(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render_dashboard(model, out_path):
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("__MODEL_JSON__", _json_for_script(model))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def _esc(s):
    return html_mod.escape("" if s is None else str(s))


def _fmt(n):
    return "-" if n is None else f"{n:,}"


def _f2(x):
    return "-" if x is None else f"{x:.2f}"


def render_share(model, out_path):
    """카톡 공유용 정적 요약 카드 (1200px)."""
    univs = model["univs"]
    cur = model["current"]
    collected = parse_iso(model["collected_at"]) if model.get("collected_at") else None
    pts = model.get("page_ts_range")
    tiles = []
    for u in univs:
        c = cur[u["key"]]
        hl = " hl" if u["highlight"] else ""
        if c.get("applicants") is None:
            tiles.append(f'<div class="tile{hl}"><div class="nm">{_esc(u["name"])}</div><div class="err">수집 실패</div></div>')
            continue
        vp = c.get("vs_prev")
        vp_html = ""
        if vp:
            sign = "+" if vp["diff"] >= 0 else ""
            pct = f" ({sign}{vp['pct']:.1f}%)" if vp.get("pct") is not None else ""
            vp_html = f'<div class="sub">작년 D+{vp["day_index"]} {_fmt(vp["prev_applicants"])}명 → {_fmt(vp["this_applicants"])}명 <b>{sign}{_fmt(vp["diff"])}</b>{pct}</div>'
        rank = f'<span class="rank">{c.get("rank", "-")}위</span>'
        stale = ' <span class="stale">이전값</span>' if c.get("stale") else ""
        tiles.append(
            f'<div class="tile{hl}"><div class="nm">{rank}{_esc(u["name"])}{stale}</div>'
            f'<div class="hero">{_f2(c["ratio"])}<span class="unit"> : 1</span></div>'
            f'<div class="sub">지원 <b>{_fmt(c["applicants"])}</b> / 모집 {_fmt(c["quota"])}</div>'
            f'{vp_html}<div class="ts">기준 {_esc((c.get("page_ts") or "")[5:])}</div></div>'
        )

    def table(rows, year_label, quotas, final=False):
        h1 = f'<tr><th rowspan="3" class="gu">구분</th><th colspan="{len(univs) * 2}" class="yr">{_esc(year_label)} 수시모집</th></tr>'
        h2 = "".join(f'<th colspan="2" class="un"><span>{_esc(u["name"])}</span><span class="q">{_fmt(quotas.get(u["key"]))}</span></th>' for u in univs)
        h3 = "".join('<th class="sh">지원자</th><th class="sh">경쟁률</th>' for _ in univs)
        body = []
        for r in rows:
            cells = []
            for u in univs:
                v = r["values"].get(u["key"])
                if v:
                    cells.append(f'<td class="n">{_fmt(v["applicants"])}</td><td class="n">{_f2(v["ratio"])}</td>')
                else:
                    cells.append('<td class="n"></td><td class="n">0.00</td>')
            lab = _esc(r["label"]) + (" 최종" if r.get("is_final") else "")
            body.append(f'<tr><td class="lab">{lab}</td>{"".join(cells)}</tr>')
        return f'<table class="grid">{h1}<tr>{h2}</tr><tr>{h3}</tr>{"".join(body)}</table>'

    this_q = {u["key"]: (cur[u["key"]].get("quota") or u.get("expected_quota")) for u in univs}
    when = collected.strftime("%Y-%m-%d %H:%M") if collected else "-"
    pts_txt = f"페이지 기준 {pts[0][5:]}~{pts[1][5:]}" if pts and pts[0] != pts[1] else (f"페이지 기준 {pts[0][5:]}" if pts else "")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>share</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#ffffff;font-family:"Malgun Gothic","Segoe UI",system-ui,sans-serif;color:#0b0b0b;width:1200px}}
.wrap{{padding:18px 22px}}
h1{{font-size:22px;margin:0 0 2px}} .meta{{color:#52514e;font-size:13px;margin-bottom:12px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}}
.tile{{border:1px solid rgba(11,11,11,.12);border-radius:8px;padding:8px 10px;background:#fcfcfb}}
.tile.hl{{border:2px solid #2a78d6;background:#eef4fc}}
.nm{{font-weight:700;font-size:15px;display:flex;align-items:center;gap:6px}}
.rank{{font-size:11px;background:#0b0b0b;color:#fff;border-radius:10px;padding:1px 6px}}
.stale{{font-size:10px;color:#d03b3b;border:1px solid #d03b3b;border-radius:8px;padding:0 4px}}
.hero{{font-size:30px;font-weight:700;line-height:1.15;margin:2px 0}} .unit{{font-size:13px;font-weight:400;color:#52514e}}
.sub{{font-size:12px;color:#52514e}} .sub b{{color:#0b0b0b}} .ts{{font-size:11px;color:#898781;margin-top:2px}} .err{{color:#d03b3b}}
.grid{{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:10px;table-layout:fixed}}
.grid th,.grid td{{border:1px solid #9a9a9a;padding:3px 2px;text-align:center;white-space:nowrap;overflow:hidden}}
.grid .yr{{background:#fff2cc;font-weight:700;font-size:13px}} .grid .gu{{background:#fff2cc;width:92px}}
.grid .un{{background:#fff2cc;font-weight:700}} .grid .un .q{{margin-left:6px}} .grid .sh{{background:#fafafa;font-weight:400}}
.grid .lab{{text-align:center;background:#fafafa;width:92px}} .grid .n{{text-align:right;padding-right:6px}}
.cap{{font-size:12px;color:#52514e;margin:0 0 3px}}
</style></head><body><div class="wrap">
<h1>{_esc(model["title"])} <span style="font-size:14px;font-weight:400;color:#52514e">{_esc(model["subtitle"])}</span></h1>
<div class="meta">수집 {when} · {pts_txt} · 경쟁률 = 지원자 ÷ 모집인원 · 작년 비교는 D+N일 {_esc(model["cutoff"])} 기준 · 정상 {model["ok_count"]}/{len(univs)}개교</div>
<div class="tiles">{"".join(tiles)}</div>
<p class="cap">{_esc(model["year_label"])} 일별 현황 ({_esc(model["cutoff"])} 기준, 마지막 날은 최종)</p>
{table(model["daily"], model["year_label"], this_q)}
<p class="cap">{_esc(model["prev"]["label"])} 최종 (전년도)</p>
{table(model["prev"]["rows"], model["prev"]["label"], model["prev"]["quota"])}
</div></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)


def make_png(cfg, html_path, png_path, width=1200, height=1000):
    exe = next((p for p in cfg.get("chrome_paths", []) if os.path.exists(p)), None)
    if not exe:
        print("  ! 크롬/엣지를 찾지 못해 PNG 생략")
        return False
    profile = os.path.join(tempfile.gettempdir(), "susi_ratio_chrome_profile")
    cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions", f"--user-data-dir={profile}",
           "--force-device-scale-factor=2", f"--window-size={width},{height}",
           f"--screenshot={png_path}", Path(html_path).resolve().as_uri()]
    try:
        subprocess.run(cmd, capture_output=True, timeout=90)
        return os.path.exists(png_path)
    except Exception as e:  # noqa: BLE001
        print("  ! PNG 생성 실패:", e)
        return False


# ----------------------------------------------------------------------------
# XLSX
# ----------------------------------------------------------------------------
def render_xlsx(model, history, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    univs = model["univs"]
    cur = model["current"]
    wb = Workbook()
    ws = wb.active
    ws.title = "경쟁률현황"
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    yellow = PatternFill("solid", fgColor="FFF2CC")
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    bold = Font(bold=True)

    def write_table(r0, rows, year_label, quotas, final_last=False):
        ws.cell(r0, 1, "구분").font = bold
        ws.merge_cells(start_row=r0, start_column=1, end_row=r0 + 2, end_column=1)
        ws.cell(r0, 2, f"{year_label} 수시모집").font = bold
        ws.merge_cells(start_row=r0, start_column=2, end_row=r0, end_column=1 + len(univs) * 2)
        for i, u in enumerate(univs):
            c = 2 + i * 2
            ws.cell(r0 + 1, c, u["name"]).font = bold
            ws.cell(r0 + 1, c + 1, quotas.get(u["key"])).font = bold
            ws.cell(r0 + 1, c + 1).number_format = "#,##0"
            ws.cell(r0 + 2, c, "지원자")
            ws.cell(r0 + 2, c + 1, "경쟁률")
        for rr in range(r0, r0 + 3):
            for cc in range(1, 2 + len(univs) * 2):
                cell = ws.cell(rr, cc)
                cell.fill = yellow if rr < r0 + 2 else PatternFill()
                cell.border = border
                cell.alignment = center
        r = r0 + 3
        for row in rows:
            lab = row["label"] + (" 최종" if row.get("is_final") else "")
            ws.cell(r, 1, lab).alignment = center
            ws.cell(r, 1).border = border
            for i, u in enumerate(univs):
                c = 2 + i * 2
                v = row["values"].get(u["key"])
                a_cell, q_cell = ws.cell(r, c), ws.cell(r, c + 1)
                if v:
                    a_cell.value = v["applicants"]
                    q_cell.value = f"=IFERROR(ROUND({get_column_letter(c)}{r}/{get_column_letter(c + 1)}{r0 + 1},2),0)"
                else:
                    q_cell.value = f"=IFERROR(ROUND({get_column_letter(c)}{r}/{get_column_letter(c + 1)}{r0 + 1},2),0)"
                a_cell.number_format = "#,##0"
                q_cell.number_format = "0.00"
                for cell in (a_cell, q_cell):
                    cell.border = border
                    cell.alignment = right
            r += 1
        return r

    collected = parse_iso(model["collected_at"]).strftime("%Y-%m-%d %H:%M") if model.get("collected_at") else "-"
    ws.cell(1, 1, f"{collected} 수집 기준 (일별표는 매일 {model['cutoff']} 기준값)")
    this_q = {u["key"]: (cur[u["key"]].get("quota") or u.get("expected_quota")) for u in univs}
    r = write_table(2, model["daily"], model["year_label"], this_q)
    ws.cell(r + 1, 1, "최종").font = bold
    r = write_table(r + 2, model["prev"]["rows"], model["prev"]["label"], model["prev"]["quota"])
    # 현재값 표
    r += 1
    ws.cell(r, 1, "현재값(최신 수집)").font = bold
    r += 1
    hdr = ["대학", "모집인원", "지원자", "경쟁률", "페이지 기준시각", "수집시각", "순위", "최근1h 증가", "작년 D+N 지원자", "작년 대비", "예상 최종 경쟁률(작년추세)", "상태"]
    for i, h in enumerate(hdr):
        cell = ws.cell(r, 1 + i, h)
        cell.font = bold
        cell.fill = yellow
        cell.border = border
        cell.alignment = center
    r += 1
    for u in univs:
        c = cur[u["key"]]
        vp, pj = c.get("vs_prev"), c.get("projection")
        vals = [u["name"], c.get("quota"), c.get("applicants"), c.get("ratio"), c.get("page_ts"),
                (c.get("collected_at") or "")[:16].replace("T", " "), c.get("rank"), c.get("delta_1h"),
                vp["prev_applicants"] if vp else None, vp["diff"] if vp else None,
                pj["ratio"] if pj else None,
                ("정상" if c.get("ok") else ("이전값(수집실패)" if c.get("stale") else "실패"))]
        for i, v in enumerate(vals):
            cell = ws.cell(r, 1 + i, v)
            cell.border = border
            if isinstance(v, int):
                cell.number_format = "#,##0"
            if isinstance(v, float):
                cell.number_format = "0.00"
        r += 1
    ws.column_dimensions["A"].width = 16
    for i in range(2, 2 + len(univs) * 2):
        ws.column_dimensions[get_column_letter(i)].width = 9
    ws.freeze_panes = "B5"

    # 수집 이력
    ws2 = wb.create_sheet("수집이력")
    hdr = ["수집시각", "출처"]
    for u in univs:
        hdr += [f"{u['name']} 지원자", f"{u['name']} 경쟁률", f"{u['name']} 기준시각"]
    ws2.append(hdr)
    for rec in history:
        row = [parse_iso(rec["collected_at"]).strftime("%Y-%m-%d %H:%M"), rec.get("source", "")]
        for u in univs:
            d = rec["univs"].get(u["key"]) or {}
            row += [d.get("applicants"), d.get("ratio"), d.get("page_ts") or d.get("error", "")]
        ws2.append(row)
    ws2.freeze_panes = "A2"

    # 전형별 (최신)
    ws3 = wb.create_sheet("전형별(최신)")
    ws3.append(["대학", "구분", "전형", "모집인원", "지원인원", "경쟁률", "미달여부", "페이지 기준시각"])
    for u in univs:
        c = cur[u["key"]]
        for t in c.get("types", []):
            ws3.append([u["name"], t.get("group"), t["name"], t.get("quota"), t.get("applicants"),
                        t.get("ratio_calc"), "미달" if t.get("under") else "", c.get("page_ts")])
    ws3.freeze_panes = "A2"

    # 모집단위별 (최신)
    ws4 = wb.create_sheet("모집단위(최신)")
    ws4.append(["대학", "전형", "단과대학", "모집단위", "모집인원", "지원인원", "경쟁률", "미달여부"])
    for u in univs:
        c = cur[u["key"]]
        for x in c.get("units", []):
            under = x.get("quota") is not None and x.get("applicants") is not None and x["applicants"] < x["quota"]
            ws4.append([u["name"], x.get("type"), x.get("college"), x.get("unit"), x.get("quota") if x.get("quota") is not None else x.get("quota_text"),
                        x.get("applicants"), x.get("ratio"), "미달" if under else ""])
    ws4.freeze_panes = "A2"
    tmp = out_path + ".tmp.xlsx"
    wb.save(tmp)
    try:
        os.replace(tmp, out_path)
    except PermissionError:
        print("  ! 엑셀 파일이 열려 있어 갱신하지 못함:", out_path)
        os.remove(tmp)


# ----------------------------------------------------------------------------
def render_all(cfg, history, latest, base):
    model = build_model(cfg, history, latest)
    with open(os.path.join(base, "data", "model.json"), "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False)
    render_dashboard(model, os.path.join(base, "dashboard.html"))
    share_html = os.path.join(base, "share.html")
    render_share(model, share_html)
    png = os.path.join(base, "share.png")
    if make_png(cfg, share_html, png):
        stamp = parse_iso(model["collected_at"]).strftime("%Y%m%d_%H%M") if model.get("collected_at") else "now"
        os.makedirs(os.path.join(base, "data", "png"), exist_ok=True)
        shutil.copyfile(png, os.path.join(base, "data", "png", f"share_{stamp}.png"))
    render_xlsx(model, history, os.path.join(base, "수시경쟁률_현황.xlsx"))
    print("  산출물 갱신: dashboard.html, share.png, 수시경쟁률_현황.xlsx")
    return model
