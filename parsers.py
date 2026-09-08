# -*- coding: utf-8 -*-
"""경쟁률 페이지 파서 (유웨이어플라이 ratio.uwayapply.com / 진학어플라이 addon.jinhakapply.com 공통).

두 대행사 모두 서버 렌더링된 정적 HTML 안에 표가 그대로 들어 있어 JS 실행이 필요 없다.
표 구조는 헤더 텍스트('총모집인원'/'모집인원', '지원인원', '경쟁률', '모집단위')로 찾으며
열 인덱스를 하드코딩하지 않는다. rowspan/colspan 은 격자로 펼쳐서 처리한다.
"""
import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

TOTAL_LABELS = ("총계", "합계", "총 계", "합 계")
SUBTOTAL_LABELS = ("소계", "소 계")


@dataclass
class Cell:
    text: str
    cid: int  # 원본 셀 식별자 (rowspan 으로 펼쳐진 중복 행 제거용)


def to_int(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    return int(s) if re.fullmatch(r"\d+", s) else None  # '제한없음', '-', '' -> None


def to_ratio(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*1", s)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", s)
    return float(m.group(1)) if m else None


def decode_response(content: bytes, header_charset: Optional[str] = None) -> str:
    m = re.search(rb'charset=["\']?\s*([\w-]+)', content[:5000], re.I)
    enc = (m.group(1).decode() if m else None) or header_charset or "utf-8"
    enc = enc.lower()
    if enc in ("euc-kr", "ks_c_5601-1987", "ksc5601"):
        enc = "cp949"  # euc-kr 확장(특수문자 Ⅰ,Ⅱ 등) 안전하게
    try:
        text = content.decode(enc, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    if "경쟁률" not in text:
        # charset 선언이 실제 인코딩과 다른 경우(예: euc-kr 선언 + utf-8 저장) 대비
        for alt in ("utf-8", "cp949"):
            try:
                t2 = content.decode(alt)
            except UnicodeDecodeError:
                continue
            if "경쟁률" in t2:
                return t2
    return text


def parse_page_timestamp(text: str) -> Optional[str]:
    """페이지에 표시된 기준시각 -> 'YYYY-MM-DD HH:MM'."""
    m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2})시\s*(\d{1,2})분\s*기준", text)
    if m:  # 유웨이: 2026년 09월 07일 21시 50분 기준
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s*(오전|오후)?\s*(\d{1,2}):(\d{2})", text)
    if m:  # 진학: 2026-09-07 오후 9:50
        y, mo, d, ampm, hh, mm = m.groups()
        hh = int(hh)
        if ampm == "오후" and hh < 12:
            hh += 12
        if ampm == "오전" and hh == 12:
            hh = 0
        return f"{y}-{mo}-{d} {hh:02d}:{mm}"
    return None


def table_grid(table) -> list:
    """<table> -> rowspan/colspan 을 펼친 2차원 Cell 격자."""
    rows = []
    pending = {}  # col -> [Cell, remaining_rows]
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue  # 중첩 테이블의 행 제외
        cells = tr.find_all(["th", "td"], recursive=False)
        row, col, ci = [], 0, 0
        while True:
            if col in pending:
                cell, rem = pending[col]
                row.append(cell)
                if rem <= 1:
                    del pending[col]
                else:
                    pending[col][1] = rem - 1
                col += 1
                continue
            if ci >= len(cells):
                # 남은 pending 열이 더 오른쪽에 있으면 이어서 채움
                right = [c for c in pending if c > col]
                if right:
                    col = min(right)
                    continue
                break
            c = cells[ci]
            ci += 1
            cell = Cell(text=c.get_text(" ", strip=True), cid=id(c))
            try:
                rs = max(1, int(c.get("rowspan") or 1))
            except ValueError:
                rs = 1
            try:
                cs = max(1, int(c.get("colspan") or 1))
            except ValueError:
                cs = 1
            for _ in range(cs):
                row.append(cell)
                if rs > 1:
                    pending[col] = [cell, rs - 1]
                col += 1
        if row:
            rows.append(row)
    return rows


def _header_index(grid):
    for i, row in enumerate(grid[:4]):
        texts = [c.text for c in row]
        if any("지원인원" in t or "지원자" in t for t in texts) and any("모집인원" in t for t in texts):
            return i
    return None


def _col(texts, pattern):
    for i, t in enumerate(texts):
        if re.search(pattern, t):
            return i
    return None


def _table_title(table) -> str:
    cap = table.find("caption")
    if cap and cap.get_text(strip=True):
        return cap.get_text(" ", strip=True)
    # 진학어플라이: <h2><strong>전형명</strong> 경쟁률 현황</h2> 가 표 바로 앞에 옴
    for el in table.find_all_previous(["h2", "h3", "h4", "p", "div", "span"], limit=12):
        txt = el.get_text(" ", strip=True)
        if "경쟁률" in txt and "현황" in txt and len(txt) < 150:
            return txt
    return ""


def _is_label(text, labels):
    t = text.replace(" ", "")
    return any(t == l.replace(" ", "") for l in labels)


def parse_ratio_page(html: str) -> dict:
    """경쟁률 페이지 HTML -> 합계/전형별/모집단위별 구조."""
    soup = BeautifulSoup(html, "lxml")
    body_text = soup.get_text(" ", strip=True)
    page_ts = parse_page_timestamp(body_text)

    total = None
    types = []
    units = []
    notes = []

    for table in soup.find_all("table"):
        grid = table_grid(table)
        if not grid:
            continue
        hi = _header_index(grid)
        if hi is None:
            continue
        header = [c.text for c in grid[hi]]
        q_col = _col(header, r"모집인원")
        a_col = _col(header, r"지원인원|지원자")
        r_col = _col(header, r"경쟁률")
        if q_col is None or a_col is None:
            continue
        title = _table_title(table)
        type_name = re.split(r"\s*경쟁률\s*현황", title)[0].strip() or title
        name_cols = [i for i, t in enumerate(header) if i < min(q_col, a_col) and not re.search(r"인원|경쟁률", t)]
        is_unit_table = any("모집단위" in t for t in header)
        groups = {}  # quota cell id -> unit entry (광역 모집단위: 모집인원 rowspan 병합, 지원인원은 학과별)

        for row in grid[hi + 1:]:
            if len(row) <= max(q_col, a_col):
                continue
            texts = [c.text for c in row]
            first = texts[0] if texts else ""
            label_cells = [texts[i] for i in name_cols] if name_cols else [first]
            q_txt, a_txt = texts[q_col], texts[a_col]
            r_txt = texts[r_col] if r_col is not None and r_col < len(texts) else ""
            q, a, r = to_int(q_txt), to_int(a_txt), to_ratio(r_txt)

            # 합계 행 (총계): 문서상 첫 번째 것을 채택 (전체 표 > 전형별 표 순서)
            if any(_is_label(t, TOTAL_LABELS) for t in texts[: max(1, q_col)]):
                if total is None and (q is not None or a is not None):
                    total = {"quota": q, "applicants": a, "ratio_shown": r, "table": title}
                continue
            if any(_is_label(t, SUBTOTAL_LABELS) for t in texts[: max(1, q_col)]):
                continue

            if is_unit_table:
                qcell, acell = row[q_col], row[a_col]
                # 열 이름 기반 매핑
                college = None
                unit_parts = []
                for i, t in enumerate(header):
                    if i >= min(q_col, a_col):
                        break
                    if re.search(r"대학|계열|단과", t) and college is None and not re.search(r"모집단위", t):
                        college = texts[i]
                    elif re.search(r"모집단위|학과|전공|학부", t) and not re.search(r"진로|소개|특징|비고|취업|안내", t):
                        if texts[i] and texts[i] not in unit_parts:
                            unit_parts.append(texts[i])
                if not unit_parts:
                    unit_parts = [t for i, t in enumerate(texts) if i in name_cols and t]
                unit_name = " / ".join(unit_parts) if unit_parts else first
                g = groups.get(qcell.cid)
                if g is None:
                    g = {
                        "type": type_name, "college": college, "unit": unit_name,
                        "quota": q, "quota_text": q_txt, "applicants": a, "ratio": r,
                        "_acells": {acell.cid}, "_subunits": [],
                    }
                    groups[qcell.cid] = g
                    units.append(g)
                elif acell.cid not in g["_acells"]:
                    # 같은 모집인원 셀을 공유하는 하위 학과 행: 지원인원을 합산 (광역 모집단위)
                    g["_acells"].add(acell.cid)
                    if a is not None:
                        g["applicants"] = (g["applicants"] or 0) + a
                    sub = unit_parts[-1] if unit_parts else first
                    if sub and sub not in g["_subunits"]:
                        g["_subunits"].append(sub)
            else:
                # 전형별 표: 이름은 숫자열 앞 텍스트열 중 마지막(전형명), 그룹은 첫 열
                names = [t for t in label_cells if t]
                if not names:
                    continue
                types.append({
                    "group": names[0] if len(names) > 1 else None,
                    "name": names[-1],
                    "quota": q, "quota_text": q_txt,
                    "applicants": a,
                    "ratio": r,
                })

    for g in units:
        subs = g.pop("_subunits", [])
        g.pop("_acells", None)
        if subs:
            g["unit"] = f"{g['unit']} (+{len(subs)}개 학과 광역)"
            g["subunits"] = subs

    if total is None:
        raise ValueError("합계(총계) 행을 찾지 못함")
    if page_ts is None:
        notes.append("페이지 기준시각을 찾지 못함")

    # 전형별 합계와 총계 교차검증
    t_q = sum(t["quota"] or 0 for t in types)
    t_a = sum(t["applicants"] or 0 for t in types)
    if types and total["quota"] is not None and t_q != total["quota"]:
        notes.append(f"전형별 모집인원 합({t_q}) ≠ 총계({total['quota']})")
    if types and total["applicants"] is not None and t_a != total["applicants"]:
        notes.append(f"전형별 지원인원 합({t_a}) ≠ 총계({total['applicants']})")

    return {
        "quota": total["quota"],
        "applicants": total["applicants"],
        "ratio_shown": total["ratio_shown"],
        "page_ts": page_ts,
        "types": types,
        "units": units,
        "notes": notes,
    }


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1]
    with open(path, "rb") as f:
        raw = f.read()
    html = decode_response(raw)
    res = parse_ratio_page(html)
    res["units"] = res["units"][:5] + [{"...": len(res["units"])}]
    print(json.dumps(res, ensure_ascii=False, indent=1))
