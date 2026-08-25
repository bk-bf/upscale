<script>
  import { onMount, onDestroy } from "svelte";

  let rows = $state([]);
  let devices = $state([]);
  let book = $state([]);
  let counts = $state({});
  let pending = $state({});
  let running = $state(false);
  let stopping = $state(false);
  let error = $state("");
  let notice = $state("");
  let busy = $state("");
  let noticeTimer;

  let showImport = $state(false);
  let showMachine = $state(false);
  let showStart = $state(false);

  let query = $state("/mnt/media/tv/");
  let listing = $state([]);
  let base = $state("");
  let picked = $state(new Set());
  let searchTimer;

  let mName = $state("");
  let mSsh = $state("");
  let mScratch = $state("");
  let mWorkers = $state("");
  let probe = $state(null);
  let probing = $state(false);

  let sDevices = $state(new Set());
  let sSource = $state("");
  let sTarget = $state("");
  let sArchive = $state("");
  let sDelete = $state(false);
  let sScratch = $state("");
  let sWorkers = $state("");

  let timer;

  async function api(method, path, body) {
    const opts = { method };
    if (method !== "GET") {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body || {});
    }
    const r = await fetch(path, opts);
    const d = await r.json().catch(() => ({ ok: false, error: `${path} → ${r.status}` }));
    return d;
  }

  function say(text) {
    notice = text;
    clearTimeout(noticeTimer);
    if (text) noticeTimer = setTimeout(() => (notice = ""), 6000);
  }

  async function refresh() {
    const d = await api("GET", "/api/queue");
    if (d.ok === false) { error = d.error || "queue unavailable"; return; }
    rows = d.rows || [];
    counts = d.counts || {};
    devices = d.devices || [];
    pending = d.pending || {};
    running = !!d.running;
    stopping = !!d.stopping;
    error = "";
  }

  async function loadBook() {
    const d = await api("GET", "/api/devices");
    if (d.ok) book = d.devices || [];
  }

  async function act(method, path, body, label) {
    busy = label;
    try {
      const d = await api(method, path, body);
      say(d.ok === false ? `${label} failed: ${d.error}` : (d.note || `${label} ok`));
      await refresh();
      return d;
    } finally { busy = ""; }
  }

  async function browse() {
    const d = await api("GET", `/api/browse?q=${encodeURIComponent(query)}`);
    listing = d.results || []; base = d.base || "";
  }
  function onQuery() { clearTimeout(searchTimer); searchTimer = setTimeout(browse, 130); }
  function into(d) { query = d.path + "/"; picked = new Set(); browse(); }
  function up() {
    const p = (base || query).replace(/\/+$/, "");
    query = p.slice(0, p.lastIndexOf("/") + 1) || "/";
    picked = new Set(); browse();
  }

  async function doImport() {
    if (!picked.size) return;
    const d = await act("POST", "/api/import", { paths: [...picked] }, "import");
    if (d?.ok) { showImport = false; picked = new Set(); sSource = d.source; }
  }

  async function doProbe() {
    probing = true; probe = null;
    try {
      probe = await api("POST", "/api/devices/probe", { ssh: mSsh });
      if (probe.reachable && !mName) mName = probe.host.replace(/[^A-Za-z0-9._-]/g, "-");
    } finally { probing = false; }
  }

  async function addMachine() {
    const d = await act("POST", "/api/devices/add",
      { name: mName, ssh: mSsh, scratch: mScratch, workers: mWorkers }, "add machine");
    if (d?.ok) {
      book = d.devices;
      mName = ""; mSsh = ""; mScratch = ""; mWorkers = ""; probe = null;
    }
  }

  async function removeMachine(name) {
    const d = await act("POST", "/api/devices/remove", { name }, `remove ${name}`);
    if (d?.devices) book = d.devices;
    if (d?.ok) sDevices = new Set([...sDevices].filter(n => n !== name));
  }

  function openStart() {
    sSource = sSource || pending.source || "";
    sTarget = sTarget || pending.target || "";
    sArchive = sArchive || pending.archive || "";
    if (!sDevices.size && book.length) sDevices = new Set([book[0].name]);
    showStart = true;
  }

  function toggleDevice(name) {
    const s = new Set(sDevices);
    s.has(name) ? s.delete(name) : s.add(name);
    sDevices = s;
  }

  function startBody(dry) {
    return {
      devices: [...sDevices], source: sSource, target: sTarget,
      archive: sDelete ? "" : sArchive, delete: sDelete,
      scratch: sScratch, workers: sWorkers, dry_run: !!dry,
    };
  }

  async function preview() { await act("POST", "/api/start", startBody(true), "preview"); }

  async function start() {
    const d = await act("POST", "/api/start", startBody(false), "start");
    if (d?.ok) showStart = false;
  }

  let sortKey = $state("n");
  let sortDir = $state(1);
  function sortBy(k) {
    if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = 1; }
  }
  const RANK = { running: 0, paused: 1, queued: 2, done: 3 };
  const sorted = $derived([...rows].sort((a, b) => {
    const k = sortKey;
    let x, y;
    if (k === "status") { x = RANK[a.status] ?? 9; y = RANK[b.status] ?? 9; }
    else if (k === "progress") { x = a.phase_percent ?? -1; y = b.phase_percent ?? -1; }
    else if (k === "rate") { x = a.fps || 0; y = b.fps || 0; }
    else if (k === "eta") { x = a.phase_eta_s || a.eta_s || 0; y = b.phase_eta_s || b.eta_s || 0; }
    else if (k === "n") { x = a.n ?? 1e9; y = b.n ?? 1e9; }
    else { x = (a[k] ?? "").toString().toLowerCase(); y = (b[k] ?? "").toString().toLowerCase(); }
    if (x < y) return -sortDir;
    if (x > y) return sortDir;
    return (a.n ?? 0) - (b.n ?? 0);
  }));

  const focusOnMount = (n) => n.focus();

  const hhmm = (s) => {
    if (!s || s < 0) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
  };
  const gb = (b) => b ? `${(b / 1073741824).toFixed(1)} GB` : "";
  const dir = (p) => (p || "").replace(/\/[^/]*$/, "");

  function phaseRate(r) {
    if (!["running", "paused"].includes(r.status)) return "";
    if (r.phase_unit === "bytes" && r.phase_elapsed_s > 0 && r.phase_done > 0)
      return `${(r.phase_done / r.phase_elapsed_s / 1048576).toFixed(1)} MB/s`;
    if (r.phase === "upscaling" && r.fps) return `${r.fps} fps`;
    if (r.phase_unit === "frames" && r.phase_elapsed_s > 2 && r.phase_done > 0)
      return `${(r.phase_done / r.phase_elapsed_s).toFixed(1)} fps`;
    if (r.phase_unit === "chunks" && r.phase_total)
      return `${r.phase_done}/${r.phase_total}`;
    return "…";
  }

  onMount(async () => {
    await Promise.all([refresh(), loadBook()]);
    timer = setInterval(refresh, 3000);
  });
  onDestroy(() => { clearInterval(timer); clearTimeout(noticeTimer); });
</script>

<svelte:window onkeydown={(e) => { if (e.key === "Escape") { showImport = false; showMachine = false; showStart = false; } }} />
<svelte:head><title>upscale</title></svelte:head>

<nav class="topbar">
  <span class="brand">upscale</span>
  <span class="counts">
    {#if counts.running}<b class="c-run">{counts.running} running</b>{/if}
    <span>{counts.queued ?? 0} queued</span>
    <span class="muted">{counts.done ?? 0} done</span>
  </span>
  <span class="spacer"></span>

  {#each devices as h}
    <span class="chip" class:down={h.reachable === false} title={h.error || ""}>
      {h.name}<em>{h.reachable === null ? "idle" : h.reachable ? (h.phase || h.state || "idle") : "unreachable"}</em>
      {#if h.queue_running}
        <span class="qtag" class:stopping={h.queue_stopping} title={h.queue_note || ""}>
          {h.queue_stopping ? "stopping" : "queue"}
        </span>
      {/if}
    </span>
  {/each}

  <button class="tb" onclick={openStart} disabled={running || !!busy}>▶ Start</button>
  <button class="tb" onclick={() => act("POST", "/api/stop", {}, "stop")}
          disabled={!running || stopping || !!busy}
          title="Stop after the current file. Finished chunks are kept.">■ Stop</button>
  <button class="tb" onclick={() => { showMachine = true; probe = null; loadBook(); }}>⚙ Machines</button>
  <button class="add" onclick={() => { showImport = true; picked = new Set(); browse(); }} title="Pick the source directory">+</button>
</nav>

{#if error}<p class="bar err">{error}</p>{/if}
{#if notice}<button class="bar notice" onclick={() => say("")}>{notice}</button>{/if}

<table>
  <thead><tr>
    {#each [["n","#","num"],["name","Episode",""],["library_name","Library","lib"],
            ["device","Device","dev"],["status","Status","st"],["progress","Progress","pr"],
            ["rate","Rate","rt"],["eta","ETA","et"]] as [k, label, cls]}
      <th class={cls}>
        <button class="sorth" onclick={() => sortBy(k)}>
          {label}{#if sortKey === k}<span class="arrow">{sortDir > 0 ? "▲" : "▼"}</span>{/if}
        </button>
      </th>
    {/each}
  </tr></thead>
  <tbody>
    {#each sorted as r}
      <tr class={r.status}>
        <td class="num">{r.n ?? "–"}</td>
        <td class="name" title={r.target_dir ? `${r.path}\n→ ${r.target_dir}` : r.path}>
          <span class="fname">{r.name}</span>
          <span class="fpath">{dir(r.path)}</span>
          {#if r.target_dir && r.status !== "done"}<span class="fdest">→ {r.target_dir}</span>{/if}
        </td>
        <td class="lib">{r.library_name}</td>
        <td class="dev">{#if r.device}<span class="devtag">{r.device}</span>{:else}<span class="muted">—</span>{/if}</td>
        <td class="st"><span class="pill {r.status}">{r.status === "running" && r.phase ? r.phase : r.status}</span></td>
        <td class="pr">
          {#if r.status === "running" || r.status === "paused"}
            {#if (r.phase_percent ?? -1) >= 0}
              <div class="bar2"><div class="fill" style="width:{r.phase_percent}%"></div></div>
              <span class="pct">{r.phase_percent}%</span>
            {:else}
              <div class="bar2"><div class="fill indet"></div></div>
              <span class="pct">–</span>
            {/if}
          {:else if r.status === "done"}<span class="muted">✓</span>
          {:else}<span class="muted">{gb(r.size)}</span>{/if}
        </td>
        <td class="rt">{phaseRate(r)}</td>
        <td class="et">{r.phase_eta_s ? hhmm(r.phase_eta_s) : (r.eta_s ? hhmm(r.eta_s) : (r.status === "running" ? "…" : ""))}</td>
      </tr>
    {:else}
      <tr><td colspan="8" class="muted pad">no source directory — press + to pick one</td></tr>
    {/each}
  </tbody>
</table>

{#if showImport}
  <button class="scrim" aria-label="Close" onclick={() => (showImport = false)}></button>
  <div class="modal wide">
    <h2>Source directory</h2>
    <div class="row">
      <button class="ghost" onclick={up} title="Up one level">↑</button>
      <input class="grow" bind:value={query} oninput={onQuery} use:focusOnMount
             placeholder="/mnt/media/tv/… — a path, or a name to filter" />
    </div>
    <ul class="ac">
      {#each listing as it}
        <li>
          {#if it.kind === "dir"}
            <button class="acrow" onclick={() => into(it)}>
              <span class="ic">📁</span><span class="acname">{it.name}</span>
              <span class="muted small">{it.files ? `${it.files} file${it.files === 1 ? "" : "s"}` : `${it.dirs} folder${it.dirs === 1 ? "" : "s"}`}</span>
            </button>
          {:else}
            <button class="acrow" onclick={() => { const s = new Set(picked); s.has(it.path) ? s.delete(it.path) : s.add(it.path); picked = s; }}>
              <span class="ic">{picked.has(it.path) ? "☑" : "☐"}</span>
              <span class="acname">{it.name}</span>
              <span class="muted small">{gb(it.size)}</span>
            </button>
          {/if}
        </li>
      {:else}
        <li class="muted pad small">nothing here</li>
      {/each}
    </ul>
    <div class="row gap">
      <button class="primary" onclick={() => { picked = new Set([base || query.replace(/\/+$/, "")]); doImport(); }} disabled={!!busy}>
        Use this directory
      </button>
      <button class="ghost" onclick={doImport} disabled={!picked.size || !!busy}>
        Use the {picked.size || ""} picked file{picked.size === 1 ? "" : "s"}' directory
      </button>
      <button class="ghost" onclick={() => (showImport = false)}>Cancel</button>
    </div>
  </div>
{/if}

{#if showMachine}
  <button class="scrim" aria-label="Close" onclick={() => (showMachine = false)}></button>
  <div class="modal">
    <h2>Machines</h2>
    {#each book as m}
      <div class="row hostrow">
        <span class="grow">{m.name} <span class="muted small mono">{m.ssh}</span></span>
        <button class="ghost danger" onclick={() => removeMachine(m.name)} disabled={!!busy}>Remove</button>
      </div>
    {:else}
      <p class="muted small">none yet</p>
    {/each}
    <hr />
    <div class="form">
      <label>SSH target
        <input bind:value={mSsh} use:focusOnMount placeholder="desktop · user@host · -p 48726 root@1.2.3.4" />
      </label>
      <label>Name
        <input bind:value={mName} placeholder="desktop" />
      </label>
      <label>Scratch
        <input bind:value={mScratch} placeholder="/home/kirill/upscale-scratch" />
      </label>
      <label>Workers
        <input bind:value={mWorkers} placeholder="8" />
      </label>
    </div>
    <div class="row gap">
      <button class="ghost" onclick={doProbe} disabled={!mSsh || probing}>{probing ? "probing…" : "Test connection"}</button>
      <button class="primary" onclick={addMachine} disabled={!mSsh || !mName || !!busy}>Add machine</button>
    </div>
    {#if probe}
      <div class="probe">
        {#if probe.reachable}
          <p><b>{probe.host}</b> · {probe.cores} cores · {probe.gpu || "no GPU detected"} · {probe.free_gb} GB free</p>
          {#each probe.warnings as w}<p class="err small">{w}</p>{/each}
        {:else}<p class="err small">{probe.error || "unreachable"}</p>{/if}
      </div>
    {/if}
  </div>
{/if}

{#if showStart}
  <button class="scrim" aria-label="Close" onclick={() => (showStart = false)}></button>
  <div class="modal wide">
    <h2>Start</h2>
    <div class="form">
      <label>Source
        <input bind:value={sSource} placeholder="/mnt/media/tv/show" />
      </label>
      <label>Target
        <input bind:value={sTarget} placeholder="/mnt/media/tv-4k/show" />
      </label>
      <label>Archive
        <input bind:value={sArchive} disabled={sDelete} placeholder="/mnt/media/archive/show" />
      </label>
      <label>Scratch
        <input bind:value={sScratch} placeholder="leave empty for the device default" />
      </label>
      <label>Workers
        <input bind:value={sWorkers} placeholder="leave empty for the device default" />
      </label>
    </div>
    <label class="check">
      <input type="checkbox" bind:checked={sDelete} />
      Delete each source once it is delivered, instead of archiving it
    </label>
    <div class="devpick">
      {#each book as m}
        <button class="pickdev" class:on={sDevices.has(m.name)} onclick={() => toggleDevice(m.name)}>
          {sDevices.has(m.name) ? "☑" : "☐"} {m.name}
        </button>
      {:else}
        <span class="muted small">no machines — add one under ⚙ Machines</span>
      {/each}
    </div>
    <div class="row gap">
      <button class="primary" onclick={start} disabled={!sDevices.size || !!busy}>Start</button>
      <button class="ghost" onclick={preview} disabled={!sDevices.size || !!busy}>Show the command</button>
      <button class="ghost" onclick={() => (showStart = false)}>Cancel</button>
    </div>
  </div>
{/if}

<style>
  :global(body) { margin: 0; background: #0b0d12; color: #e6e6e6;
    font: 14px/1.45 ui-sans-serif, system-ui, sans-serif; }
  .topbar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: .6rem;
    padding: .55rem .9rem; background: #12151d; border-bottom: 1px solid #232838; flex-wrap: wrap; }
  .brand { font-weight: 700; }
  .counts { display: flex; gap: .55rem; font-size: .8rem; align-items: center; }
  .c-run { color: #6ee7a0; } .c-dev { color: #7ab6f5; } .c-held { color: #f5d76e; } .c-miss { color: #f8899f; }
  .spacer { flex: 1; }
  .chip { display: inline-flex; gap: .4rem; align-items: center; font-size: .78rem;
    background: #171b26; border: 1px solid #232838; border-radius: 999px; padding: .2rem .6rem; }
  .chip em { font-style: normal; color: #7ab6f5; }
  .chip.down em { color: #f8899f; }
  .tb { background: #1b2030; color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .3rem .6rem; font: inherit; font-size: .8rem; cursor: pointer; }
  .tb:hover:not(:disabled) { background: #232838; }
  .tb:disabled, .selbar button:disabled { opacity: .4; cursor: not-allowed; }
  .tb.danger, .selbar .danger { border-color: #5a2233; color: #f8899f; }
  .add { width: 2rem; height: 2rem; border-radius: 8px; background: #1d4ed8; color: #fff;
    border: 0; font-size: 1.2rem; line-height: 1; cursor: pointer; }
  p.bar, button.bar { margin: 0; padding: .5rem .9rem; font-size: .85rem; display: block;
    width: 100%; text-align: left; border: 0; font-family: inherit; }
  .bar.err { background: #2a1220; color: #f8899f; }
  .bar.notice { background: #10233a; color: #7ab6f5; cursor: pointer; }
  .selbar { display: flex; align-items: center; gap: .6rem; padding: .45rem .9rem;
    background: #16203a; border-bottom: 1px solid #232838; font-size: .82rem; flex-wrap: wrap; }
  .selbar button { background: #232838; color: #e6e6e6; border: 1px solid #2b3244;
    border-radius: 6px; padding: .25rem .6rem; font: inherit; font-size: .8rem; cursor: pointer; }
  .ghost { background: transparent !important; }
  table { width: 100%; border-collapse: collapse; }
  .sorth { background: none; border: 0; color: inherit; font: inherit; padding: 0;
    cursor: pointer; text-transform: inherit; letter-spacing: inherit; }
  .sorth:hover { color: #e6e6e6; }
  .arrow { font-size: .6rem; margin-left: .25rem; }
  thead th { position: sticky; top: 2.9rem; background: #12151d; text-align: left; font-size: .7rem;
    text-transform: uppercase; letter-spacing: .07em; color: #8b93a7; padding: .45rem .6rem;
    border-bottom: 1px solid #232838; }
  tbody td { padding: .34rem .6rem; border-bottom: 1px solid #171b26; font-size: .84rem; }
  tbody tr:hover { background: #12151d; }
  tbody, thead { user-select: none; -webkit-user-select: none; }
  tbody tr.sel { background: #16203a; }
  tbody tr.done { color: #6b7280; }
  tbody tr:focus-visible { outline: 2px solid #3b82f6; outline-offset: -2px; }
  .num { width: 3rem; color: #8b93a7; text-align: right; font-variant-numeric: tabular-nums; }
  .name { max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fname, .fsrc, .fpath, .fdest { display: block; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }
  .fdest { font-family: ui-monospace, monospace; font-size: .7rem; color: #6b7280; }
  .fsrc { font-family: ui-monospace, monospace; font-size: .72rem; color: #7ab6f5;
    margin-top: .1rem; }
  .fpath { font-family: ui-monospace, monospace; font-size: .7rem; color: #6b7280; }
  .lib { width: 8rem; color: #8b93a7; }
  .dev { width: 10rem; }
  .st { width: 7.5rem; } .pr { width: 11rem; } .rt { width: 5rem; } .et { width: 5rem; }
  .rt, .et { color: #8b93a7; font-variant-numeric: tabular-nums; }
  .qtag { font-size: .68rem; background: #12351f; color: #6ee7a0;
    padding: .05rem .4rem; border-radius: 999px; }
  .qtag.stopping { background: #3a3212; color: #f5d76e; }
  .qtag.off { background: #232838; color: #8b93a7; }
  .devtag { font-size: .74rem; background: #17273f; color: #7ab6f5; padding: .1rem .45rem; border-radius: 999px; }
  .pill { font-size: .68rem; text-transform: uppercase; letter-spacing: .05em; padding: .1rem .4rem;
    border-radius: 999px; background: #232838; color: #aab; }
  .pill.running, .pill.upscaling { background: #12351f; color: #6ee7a0; }
  .pill.delivering, .pill.fetching, .pill.extracting, .pill.encoding, .pill.muxing,
  .pill.working { background: #17273f; color: #7ab6f5; }
  .pill.paused, .pill.held { background: #3a3212; color: #f5d76e; }
  .pill.delivering { background: #17273f; color: #7ab6f5; }
  .pill.missing { background: #3a1620; color: #f8899f; }
  .pill.done { background: #1a1f1a; color: #6b8a6b; }
  .bar2 { height: 6px; width: 7rem; background: #232838; border-radius: 3px; overflow: hidden;
    display: inline-block; vertical-align: middle; }
  .fill { height: 100%; background: linear-gradient(90deg, #3b82f6, #6ee7a0); transition: width .4s; }
  .fill.alt { background: linear-gradient(90deg, #7c5cff, #7ab6f5); }
  .pct { font-size: .75rem; color: #8b93a7; margin-left: .4rem; font-variant-numeric: tabular-nums; }
  .fill.indet { width: 40%; background: linear-gradient(90deg, #232838, #7ab6f5, #232838);
    animation: slide 1.4s ease-in-out infinite; }
  @keyframes slide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }
  .scrim { position: fixed; inset: 0; background: #000a; z-index: 9; border: 0; padding: 0; }
  .modal { position: fixed; z-index: 10; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: min(620px, 94vw); background: #12151d; border: 1px solid #2b3244; border-radius: 12px;
    padding: 1.1rem; box-shadow: 0 20px 60px #000a; }
  .modal.wide { width: min(860px, 96vw); }
  .modal h2 { margin: 0 0 .8rem; font-size: .8rem; text-transform: uppercase;
    letter-spacing: .08em; color: #8b93a7; }
  .row { display: flex; gap: .5rem; align-items: center; }
  .row.gap { margin-top: .8rem; flex-wrap: wrap; }
  .hostrow { padding: .3rem 0; border-bottom: 1px solid #1b2030; font-size: .85rem; }
  .grow { flex: 1; }
  .form { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: .7rem; }
  label { display: flex; flex-direction: column; gap: .25rem; font-size: .78rem; color: #8b93a7; }
  select, input { background: #0b0d12; color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .4rem .45rem; font: inherit; font-size: .84rem; }
  .ac { list-style: none; margin: .6rem 0 0; padding: 0; max-height: 52vh; overflow-y: auto;
    border: 1px solid #232838; border-radius: 8px; }
  .ac li + li { border-top: 1px solid #171b26; }
  .acrow { display: flex; align-items: center; gap: .55rem; width: 100%; text-align: left;
    background: transparent; border: 0; color: #e6e6e6; font: inherit; font-size: .85rem;
    padding: .4rem .6rem; cursor: pointer; }
  .acrow:hover { background: #16203a; }
  .acrow.dim { opacity: .5; cursor: default; }
  .acname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic { width: 1.1rem; text-align: center; }
  .probe { margin-top: .7rem; padding: .6rem; background: #0f131c; border: 1px solid #232838;
    border-radius: 8px; }
  .probe p { margin: .15rem 0; font-size: .85rem; }
  hr { border: 0; border-top: 1px solid #232838; margin: .8rem 0; }
  button.primary { background: #1d4ed8; color: #fff; border: 0; border-radius: 6px;
    padding: .45rem .9rem; font: inherit; font-size: .85rem; cursor: pointer; }
  button.primary:disabled { opacity: .45; cursor: not-allowed; }
  .modal button.ghost { color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .45rem .9rem; font: inherit; font-size: .85rem; cursor: pointer; }
  .check { display: flex; flex-direction: row; align-items: center; gap: .5rem;
    margin-top: .8rem; font-size: .82rem; color: #b9c0d0; }
  .check input { width: 1rem; height: 1rem; padding: 0; accent-color: #1d4ed8; }
  .devpick { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .8rem; }
  .pickdev { background: #171b26; color: #e6e6e6; border: 1px solid #2b3244;
    border-radius: 999px; padding: .25rem .7rem; font: inherit; font-size: .8rem;
    cursor: pointer; }
  .pickdev.on { border-color: #1d4ed8; color: #9dc0ff; }
  .modal .danger { border-color: #5a2233; color: #f8899f; }
  .muted { color: #8b93a7; } .small { font-size: .78rem; }
  .mono { font-family: ui-monospace, monospace; font-size: .76rem; }
  .pad { padding: 1.2rem .6rem; }

  @media (max-width: 760px) {
    thead { position: absolute; width: 1px; height: 1px; margin: -1px;
      overflow: hidden; clip-path: inset(50%); }
    table, tbody, tr, td { display: block; width: auto; }
    tbody tr { border: 1px solid #232838; border-radius: 10px;
      margin: .5rem .55rem; padding: .5rem .7rem; }
    tbody tr:focus-visible { outline-offset: 2px; }
    tbody td { padding: .1rem 0; border-bottom: 0; font-size: .85rem; }

    .name { max-width: none; white-space: normal; overflow: visible;
      font-weight: 600; margin-bottom: .15rem; }
    .fname, .fsrc, .fpath, .fdest { white-space: normal; overflow: visible; }
    .fsrc, .fpath, .fdest { font-weight: 400; overflow-wrap: anywhere; }

    .num, .lib, .dev, .st, .rt, .et {
      display: inline-flex; align-items: center; width: auto;
      text-align: left; margin: .1rem .5rem .1rem 0; }

    .pr { display: flex; align-items: center; gap: .4rem; margin-top: .2rem; }
    .bar2 { flex: 1; width: auto; }
    .pct { margin-left: 0; }

    .topbar { padding: .5rem .55rem; gap: .45rem; }
    .counts { flex-wrap: wrap; row-gap: .2rem; }
    .spacer { flex-basis: 100%; height: 0; }
    .tb, .selbar button, button.primary, .modal button.ghost { min-height: 2.25rem; }
    .add { width: 2.4rem; height: 2.4rem; }

    .modal, .modal.wide { width: calc(100vw - 1.2rem);
      max-height: min(88vh, 100dvh - 2rem); overflow-y: auto; padding: .9rem; }
    .form { grid-template-columns: 1fr; }
  }
</style>
