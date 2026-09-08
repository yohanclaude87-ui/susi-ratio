# -*- coding: utf-8 -*-
"""GitHub Actions 용: PC 에서 최근 게시가 없을 때만(=PC 꺼짐) 8개교를 직접 수집해 model.json 생성.

진학어플라이(호남대·목포대·순천대)는 해외 IP 를 막으므로(403) GitHub 에서는 유웨이 5개교만 갱신되고
진학 3개교는 마지막 값이 '이전값'으로 남는다. 평소에는 PC 의 exe 가 8개교 전체를 5분마다 게시한다.
"""
import json, os, sys
from datetime import datetime, timedelta, timezone
os.environ["SUSI_NO_RAW"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect, render

KST = timezone(timedelta(hours=9))
FRESH_MINUTES = 20
mpath = os.path.join(collect.BASE, "model.json")
if os.path.exists(mpath):
    try:
        with open(mpath, encoding="utf-8") as f:
            last = json.load(f).get("collected_at")
        if last:
            age = datetime.now(KST) - datetime.fromisoformat(last).astimezone(KST)
            if age < timedelta(minutes=FRESH_MINUTES):
                print(f"PC 게시분이 {int(age.total_seconds() // 60)}분 전 → GitHub 수집 생략")
                sys.exit(0)
    except Exception as e:  # noqa: BLE001
        print("model.json 확인 실패:", e)

cfg = collect.load_config()
collect.collect_once(cfg, render_outputs=False)
history = collect.load_history()
collect.seed_manual(cfg, history)
model = render.build_model(cfg, history, collect.load_latest())
model["app_version"] = "web (GitHub Actions, PC 미가동 시 유웨이 5개교만)"
with open(mpath, "w", encoding="utf-8") as f:
    json.dump(model, f, ensure_ascii=False)
print("model.json 생성")
