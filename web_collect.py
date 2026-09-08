# -*- coding: utf-8 -*-
"""GitHub Actions 용: 5분마다 8개교 수집 → model.json. 진학어플라이는 해외 IP를 막으므로(403)
Vercel 서울 리전 함수(api/proxy.js)를 통해 읽는다(환경변수 SUSI_PROXY)."""
import json, os, sys
from datetime import datetime, timedelta, timezone
os.environ["SUSI_NO_RAW"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect, render

KST = timezone(timedelta(hours=9))
mpath = os.path.join(collect.BASE, "model.json")

cfg = collect.load_config()
collect.collect_once(cfg, render_outputs=False)
history = collect.load_history()
collect.seed_manual(cfg, history)
model = render.build_model(cfg, history, collect.load_latest())
model["app_version"] = "web (GitHub Actions 5분 · 서울 프록시 경유)"
with open(mpath, "w", encoding="utf-8") as f:
    json.dump(model, f, ensure_ascii=False)
print("model.json 생성")
