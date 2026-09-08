"""A minimal browser UI for exercising the API by hand during development.

Only mounted when ENVIRONMENT is development (see app/main.py). It exists
because clicking through /docs to test a change is slow, and because the
genre breakdown is a lot easier to sanity check as a bar chart than as a
wall of JSON.

Served from the API rather than as a separate static site on purpose. A page
on another origin would need CORS opened up, and adding CORS to make testing
convenient is exactly the kind of thing that quietly ships to production.
Same origin means no CORS is needed at all.

The page is one self-contained file with no external scripts, fonts, or
stylesheets, so the only CSP relaxation needed is 'self' for this one path.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dev"])

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PlayGraph dev</title>
<style>
  :root {
    --bg:#0d0d10; --panel:#16161b; --line:#26262e; --text:#e8e8ec;
    --dim:#8a8a96; --accent:#7fd1c1; --warn:#e2b76a; --bad:#e07a6a;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.5 ui-monospace,"Cascadia Mono",Consolas,monospace; }
  header { padding:16px 20px; border-bottom:1px solid var(--line);
           display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  h1 { margin:0; font-size:16px; letter-spacing:.5px; }
  .who { color:var(--dim); }
  main { padding:20px; display:grid; gap:16px;
         grid-template-columns:repeat(auto-fit,minmax(380px,1fr)); }
  section { background:var(--panel); border:1px solid var(--line); padding:14px 16px; }
  h2 { margin:0 0 10px; font-size:12px; text-transform:uppercase;
       letter-spacing:1px; color:var(--dim); font-weight:600; }
  button { background:transparent; border:1px solid var(--line); color:var(--text);
           padding:6px 12px; font:inherit; cursor:pointer; }
  button:hover:not(:disabled) { border-color:var(--accent); color:var(--accent); }
  button:disabled { opacity:.4; cursor:default; }
  input { background:#0a0a0d; border:1px solid var(--line); color:var(--text);
          padding:6px 8px; font:inherit; width:100%; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .row + .row { margin-top:8px; }
  pre { background:#0a0a0d; border:1px solid var(--line); padding:10px;
        overflow:auto; max-height:260px; margin:10px 0 0; font-size:12px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  td { padding:3px 6px; border-bottom:1px solid var(--line); }
  td.n { text-align:right; color:var(--dim); white-space:nowrap; }
  .bar { height:9px; background:var(--accent); min-width:2px; display:block; }
  .muted { color:var(--dim); }
  .warn { color:var(--warn); }
  .bad { color:var(--bad); }
  .ok { color:var(--accent); }
  .full { grid-column:1/-1; }
</style>
</head>
<body>
<header>
  <h1>PlayGraph</h1>
  <span class="who" id="who">not signed in</span>
  <span style="flex:1"></span>
  <button id="logout" disabled>log out</button>
</header>

<main>
  <section>
    <h2>1. session</h2>
    <div class="row">
      <button id="login">sign in through Steam</button>
      <span class="muted">opens Steam, returns JSON</span>
    </div>
    <div class="row">
      <input id="token" placeholder="paste access_token from the callback JSON">
      <button id="usetoken">use</button>
    </div>
    <div class="row muted" id="sessionnote">
      Sessions last 30 minutes. The token is kept in this tab only.
    </div>
  </section>

  <section>
    <h2>2. sync</h2>
    <div class="row">
      <button id="sync" disabled>start sync</button>
      <button id="poll" disabled>check status</button>
      <span id="syncstate" class="muted">idle</span>
    </div>
    <pre id="syncout" hidden></pre>
  </section>

  <section class="full">
    <h2>3. genres by playtime</h2>
    <div class="row">
      <button id="loadgenres" disabled>load</button>
      <span class="muted">totals exceed real playtime on purpose, full credit per tag</span>
    </div>
    <div id="genres"></div>
  </section>

  <section class="full">
    <h2>4. library</h2>
    <div class="row">
      <button id="loadlib" disabled>load</button>
      <input id="filter" placeholder="filter by name" style="max-width:240px">
      <span class="muted" id="libcount"></span>
    </div>
    <div id="library"></div>
  </section>
</main>

<script>
const $ = id => document.getElementById(id);
let token = sessionStorage.getItem("pg_token") || "";
let jobId = null;
let library = [];

function setSignedIn(on, name) {
  $("who").textContent = on ? ("signed in as " + (name || "?")) : "not signed in";
  $("who").className = on ? "ok" : "who";
  for (const id of ["sync","poll","loadgenres","loadlib","logout"]) $(id).disabled = !on;
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign({
    headers: token ? {"Authorization": "Bearer " + token} : {}
  }, opts || {}));
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = text; }
  if (!res.ok) throw Object.assign(new Error(res.status), {status: res.status, body});
  return body;
}

function mins(m) {
  if (!m) return "0h";
  const h = Math.floor(m / 60);
  return h >= 1 ? h + "h" : m + "m";
}

$("login").onclick = () => window.open("/auth/steam/login", "_blank");

$("usetoken").onclick = async () => {
  token = $("token").value.trim();
  if (!token) return;
  try {
    // /me/library is the cheapest authenticated call, so it doubles as a
    // "is this token any good" probe.
    await api("/me/library");
    sessionStorage.setItem("pg_token", token);
    $("token").value = "";
    setSignedIn(true, "");
    $("sessionnote").textContent = "token accepted";
    $("sessionnote").className = "row ok";
  } catch (e) {
    setSignedIn(false);
    $("sessionnote").textContent = "rejected: " + (e.body && e.body.detail || e.message);
    $("sessionnote").className = "row bad";
  }
};

$("logout").onclick = async () => {
  try { await api("/auth/logout", {method: "POST"}); } catch (e) {}
  token = ""; sessionStorage.removeItem("pg_token");
  setSignedIn(false);
  $("sessionnote").textContent = "signed out";
  $("sessionnote").className = "row muted";
};

$("sync").onclick = async () => {
  $("syncout").hidden = false;
  try {
    const r = await api("/me/sync", {method: "POST"});
    jobId = r.job_id;
    $("syncstate").textContent = "queued";
    $("syncstate").className = "warn";
    $("syncout").textContent = JSON.stringify(r, null, 2);
    autoPoll();
  } catch (e) {
    $("syncstate").textContent = e.status === 409 ? "already running" : "failed";
    $("syncstate").className = "bad";
    $("syncout").textContent = JSON.stringify(e.body, null, 2);
  }
};

$("poll").onclick = () => pollOnce();

async function pollOnce() {
  if (!jobId) { $("syncstate").textContent = "no job this session"; return null; }
  const r = await api("/me/sync/status/" + jobId);
  $("syncstate").textContent = r.status;
  $("syncstate").className = r.status === "complete" ? "ok" : "warn";
  $("syncout").hidden = false;
  $("syncout").textContent = JSON.stringify(r, null, 2);
  return r;
}

// A sync runs for minutes. Polling every 5s means the page reflects reality
// without anyone having to remember to click.
function autoPoll() {
  const t = setInterval(async () => {
    try {
      const r = await pollOnce();
      if (!r || r.status === "complete" || r.status === "not_found") clearInterval(t);
    } catch (e) { clearInterval(t); }
  }, 5000);
}

$("loadgenres").onclick = async () => {
  const el = $("genres");
  el.textContent = "loading...";
  try {
    const rows = await api("/me/genres");
    if (!rows.length) { el.innerHTML = '<p class="muted">no genre data yet, run a sync</p>'; return; }
    const max = rows[0].total_minutes || 1;
    el.innerHTML = "<table>" + rows.map(r =>
      "<tr><td style='width:130px'>" + esc(r.genre) + "</td>" +
      "<td><span class='bar' style='width:" + (100 * r.total_minutes / max) + "%'></span></td>" +
      "<td class='n'>" + mins(r.total_minutes) + "</td>" +
      "<td class='n'>" + r.game_count + " games</td></tr>"
    ).join("") + "</table>";
  } catch (e) { el.innerHTML = '<p class="bad">' + esc(String(e.body && e.body.detail || e.message)) + '</p>'; }
};

$("loadlib").onclick = async () => {
  const el = $("library");
  el.textContent = "loading...";
  try {
    library = await api("/me/library");
    $("libcount").textContent = library.length + " games";
    renderLib();
  } catch (e) { el.innerHTML = '<p class="bad">' + esc(String(e.body && e.body.detail || e.message)) + '</p>'; }
};

$("filter").oninput = renderLib;

function renderLib() {
  const q = $("filter").value.toLowerCase();
  const rows = library.filter(r => r.game.name.toLowerCase().includes(q)).slice(0, 200);
  $("library").innerHTML = "<table>" + rows.map(r => {
    const a = r.achievements_total
      ? r.achievements_unlocked + "/" + r.achievements_total
      : "";
    return "<tr><td>" + esc(r.game.name) + "</td>" +
      "<td class='muted' style='font-size:11px'>" + esc(r.game.genres || "") + "</td>" +
      "<td class='n'>" + mins(r.playtime_minutes) + "</td>" +
      "<td class='n'>" + a + "</td></tr>";
  }).join("") + "</table>";
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

if (token) {
  api("/me/library").then(() => setSignedIn(true, "")).catch(() => {
    token = ""; sessionStorage.removeItem("pg_token"); setSignedIn(false);
  });
} else setSignedIn(false);
</script>
</body>
</html>
"""


@router.get("/app", response_class=HTMLResponse, include_in_schema=False)
def dev_ui() -> HTMLResponse:
    return HTMLResponse(PAGE)
