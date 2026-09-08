// Vercel 서버리스 함수 (서울 icn1 리전): 대행사 경쟁률 페이지를 한국 IP로 대신 읽어 그대로 돌려줌.
// _config.json 에 등록된 8개 URL 만 허용 (그 외 거부). GitHub Actions(해외 IP)가 진학어플라이(해외 차단)를 읽을 때 사용.
const cfg = require("./_config.json");
const ALLOW = new Set(cfg.universities.map(u => u.url));
module.exports = async (req, res) => {
  const raw = String((req.query && req.query.url) || "");
  if (!ALLOW.has(raw)) { res.status(403).send("url not allowed"); return; }
  try {
    const r = await fetch(raw, {
      redirect: "follow",
      headers: {
        "User-Agent": cfg.user_agent,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
      },
    });
    const buf = Buffer.from(await r.arrayBuffer());
    res.setHeader("Content-Type", r.headers.get("content-type") || "text/html");
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Upstream-Status", String(r.status));
    res.status(r.status).send(buf);
  } catch (e) {
    res.status(502).send("upstream error");
  }
};
