// 최신 model.json 을 GitHub API 로 직접 읽어 돌려줌 (raw CDN 5분 캐시 회피). 30초 메모리 캐시.
let cache = { at: 0, body: null };
module.exports = async (req, res) => {
  const now = Date.now();
  if (cache.body && now - cache.at < 30000) { res.setHeader("Content-Type", "application/json; charset=utf-8"); res.setHeader("Cache-Control", "no-store"); res.status(200).send(cache.body); return; }
  const headers = { "Accept": "application/vnd.github.raw", "User-Agent": "susi-model" };
  if (process.env.GH_TOKEN) headers["Authorization"] = "Bearer " + process.env.GH_TOKEN;
  try {
    const r = await fetch("https://api.github.com/repos/yohanclaude87-ui/susi-ratio/contents/model.json?ref=main", { headers });
    if (!r.ok) throw new Error("github " + r.status);
    const body = await r.text();
    cache = { at: now, body };
    res.setHeader("Content-Type", "application/json; charset=utf-8"); res.setHeader("Cache-Control", "no-store");
    res.status(200).send(body);
  } catch (e) {
    res.status(502).send(JSON.stringify({ error: String(e && e.message) }));
  }
};
