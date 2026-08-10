// WIP homepage for calibrewebnextgen.com — served on the apex + www.
// CWNG identity: dark charcoal-blue + amber. Links out to the project surfaces.
const HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calibre-Web NextGen</title>
<meta name="description" content="Calibre-Web NextGen — a community-maintained Calibre-Web-Automated build that ships fixes today. Self-hosted eBook library with auto-ingest, kepubify, KOReader &amp; Kobo sync.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0f1218; --bg2:#151a23; --ink:#eef1f6; --muted:#9aa4b2;
    --accent:#cc7b19; --accent2:#e69a3c; --line:rgba(255,255,255,.08);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{
    background:
      radial-gradient(900px 500px at 80% -10%, rgba(204,123,25,.16), transparent 60%),
      radial-gradient(700px 500px at 0% 110%, rgba(60,90,140,.18), transparent 55%),
      var(--bg);
    color:var(--ink); font-family:Inter,system-ui,sans-serif; line-height:1.55;
    display:flex; flex-direction:column; min-height:100%;
    -webkit-font-smoothing:antialiased;
  }
  /* subtle grain */
  body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.035;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");}
  main{flex:1;display:flex;align-items:center;justify-content:center;padding:6vh 24px}
  .card{max-width:680px;width:100%}
  .badge{display:inline-flex;align-items:center;gap:8px;font-size:12px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--accent2);border:1px solid var(--line);
    border-radius:999px;padding:6px 14px;background:rgba(204,123,25,.06)}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 10px var(--accent)}
  h1{font-family:Fraunces,serif;font-weight:600;font-size:clamp(40px,8vw,74px);line-height:1.02;
    margin:24px 0 0;letter-spacing:-.02em}
  h1 .nx{color:var(--accent)}
  p.lede{font-size:clamp(17px,2.4vw,21px);color:var(--muted);margin:20px 0 0;max-width:60ch}
  .row{display:flex;flex-wrap:wrap;gap:14px;margin-top:36px}
  a.btn{display:inline-flex;align-items:center;gap:9px;text-decoration:none;font-weight:500;
    padding:13px 20px;border-radius:12px;font-size:15px;transition:.18s transform,.18s box-shadow,.18s background}
  a.btn:hover{transform:translateY(-2px)}
  a.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#1a1206;
    box-shadow:0 8px 26px -8px rgba(204,123,25,.6)}
  a.ghost{background:rgba(255,255,255,.04);color:var(--ink);border:1px solid var(--line)}
  a.ghost:hover{background:rgba(255,255,255,.08)}
  .note{margin-top:40px;font-size:13.5px;color:var(--muted)}
  .note code{background:rgba(255,255,255,.06);padding:2px 7px;border-radius:6px;color:var(--accent2);font-size:12.5px}
  footer{padding:22px 24px;text-align:center;color:var(--muted);font-size:12.5px;border-top:1px solid var(--line)}
  footer a{color:var(--muted)}
  svg{width:18px;height:18px;fill:currentColor}
</style>
</head>
<body>
<main>
  <div class="card">
    <span class="badge"><span class="dot"></span> Work in progress</span>
    <h1>Calibre&#8209;Web <span class="nx">NextGen</span></h1>
    <p class="lede">A community&#8209;maintained continuation of Calibre&#8209;Web&#8209;Automated — the self&#8209;hosted eBook library that ships fixes <em>today</em>. Auto&#8209;ingest, kepubify, KOReader &amp; Kobo sync, and a from&#8209;scratch new UI, all in one drop&#8209;in image.</p>
    <div class="row">
      <a class="btn primary" href="https://github.com/new-usemame/Calibre-Web-NextGen">
        <svg viewBox="0 0 24 24"><path d="M12 .5C5.7.5.5 5.7.5 12a11.5 11.5 0 0 0 7.9 10.9c.6.1.8-.2.8-.5v-2c-3.2.7-3.9-1.4-3.9-1.4-.5-1.3-1.3-1.7-1.3-1.7-1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.6-.3-5.3-1.3-5.3-5.7 0-1.3.4-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.4 11.4 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.4-2.7 5.4-5.3 5.7.4.4.8 1.1.8 2.2v3.3c0 .3.2.6.8.5A11.5 11.5 0 0 0 23.5 12C23.5 5.7 18.3.5 12 .5Z"/></svg>
        View on GitHub
      </a>
      <a class="btn ghost" href="https://discord.gg/B8NXZmcp32">Discord</a>
      <a class="btn ghost" href="https://ko-fi.com/calibrewebnextgen">Support on Ko&#8209;fi</a>
    </div>
    <p class="note">Run it: <code>docker pull ghcr.io/new-usemame/calibre-web-nextgen:latest</code></p>
  </div>
</main>
<footer>Calibre&#8209;Web NextGen · community&#8209;maintained · not affiliated with the original authors · <a href="https://github.com/new-usemame/Calibre-Web-NextGen">source</a></footer>
</body>
</html>`;

// Serve our own robots.txt so the apex stops depending on Cloudflare's managed
// injection for it. The policy must stay IDENTICAL to what that injection emits
// today, because the injection is currently the only thing expressing the
// operator's ai-train=no opt-out — turning it off before this shipped would have
// silently deleted the opt-out and left the apex with no robots.txt at all.
//
// Search and AI-ANSWER crawlers (OAI-SearchBot, PerplexityBot, Claude-SearchBot,
// DuckAssistBot, ChatGPT-User, Perplexity-User, Googlebot, Bingbot) are
// deliberately absent from the named groups, so they match `*` and are allowed.
// Only model-TRAINING crawlers get a named group. Per the robots.txt spec a
// crawler that finds a group naming it ignores `*` entirely — that is the whole
// mechanism, so do not collapse these into one group and do not add a search
// crawler to the list.
const AI_TRAINING_CRAWLERS = [
  "Amazonbot", "Applebot-Extended", "Bytespider", "CCBot", "ClaudeBot",
  "CloudflareBrowserRenderingCrawler", "Google-Extended", "GPTBot",
  "meta-externalagent",
];

const ROBOTS = [
  "# Search and AI-answer crawlers are welcome.",
  "# Model training crawlers are opted out below, matching the Content-Signal.",
  "",
  "User-agent: *",
  "Content-Signal: search=yes,ai-train=no,use=reference",
  "Allow: /",
  "",
  ...AI_TRAINING_CRAWLERS.flatMap((ua) => [`User-agent: ${ua}`, "Disallow: /", ""]),
  "Sitemap: https://wiki.calibrewebnextgen.com/sitemap.xml",
  "",
].join("\n");

export default {
  async fetch(request) {
    const { pathname } = new URL(request.url);

    if (pathname === "/robots.txt") {
      return new Response(ROBOTS, {
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Cache-Control": "public, max-age=3600",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }

    return new Response(HTML, {
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=300",
        "X-Content-Type-Options": "nosniff",
      },
    });
  },
};
