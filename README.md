# 수시 경쟁률 현황 (웹)
GitHub Actions 가 10분마다 8개 대학 경쟁률 페이지를 읽어 `model.json` 을 갱신하고, `index.html` 이 그것을 보여줍니다.
- 수동 실행: Actions 탭 → "수집" → Run workflow
- 웹페이지: Settings → Pages → Branch main 저장 후 생성되는 주소, 또는 Vercel 에서 이 저장소를 Import


- `site` 브랜치: Vercel 배포용 코드 전용 브랜치 (데이터 커밋은 main 에만 쌓임)
<!-- deploy-try 2026-09-10 12:20 -->
