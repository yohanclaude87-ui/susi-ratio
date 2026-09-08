// 외부 타이머(cron-job.org 등)가 5분마다 호출하면 GitHub 수집 워크플로를 실행시킴.
// 환경변수: GH_TOKEN (Actions: Read and write 권한 fine-grained token), TRIGGER_KEY (아무 비밀 문자열)
module.exports = async (req, res) => {
  const key = req.query && req.query.key;
  if (!process.env.TRIGGER_KEY || key !== process.env.TRIGGER_KEY) { res.status(403).send("forbidden"); return; }
  const r = await fetch("https://api.github.com/repos/yohanclaude87-ui/susi-ratio/actions/workflows/collect.yml/dispatches", {
    method: "POST",
    headers: { "Authorization": "Bearer " + process.env.GH_TOKEN, "Accept": "application/vnd.github+json",
               "User-Agent": "susi-trigger", "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main" }),
  });
  res.status(r.status === 204 ? 200 : 500).send(r.status === 204 ? "triggered" : "github " + r.status + " " + (await r.text()));
};
