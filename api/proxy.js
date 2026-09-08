// Vercel 서버리스 함수 (서울 icn1 리전): 대행사 경쟁률 페이지를 한국 IP로 대신 읽어 그대로 돌려줌.
// 허용된 두 호스트 외에는 거부. GitHub Actions(해외 IP)가 진학어플라이(해외 차단)를 읽을 때 사용.
const ALLOW = new Set(["ratio.uwayapply.com", "addon.jinhakapply.com"]);
module.exports = async (req, res) => {
  const raw = req.query && req.query.url;
  let u;
  try { u = new URL(String(raw || "")); } catch (e) { res.status(400).send("bad url"); return; }
  if (!ALLOW.has(u.hostname)) { res.status(403).send("host not allowed"); return; }
  try {
    const r = await fetch(u.toString(), {
      redirect: "follow",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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
    res.status(502).send("upstream error: " + (e && e.message));
  }
};
