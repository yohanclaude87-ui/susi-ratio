# -*- coding: utf-8 -*-
"""GitHub Actions 용: 8개교 수집 후 model.json 생성 (웹페이지가 읽음)."""
import json, os, sys
os.environ["SUSI_NO_RAW"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect, render
cfg = collect.load_config()
collect.collect_once(cfg, render_outputs=False)
history = collect.load_history()
collect.seed_manual(cfg, history)
model = render.build_model(cfg, history, collect.load_latest())
model["app_version"] = "web (GitHub Actions)"
with open(os.path.join(collect.BASE, "model.json"), "w", encoding="utf-8") as f:
    json.dump(model, f, ensure_ascii=False)
print("model.json 생성")
