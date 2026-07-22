"""
The dashboard page, served at GET /. Self-contained HTML/CSS/JS (no external
assets) so it works on an isolated homelab network. It talks to the receiver's
/api/* endpoints to search campgrounds, list watches, and add/remove them —
all applied live, no container restart.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>campwatch</title>
<style>
  :root {
    --bg: #f6f7f5; --card: #fff; --ink: #1b241e; --muted: #5c6b60;
    --line: #dde3dd; --accent: #2e7d43; --accent-ink: #fff; --danger: #b3261e;
    --chip: #eef3ee;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #12160f; --card: #1b2016; --ink: #e7ece2; --muted: #9aa896;
      --line: #2c3626; --accent: #74be7a; --accent-ink: #12160f; --danger: #f2b8b5;
      --chip: #232a1d;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
  header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
  h1 { font-size: 22px; margin: 0; letter-spacing: -0.01em; }
  h1 .tent { color: var(--accent); }
  .sub { color: var(--muted); font-size: 13px; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); margin: 28px 0 10px; }
  .card { background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 16px; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 0 0 4px; }
  input, select { width: 100%; padding: 9px 10px; border: 1px solid var(--line);
    border-radius: 8px; background: var(--bg); color: var(--ink); font: inherit; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  .row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  .field { position: relative; }
  .checks { display: flex; gap: 20px; align-items: center; margin: 4px 0 14px; }
  .checks label { display: flex; align-items: center; gap: 7px; color: var(--ink); margin: 0; }
  .checks input { width: auto; }
  button { font: inherit; border: 0; border-radius: 8px; padding: 9px 16px; cursor: pointer; }
  .primary { background: var(--accent); color: var(--accent-ink); font-weight: 600; }
  .primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .ac { position: absolute; z-index: 20; left: 0; right: 0; top: 100%; margin-top: 4px;
    background: var(--card); border: 1px solid var(--line); border-radius: 8px;
    max-height: 240px; overflow-y: auto; box-shadow: 0 6px 24px rgba(0,0,0,.18); }
  .ac div { padding: 9px 11px; cursor: pointer; }
  .ac div:hover, .ac div.on { background: var(--chip); }
  .ac .rid { color: var(--muted); font-size: 12px; }
  .picked { font-size: 13px; color: var(--accent); margin: -6px 0 12px; min-height: 18px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--line);
    font-size: 14px; vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase;
    letter-spacing: .05em; }
  .tag { display: inline-block; padding: 1px 8px; border-radius: 999px; background: var(--chip);
    font-size: 12px; color: var(--muted); }
  .tag.hold { background: #fde8b0; color: #6b4e00; }
  .tag.dry { background: var(--chip); }
  .del { background: transparent; color: var(--danger); border: 1px solid var(--line); padding: 5px 10px; }
  .msg { padding: 10px 12px; border-radius: 8px; margin: 10px 0 0; font-size: 14px; display: none; }
  .msg.err { background: #fdecea; color: #7a1c16; display: block; }
  .msg.ok { background: var(--chip); color: var(--ink); display: block; }
  @media (prefers-color-scheme: dark) { .msg.err { background: #3a1f1c; color: #f2b8b5; } }
  .empty { color: var(--muted); font-size: 14px; padding: 12px 0; }
  .events { font-size: 13px; }
  .events li { color: var(--muted); margin-bottom: 4px; list-style: none; }
  .events ul { padding: 0; margin: 0; }
  @media (max-width: 560px) { .row, .row3 { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="tent">&#9974;</span> campwatch</h1>
    <span class="sub" id="prov"></span>
  </header>
  <div class="sub" id="lastcheck"></div>

  <h2>Add a site to watch</h2>
  <div class="card">
    <div class="field" style="margin-bottom:12px">
      <label for="q">Search for a campground</label>
      <input id="q" autocomplete="off" placeholder="Start typing a park name, e.g. Kanaskat">
      <div class="ac" id="ac" style="display:none"></div>
    </div>
    <div class="picked" id="picked"></div>
    <div class="row">
      <div><label for="start">First night (start)</label><input id="start" type="date"></div>
      <div><label for="end">Checkout (end)</label><input id="end" type="date"></div>
    </div>
    <div class="row3">
      <div><label for="nights">Nights</label><input id="nights" type="number" min="1" value="1"></div>
      <div><label for="notify">Notify via</label><select id="notify"></select></div>
      <div><label for="name">Label</label><input id="name" placeholder="auto"></div>
    </div>
    <div class="checks">
      <label><input type="checkbox" id="auto_hold"> Auto-hold <span class="sub">(needs capture + session)</span></label>
    </div>
    <button class="primary" id="add" disabled>Add watch</button>
    <div class="msg" id="msg"></div>
  </div>

  <h2>Watched sites</h2>
  <div class="card" style="padding:4px 16px">
    <table>
      <thead><tr><th>Label</th><th>Park&nbsp;ID</th><th>Dates</th><th>Nights</th><th>Notify</th><th>Mode</th><th></th></tr></thead>
      <tbody id="watches"></tbody>
    </table>
    <div class="empty" id="noWatches" style="display:none">No watches yet — add one above.</div>
  </div>

  <h2>Recent activity</h2>
  <div class="card events"><ul id="events"><li>No events yet.</li></ul></div>
</div>

<script>
const $ = id => document.getElementById(id);
let picked = null, sinks = [], acItems = [], acIndex = -1;

function slug(s){ return (s||"").toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,""); }

async function loadMeta(){
  const m = await (await fetch("api/meta")).json();
  $("prov").textContent = m.provider.host + " · rec area " + m.provider.rec_area_id;
  sinks = m.sinks;
  $("notify").innerHTML = sinks.map(s => `<option>${s}</option>`).join("");
  if (m.defaults.notify) $("notify").value = m.defaults.notify;
  if (m.defaults.nights) $("nights").value = m.defaults.nights;
}

async function loadWatches(){
  const d = await (await fetch("api/watches")).json();
  const tb = $("watches"); tb.innerHTML = "";
  $("noWatches").style.display = d.watches.length ? "none" : "block";
  for (const w of d.watches){
    const mode = w.auto_hold
      ? `<span class="tag hold">auto-hold${w.dry_run ? " (dry)" : ""}</span>`
      : `<span class="tag">notify</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${w.name}</td><td>${w.campground_id}</td>
      <td>${w.start_date} &rarr; ${w.end_date}</td><td>${w.nights}</td>
      <td>${w.notify}</td><td>${mode}</td>
      <td><button class="del" data-n="${w.name}">Remove</button></td>`;
    tb.appendChild(tr);
  }
  tb.querySelectorAll(".del").forEach(b => b.onclick = () => removeWatch(b.dataset.n));
}

async function loadStatus(){
  try {
    const s = await (await fetch("status")).json();
    $("lastcheck").textContent = s.last_webhook_at
      ? "Last opening reported: " + new Date(s.last_webhook_at).toLocaleString()
      : "No openings reported yet.";
    const ev = s.recent_events.slice().reverse();
    $("events").innerHTML = ev.length
      ? ev.map(e => `<li>${e.watch || "—"}: <b>${e.action}</b> ${e.detail || ""}</li>`).join("")
      : "<li>No events yet.</li>";
  } catch(e){}
}

let acTimer = null;
$("q").addEventListener("input", () => {
  picked = null; $("picked").textContent = ""; refreshAddBtn();
  clearTimeout(acTimer);
  acTimer = setTimeout(runSearch, 220);
});
$("q").addEventListener("keydown", e => {
  if (!acItems.length) return;
  if (e.key === "ArrowDown"){ acIndex = Math.min(acIndex+1, acItems.length-1); paintAc(); e.preventDefault(); }
  else if (e.key === "ArrowUp"){ acIndex = Math.max(acIndex-1, 0); paintAc(); e.preventDefault(); }
  else if (e.key === "Enter"){ if (acIndex>=0){ choose(acItems[acIndex]); e.preventDefault(); } }
  else if (e.key === "Escape"){ hideAc(); }
});

async function runSearch(){
  const q = $("q").value.trim();
  if (q.length < 2){ hideAc(); return; }
  let d;
  try { d = await (await fetch("api/search?q=" + encodeURIComponent(q))).json(); }
  catch(e){ hideAc(); return; }
  if (d.error){ showMsg(d.error, true); hideAc(); return; }
  acItems = d.results || []; acIndex = -1; paintAc();
}

function paintAc(){
  const ac = $("ac");
  if (!acItems.length){ hideAc(); return; }
  ac.innerHTML = acItems.map((o,i) =>
    `<div class="${i===acIndex?'on':''}" data-i="${i}">${o.name}
     <span class="rid">#${o.campground_id}</span></div>`).join("");
  ac.style.display = "block";
  ac.querySelectorAll("div").forEach(el => el.onclick = () => choose(acItems[el.dataset.i]));
}
function hideAc(){ $("ac").style.display = "none"; acItems = []; acIndex = -1; }

function choose(o){
  picked = o;
  $("q").value = o.name;
  $("picked").textContent = "Selected: " + o.name + " (id " + o.campground_id +
    (o.map_id ? ", map " + o.map_id : "") + ")";
  if (!$("name").value) $("name").value = slug(o.name);
  hideAc(); refreshAddBtn();
}

function refreshAddBtn(){
  $("add").disabled = !(picked && $("start").value && $("end").value);
}
["start","end"].forEach(id => $(id).addEventListener("change", () => {
  if (picked && !$("name").value) $("name").value = slug(picked.name) + "-" + $("start").value;
  refreshAddBtn();
}));

function showMsg(text, err){
  const m = $("msg"); m.textContent = text; m.className = "msg " + (err ? "err" : "ok");
  if (!err) setTimeout(() => { m.className = "msg"; }, 4000);
}

$("add").onclick = async () => {
  if (!picked) return;
  const body = {
    name: $("name").value.trim() || (slug(picked.name) + "-" + $("start").value),
    campground_id: picked.campground_id,
    resource_location_id: picked.resource_location_id,
    map_id: picked.map_id,
    start_date: $("start").value,
    end_date: $("end").value,
    nights: parseInt($("nights").value, 10) || 1,
    notify: $("notify").value,
    auto_hold: $("auto_hold").checked,
  };
  $("add").disabled = true;
  const res = await fetch("api/watches", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const d = await res.json();
  if (!res.ok){ showMsg(d.error || "Failed to add watch", true); $("add").disabled = false; return; }
  showMsg("Added “" + body.name + "” — now live.", false);
  picked = null; $("q").value = ""; $("name").value = ""; $("picked").textContent = "";
  refreshAddBtn(); loadWatches();
};

async function removeWatch(name){
  if (!confirm("Remove watch “" + name + "”?")) return;
  const res = await fetch("api/watches/" + encodeURIComponent(name), { method: "DELETE" });
  const d = await res.json();
  if (!res.ok){ showMsg(d.error || "Failed to remove", true); return; }
  showMsg("Removed “" + name + "”.", false); loadWatches();
}

document.addEventListener("click", e => { if (!$("q").contains(e.target) && !$("ac").contains(e.target)) hideAc(); });

loadMeta().then(loadWatches).then(loadStatus);
setInterval(loadStatus, 15000);
setInterval(loadWatches, 30000);
</script>
</body>
</html>
"""
