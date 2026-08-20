<script>
  import { onMount, onDestroy } from "svelte";

  let rows = $state([]);
  let hosts = $state([]);
  let counts = $state({});
  let error = $state("");
  let notice = $state("");
  let busy = $state("");

  let selected = $state(new Set());   // file paths
  let lastClicked = $state(null);

  let showImport = $state(false);
  let showMachine = $state(false);
  let showStart = $state(false);

  // import browser
  let query = $state("/mnt/media/tv/");
  let listing = $state([]);
  let base = $state("");
  let picked = $state(new Set());
  let searchTimer;

  // add-machine
  let mSsh = $state("");
  let mLabel = $state("");
  let probe = $state(null);
  let probing = $state(false);

  // start
  let sHost = $state("");
  let sScratch = $state("");

  let timer;

  const j = async (url, opts) => {
    const r = await fetch(url, opts);
    if (!r.ok && r.status >= 500) throw new Error(`${url} → ${r.status}`);
    return r.json();
  };

  async function refresh() {
    try {
      const d = await j("/api/queue");
      rows = d.rows || [];
      counts = d.counts || {};
      hosts = d.hosts || [];
      if (!sHost && hosts.length) { sHost = hosts[0].id; sScratch = hosts[0].default_scratch || ""; }
      error = "";
    } catch (e) { error = String(e); }
  }

  async function browse() {
    try {
      const d = await j(`/api/browse?q=${encodeURIComponent(query)}`);
      listing = d.results || []; base = d.base || "";
    } catch (e) { listing = []; }
  }
  function onQuery() { clearTimeout(searchTimer); searchTimer = setTimeout(browse, 130); }
  function into(dir) { query = dir.path + "/"; picked = new Set(); browse(); }
  function up() {
    const p = (base || query).replace(/\/+$/, "");
    query = p.slice(0, p.lastIndexOf("/") + 1) || "/";
    picked = new Set(); browse();
  }

  async function post(url, body, label) {
    busy = label;
    try {
      const d = await j(url, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      notice = d.ok === false ? `failed: ${d.error}` : (d.said || `${label} ok`);
      await refresh();
      return d;
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  async function doImport() {
    if (!picked.size) return;
    const d = await post("/api/import", { paths: [...picked] }, "import");
    if (d?.ok) { notice = `imported ${d.added} file${d.added > 1 ? "s" : ""}`; showImport = false; picked = new Set(); }
  }

  // Hold = "not this one, move on".
  //
  // On a QUEUED episode it drops out of the run order. On the RUNNING one the
  // machine stops it and takes the next unheld episode - it does not stall,
  // because stopping the whole queue is what the topbar Stop button is for.
  // Finished chunks stay in scratch, so releasing later resumes rather than
  // restarts.
  async function hold(on) {
    const want = on ? ["queued", "running"] : ["held", "paused"];
    const paths = sel.filter(r => want.includes(r.status)).map(r => r.path);
    if (!paths.length) {
      // Returning quietly here made Hold look broken: nothing moved, nothing
      // was said, and the only wrong thing was the selection.
      notice = `nothing to ${on ? "hold" : "release"} — selected: `
             + [...new Set(sel.map(r => r.status))].join(", ");
      return;
    }
    busy = on ? "hold" : "release";
    try {
      const d = await j("/api/hold", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths, hold: on }) });
      notice = d.ok
        ? `${on ? "held" : "released"} ${paths.length} episode${paths.length > 1 ? "s" : ""}`
          + (d.skipped?.length ? ` — ${d.skipped.join(", ")} moving to the next` : "")
        : `failed: ${d.error}`;
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  async function del() {
    if (!selected.size) return;
    if (!confirm(`Remove ${selected.size} from the queue?`)) return;
    await post("/api/remove", { paths: [...selected] }, "removed");
    selected = new Set();
  }

  async function start() {
    const paths = selected.size
      ? rows.filter(r => selected.has(r.path) && r.status === "queued").map(r => r.path)
      : rows.filter(r => r.status === "queued").map(r => r.path);
    if (!paths.length) { notice = "nothing queued to start"; return; }
    notice = `starting ${paths.length} on ${sHost}…`;
    // The endpoint runs `upscale start <device>` and returns what the command
    // printed. It does not return a count or a scratch path any more, and
    // inventing them here is what produced "started undefined ep in scratch".
    const d = await post("/api/start", { host: sHost }, "start");
    if (d?.ok) { notice = d.note || `started ${sHost}`; showStart = false; }
  }

  async function doProbe() {
    probing = true; probe = null;
    try {
      probe = await j("/api/hosts/probe", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ssh: mSsh }) });
    } catch (e) { probe = { ok: false, error: String(e) }; }
    finally { probing = false; }
  }
  async function addMachine() {
    const d = await post("/api/hosts/add", { ssh: mSsh, label: mLabel }, "add machine");
    if (d?.ok) { showMachine = false; mSsh = ""; mLabel = ""; probe = null; }
  }

  function rowClick(ev, i, r) {
    const s = new Set(selected);
    if (ev.shiftKey && lastClicked !== null) {
      const [a, b] = [Math.min(lastClicked, i), Math.max(lastClicked, i)];
      for (let k = a; k <= b; k++) s.add(sorted[k].path);
    } else if (ev.ctrlKey || ev.metaKey) {
      s.has(r.path) ? s.delete(r.path) : s.add(r.path);
      lastClicked = i;
    } else { s.clear(); s.add(r.path); lastClicked = i; }
    selected = s;
  }

  // Sorting is a view, applied over whatever the server last sent - the table
  // keeps refreshing every 3s underneath it, so it must not be a one-off sort
  // of the array.
  let sortKey = $state("n");
  let sortDir = $state(1);
  function sortBy(k) {
    if (sortKey === k) sortDir = -sortDir;
    else { sortKey = k; sortDir = k === "n" ? 1 : 1; }
  }
  const RANK = { running: 0, delivering: 1, paused: 2, queued: 3, held: 4, missing: 5, done: 6 };
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
    // Episode number as the tiebreak, so an equal column never shuffles rows
    // between refreshes.
    return (a.n ?? 0) - (b.n ?? 0);
  }));

  const sel = $derived(rows.filter(r => selected.has(r.path)));
  const canHold    = $derived(sel.some(r => r.status === "queued" || r.status === "running"));
  const canRelease = $derived(sel.some(r => r.status === "held" || r.status === "paused"));
  const runningHost = $derived(hosts.find(h => h.reachable && h.state !== "idle"));
  const selHost = $derived(hosts.find(h => h.id === sHost));
  const focusOnMount = (n) => n.focus();

  const hhmm = (s) => {
    if (!s || s < 0) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
  };
  const gb = (b) => b ? `${(b / 1073741824).toFixed(1)} GB` : "";

  // The rate of the CURRENT phase, in that phase's own unit. fps for anything
  // counting frames, MB/s for the two that move bytes, nothing for a phase with
  // nothing to count.
  function phaseRate(r) {
    if (!["running", "paused", "delivering"].includes(r.status)) return "";
    if (r.phase_unit === "bytes" && r.phase_elapsed_s > 0 && r.phase_done > 0)
      return `${(r.phase_done / r.phase_elapsed_s / 1048576).toFixed(1)} MB/s`;
    if (r.phase === "upscaling" && r.fps) return `${r.fps} fps`;
    if (r.phase_unit === "frames" && r.phase_elapsed_s > 2 && r.phase_done > 0)
      return `${(r.phase_done / r.phase_elapsed_s).toFixed(1)} fps`;
    if (r.phase_unit === "chunks" && r.phase_total)
      return `${r.phase_done}/${r.phase_total}`;
    return "…";
  }

  onMount(async () => { await refresh(); timer = setInterval(refresh, 3000); });
  onDestroy(() => clearInterval(timer));
</script>

<svelte:window onkeydown={(e) => { if (e.key === "Escape") { showImport = false; showMachine = false; showStart = false; } }} />
<svelte:head><title>upscale</title></svelte:head>

<nav class="topbar">
  <span class="brand">upscale</span>
  <span class="counts">
    {#if counts.running}<b class="c-run">{counts.running} running</b>{/if}
    {#if counts.delivering}<span class="c-dev">{counts.delivering} delivering</span>{/if}
    <span>{counts.queued ?? 0} queued</span>
    {#if counts.held}<span class="c-held">{counts.held} held</span>{/if}
    {#if counts.missing}<span class="c-miss">{counts.missing} missing</span>{/if}
    <span class="muted">{counts.done ?? 0} done</span>
  </span>
  <span class="spacer"></span>

  {#each hosts as h}
    <span class="chip" class:down={!h.reachable} title={h.error || h.work || ""}>
      {h.label}<em>{h.reachable ? (h.phase && h.phase !== "idle" ? h.phase : h.state) : "unreachable"}</em>
      {#if h.queue_running}
        <span class="qtag" class:stopping={h.queue_stopping} title={h.queue_note || ""}>
          {h.queue_stopping ? "stopping" : "queue"}
        </span>
      {:else}
        <span class="qtag off" title={h.queue_note || ""}>stopped</span>
      {/if}
    </span>
  {/each}

  <button class="tb" onclick={() => (showStart = true)} disabled={!hosts.length || !!busy}>▶ Start</button>
  <button class="tb" onclick={() => post("/api/stop", { host: (hosts.find(h => h.queue_running) || runningHost || hosts[0])?.id }, "stop")}
          disabled={!hosts.length || !!busy}
          title="Stop everything now. Finished chunks are kept; Start resumes from there.">■ Stop</button>
  <button class="tb" onclick={() => { showMachine = true; probe = null; }} title="Onboard a GPU machine">⚙ Machines</button>
  <button class="add" onclick={() => { showImport = true; picked = new Set(); browse(); }} title="Import episodes">+</button>
</nav>

{#if selected.size}
  <div class="selbar">
    <span>{selected.size} selected</span>
    <button onclick={() => hold(true)} disabled={!canHold || !!busy}>❚❚ Hold</button>
    <button onclick={() => hold(false)} disabled={!canRelease || !!busy}>▶ Release</button>
    <button class="danger" onclick={del} disabled={!!busy}>🗑 Delete</button>
    <button class="ghost" onclick={() => (selected = new Set())}>Clear</button>
  </div>
{/if}

{#if error}<p class="bar err">{error}</p>{/if}
{#if notice}<button class="bar notice" onclick={() => (notice = "")}>{notice}</button>{/if}

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
    {#each sorted as r, i}
      <tr class={r.status} class:sel={selected.has(r.path)} tabindex="0" role="button"
          onclick={(e) => rowClick(e, i, r)}
          onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); rowClick(e, i, r); } }}>
        <td class="num">{r.n ?? "–"}</td>
        <td class="name" title={r.path}>{r.name}</td>
        <td class="lib">{r.library_name}</td>
        <!-- Which GPU machine this episode is on. Blank when nothing is running
             it: guessing a device for queued work would be a promise no
             scheduler here has made. -->
        <td class="dev">{#if r.device}<span class="devtag">{r.device}</span>{:else}<span class="muted">—</span>{/if}</td>
        <td class="st"><span class="pill {r.status}">{r.status === "running" && r.phase ? r.phase : r.status}</span></td>
        <td class="pr">
          {#if r.status === "running" || r.status === "paused" || r.status === "delivering"}
            <!-- THE PHASE'S OWN BAR. Each phase measures a different thing, so
                 an episode-level number cannot represent any of them - which is
                 how a 39% episode sat at 39% through minutes of verifying and
                 read as stuck. A phase with nothing to count says so instead of
                 showing a zero. -->
            {#if (r.phase_percent ?? -1) >= 0}
              <div class="bar2"><div class="fill" style="width:{r.phase_percent}%"></div></div>
              <span class="pct">{r.phase_percent}%</span>
            {:else}
              <div class="bar2"><div class="fill indet"></div></div>
              <span class="pct">–</span>
            {/if}
          {:else if r.status === "done"}<span class="muted">✓</span>
          {:else if r.status === "missing"}<span class="c-miss small">source moved</span>
          {:else}<span class="muted">{gb(r.size)}</span>{/if}
        </td>
        <td class="rt">{phaseRate(r)}</td>
        <td class="et">{r.phase_eta_s ? hhmm(r.phase_eta_s) : (r.eta_s ? hhmm(r.eta_s) : (r.status === "running" ? "…" : ""))}</td>
      </tr>
    {:else}
      <tr><td colspan="8" class="muted pad">nothing imported — press + to pick episodes</td></tr>
    {/each}
  </tbody>
</table>

{#if showImport}
  <button class="scrim" aria-label="Close" onclick={() => (showImport = false)}></button>
  <div class="modal wide">
    <h2>Import episodes</h2>
    <div class="row">
      <button class="ghost" onclick={up} title="Up one level">↑</button>
      <input class="grow" bind:value={query} oninput={onQuery} use:focusOnMount
             placeholder="/mnt/media/tv/… — a path, or a name to search the roots" />
    </div>
    <ul class="ac">
      {#each listing as it}
        <li>
          {#if it.kind === "dir"}
            <button class="acrow" onclick={() => into(it)}>
              <span class="ic">📁</span><span class="acname">{it.name}</span>
              <span class="muted small">{it.files} video{it.files === 1 ? "" : "s"}</span>
            </button>
          {:else}
            <button class="acrow" class:dim={it.imported} disabled={it.imported}
                    onclick={() => { const s = new Set(picked); s.has(it.path) ? s.delete(it.path) : s.add(it.path); picked = s; }}>
              <span class="ic">{picked.has(it.path) ? "☑" : it.imported ? "•" : "☐"}</span>
              <span class="acname">{it.name}</span>
              <span class="muted small">{gb(it.size)}</span>
              {#if it.imported}<span class="pill done">in queue</span>{/if}
            </button>
          {/if}
        </li>
      {:else}
        <li class="muted pad small">nothing here</li>
      {/each}
    </ul>
    <div class="row gap">
      <button class="primary" onclick={doImport} disabled={!picked.size || !!busy}>
        Import {picked.size || ""} file{picked.size === 1 ? "" : "s"}
      </button>
      <button class="ghost" onclick={() => { picked = new Set(listing.filter(x => x.kind === "file" && !x.imported).map(x => x.path)); }}>
        Select all in folder
      </button>
      <button class="ghost" onclick={() => (showImport = false)}>Cancel</button>
    </div>
  </div>
{/if}

{#if showMachine}
  <button class="scrim" aria-label="Close" onclick={() => (showMachine = false)}></button>
  <div class="modal">
    <h2>Machines</h2>
    {#each hosts as h}
      <div class="row hostrow">
        <span class="grow">{h.label} <span class="muted small">{h.reachable ? h.work : h.error}</span></span>
        <button class="ghost" onclick={() => post("/api/hosts/remove", { id: h.id }, "removed host")}>Remove</button>
      </div>
    {/each}
    <hr />
    <div class="form">
      <label>SSH target
        <input bind:value={mSsh} use:focusOnMount placeholder="desktop · user@host · -p 48726 root@1.2.3.4" />
      </label>
      <label>Label (optional)
        <input bind:value={mLabel} placeholder="desktop (RX 5700 XT)" />
      </label>
    </div>
    <div class="row gap">
      <button class="ghost" onclick={doProbe} disabled={!mSsh || probing}>{probing ? "probing…" : "Test connection"}</button>
      <button class="primary" onclick={addMachine} disabled={!mSsh || !probe?.ok || !!busy}>Add machine</button>
    </div>
    {#if probe}
      <div class="probe">
        {#if probe.ok}
          <p><b>{probe.host}</b> · {probe.cpus} CPUs · {probe.gpu || "no GPU detected"}</p>
          <p class="muted small">worker: <span class="mono">{probe.worker || "NOT FOUND"}</span></p>
          <p class="muted small">scratch: {Object.entries(probe.scratch || {}).map(([k, v]) => `${k} (${v.free_gb} GB free)`).join(" · ") || "none found"}</p>
          {#if !probe.ready}<p class="err small">{probe.note}</p>{/if}
        {:else}<p class="err small">{probe.error}</p>{/if}
      </div>
    {/if}
  </div>
{/if}

{#if showStart}
  <button class="scrim" aria-label="Close" onclick={() => (showStart = false)}></button>
  <div class="modal">
    <h2>Start</h2>
    <div class="form">
      <label>Machine
        <select bind:value={sHost} onchange={() => { sScratch = selHost?.default_scratch || ""; }}>
          {#each hosts as h}<option value={h.id}>{h.label}</option>{/each}
        </select>
      </label>
      <label>Scratch
        <select bind:value={sScratch}>
          {#each Object.entries(selHost?.scratch || {}) as [k, p]}<option value={k}>{k} — {p}</option>{/each}
        </select>
      </label>
    </div>
    <p class="muted small">
      {selected.size ? sel.filter(r => r.status === "queued").length : (counts.queued ?? 0)} episodes
    </p>
    <div class="row gap">
      <button class="primary" onclick={start} disabled={!sHost || !!busy}>Start</button>
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
  /* Shift-click is a range selection here, not a text selection - without this
     the browser paints the run of rows blue as if it were a paragraph. */
  tbody, thead { user-select: none; -webkit-user-select: none; }
  tbody tr.sel { background: #16203a; }
  tbody tr.done { color: #6b7280; }
  tbody tr:focus-visible { outline: 2px solid #3b82f6; outline-offset: -2px; }
  .num { width: 3rem; color: #8b93a7; text-align: right; font-variant-numeric: tabular-nums; }
  .name { max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
  .muted { color: #8b93a7; } .small { font-size: .78rem; }
  .mono { font-family: ui-monospace, monospace; font-size: .76rem; }
  .pad { padding: 1.2rem .6rem; }

  /* ---------------------------------------------------------------- phone ---
     One column, and the table stops being a table. Fixed widths (.dev 10rem,
     .pr 11rem, ...) add up to far more than a phone is wide, so the page used
     to scroll sideways with the episode name clipped to nothing.

     Rows become cards: the name is the heading, the small facts wrap
     underneath it, and the progress bar takes the full width. thead goes to a
     screen-reader-only box rather than display:none so sorting stays reachable
     for anything that reads the header row. */
  @media (max-width: 760px) {
    thead { position: absolute; width: 1px; height: 1px; margin: -1px;
      overflow: hidden; clip-path: inset(50%); }
    table, tbody, tr, td { display: block; width: auto; }
    tbody tr { border: 1px solid #232838; border-radius: 10px;
      margin: .5rem .55rem; padding: .5rem .7rem; }
    tbody tr:focus-visible { outline-offset: 2px; }
    tbody td { padding: .1rem 0; border-bottom: 0; font-size: .85rem; }

    /* the episode name is the card's heading, so let it wrap in full */
    .name { max-width: none; white-space: normal; overflow: visible;
      font-weight: 600; margin-bottom: .15rem; }

    /* the small facts flow inline under it instead of each owning a column */
    .num, .lib, .dev, .st, .rt, .et {
      display: inline-flex; align-items: center; width: auto;
      text-align: left; margin: .1rem .5rem .1rem 0; }

    .pr { display: flex; align-items: center; gap: .4rem; margin-top: .2rem; }
    .bar2 { flex: 1; width: auto; }
    .pct { margin-left: 0; }

    /* comfortable touch targets, and a top bar that stacks instead of clipping */
    .topbar { padding: .5rem .55rem; gap: .45rem; }
    .counts { flex-wrap: wrap; row-gap: .2rem; }
    .spacer { flex-basis: 100%; height: 0; }
    .tb, .selbar button, button.primary, .modal button.ghost { min-height: 2.25rem; }
    .add { width: 2.4rem; height: 2.4rem; }

    /* a modal centred on a short screen has to be able to scroll */
    .modal, .modal.wide { width: calc(100vw - 1.2rem);
      max-height: min(88vh, 100dvh - 2rem); overflow-y: auto; padding: .9rem; }
    .form { grid-template-columns: 1fr; }
  }
</style>
