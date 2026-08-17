<script>
  import { onMount, onDestroy } from "svelte";

  let libraries = $state([]);
  let hosts = $state([]);
  let rows = $state([]);
  let counts = $state({});
  let runRange = $state("");
  let error = $state("");
  let notice = $state("");
  let busy = $state("");

  let libs = $state([]);              // imported libraries
  let selected = $state(new Set());   // keys: "<library>#<episode number>"
  let lastClicked = $state(null);
  let showStart = $state(false);
  let showImport = $state(false);

  // import autocomplete
  let query = $state("");
  let results = $state([]);
  let hi = $state(0);
  let searchTimer;

  // start-modal fields
  let mLib = $state("");
  let mHost = $state("");
  let mScratch = $state("");
  let mRange = $state("");

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
      libs = d.libraries || [];
      if (!mHost && hosts.length) {
        mHost = hosts[0].id;
        mScratch = hosts[0].default_scratch || "";
      }
      error = libs.find((l) => l.error)?.error || "";
    } catch (e) { error = String(e); }
  }

  async function search() {
    try {
      const d = await j(`/api/browse?q=${encodeURIComponent(query)}`);
      results = d.results || []; hi = 0;
    } catch (e) { results = []; }
  }
  function onQuery() { clearTimeout(searchTimer); searchTimer = setTimeout(search, 120); }

  async function importLib(path) {
    busy = "import";
    try {
      const d = await j("/api/import", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
      notice = d.ok ? `imported ${path.split("/").pop()}` : `failed: ${d.error}`;
      if (d.ok) { showImport = false; query = ""; results = []; }
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  async function unimport(path) {
    if (!confirm(`Remove ${path.split("/").pop()} from the queue?\n\nNothing on disk is touched.`)) return;
    busy = "unimport";
    try {
      await j("/api/unimport", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) });
      selected = new Set();
      await refresh();
    } finally { busy = ""; }
  }

  // Selected rows grouped by library: every mutation is per-library, because
  // an episode NUMBER only means something inside one show.
  function byLibrary() {
    const m = new Map();
    for (const r of rows) {
      if (!selected.has(key(r)) || r.n === null) continue;
      if (!m.has(r.library)) m.set(r.library, []);
      m.get(r.library).push(r.n);
    }
    return m;
  }

  async function mutate(endpoint, extra, label) {
    const groups = byLibrary();
    if (!groups.size) return;
    busy = label;
    try {
      let n = 0;
      for (const [library, episodes] of groups) {
        const d = await j(endpoint, { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ library, episodes, ...extra }) });
        if (!d.ok) { notice = `failed: ${d.error}`; break; }
        n += episodes.length;
      }
      if (!notice.startsWith("failed")) notice = `${label} ${n} episode${n > 1 ? "s" : ""}`;
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  const hold    = (on) => mutate("/api/hold", { hold: on }, on ? "held" : "released");

  async function abort() {
    const h = runningHost;
    if (!h) return;
    if (!confirm(`Abort ${h.label} now?\n\nThe episode in flight is discarded. Finished chunks stay in scratch and a later run skips them, and delivery is atomic, so nothing half-written reaches the library.`)) return;
    busy = "abort";
    try {
      const d = await j("/api/abort", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ host: h.id }) });
      notice = d.ok ? `aborted ${h.label}` : `failed: ${d.error}`;
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }
  const remove  = () => {
    const n = byLibrary().size ? [...byLibrary().values()].flat().length : 0;
    if (!n) return;
    if (!confirm(`Remove ${n} episode${n > 1 ? "s" : ""} from the queue?\n\nThe source files are NOT deleted — they stay exactly where they are.`)) return;
    return mutate("/api/remove", { remove: true }, "removed");
  };

  async function act(id, action) {
    busy = action;
    try {
      const d = await j(`/api/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: id }),
      });
      notice = d.ok ? (d.said || `${action} sent`) : `failed: ${d.error}`;
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  async function start() {
    busy = "start";
    try {
      const d = await j("/api/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: mHost, library: mLib, scratch: mScratch,
                               range: mRange.trim() || undefined }),
      });
      notice = d.ok ? `started on ${d.host} — scratch ${d.work}` : `refused: ${d.error}`;
      if (d.ok) showStart = false;
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  // qBit-style: click selects, shift-click extends from the last click.
  function rowClick(ev, i, r) {
    if (r.status === "done") return;
    const s = new Set(selected);
    if (ev.shiftKey && lastClicked !== null) {
      const [a, b] = [Math.min(lastClicked, i), Math.max(lastClicked, i)];
      for (let k = a; k <= b; k++) if (rows[k].status !== "done") s.add(key(rows[k]));
    } else if (ev.ctrlKey || ev.metaKey) {
      s.has(key(r)) ? s.delete(key(r)) : s.add(key(r));
      lastClicked = i;
    } else {
      s.clear(); s.add(key(r)); lastClicked = i;
    }
    selected = s;
  }

  // autofocus the search when the import modal opens. An `autofocus` attribute
  // warns (it is wrong on a page), but inside a modal that just opened it is
  // exactly right — the user pressed + to type a name.
  const focusOnMount = (node) => { node.focus(); };

  const key = (r) => `${r.library}#${r.n}`;
  const selectedRows = $derived(rows.filter((r) => selected.has(key(r))));
  const canHold    = $derived(selectedRows.some((r) => r.status === "queued"));
  const canRelease = $derived(selectedRows.some((r) => r.status === "held"));
  const canRemove  = $derived(selectedRows.some((r) => r.status !== "running"));
  const selHost = $derived(hosts.find((h) => h.id === mHost));
  const runningHost = $derived(hosts.find((h) => h.reachable && h.state !== "idle"));

  const hhmm = (s) => {
    if (!s || s < 0) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  };

  onMount(async () => {
    await refresh();
    timer = setInterval(refresh, 3000);
  });
  onDestroy(() => clearInterval(timer));
</script>

<svelte:window onkeydown={(e) => { if (e.key === "Escape") showStart = false; }} />

<svelte:head><title>upscale</title></svelte:head>

<nav class="topbar">
  <span class="brand">upscale</span>

  <span class="counts">
    {#if counts.running}<b class="c-running">{counts.running} running</b>{/if}
    <span>{counts.queued ?? 0} queued</span>
    {#if counts.held}<span class="c-held">{counts.held} held</span>{/if}
    <span class="muted">{counts.done ?? 0} done</span>
  </span>

  <span class="spacer"></span>

  {#each hosts as h}
    <span class="hostchip" class:down={!h.reachable} title={h.error || h.work || ""}>
      {h.label}
      <em>{h.reachable ? (h.phase && h.phase !== "idle" ? h.phase : h.state) : "unreachable"}</em>
    </span>
  {/each}

  <button class="tb" onclick={() => { mRange = ""; mLib = libs[0]?.path || ""; showStart = true; }}
          disabled={!libs.length}>▶ Start</button>
  <button class="tb" onclick={() => act(runningHost?.id, "stop")} disabled={!runningHost || !!busy}
          title="Finish the current episode, then stop">■ Stop</button>
  <button class="tb danger" onclick={abort} disabled={!runningHost || !!busy}
          title="Kill the current episode now">✕ Abort</button>
  <button class="add" onclick={() => { showImport = true; query = ""; search(); }} title="Import a library">+</button>
</nav>

{#if error}<p class="err bar">{error}</p>{/if}
{#if notice}<button class="notice bar" onclick={() => (notice = "")}>{notice}</button>{/if}

{#if libs.length}
  <div class="libbar">
    {#each libs as l}
      <span class="libchip">
        {l.name}
        <span class="muted small">{l.counts?.queued ?? 0} queued</span>
        <button title="Remove from queue" onclick={() => unimport(l.path)} disabled={!!busy}>×</button>
      </span>
    {/each}
  </div>
{/if}

{#if selected.size}
  <div class="selbar">
    <span>{selected.size} selected</span>
    <button onclick={() => hold(true)} disabled={!canHold || !!busy}>❚❚ Hold</button>
    <button onclick={() => hold(false)} disabled={!canRelease || !!busy}>▶ Release</button>
    <button class="danger" onclick={remove} disabled={!canRemove || !!busy}>🗑 Delete</button>
    <button class="ghost" onclick={() => (selected = new Set())}>Clear</button>
    <span class="muted small">shift-click for a range · ctrl-click to add · delete removes from the queue, never from disk</span>
  </div>
{/if}

<table>
  <thead>
    <tr>
      <th class="num">#</th>
      <th>Episode</th>
      <th class="st">Status</th>
      <th class="pr">Progress</th>
      <th class="rt">Rate</th>
      <th class="et">ETA</th>
    </tr>
  </thead>
  <tbody>
    {#each rows as r, i}
      <tr class="{r.status}" class:sel={selected.has(r.n)} tabindex="0" role="button"
          onclick={(e) => rowClick(e, i, r)}
          onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); rowClick(e, i, r); } }}>
        <td class="num">{r.n ?? "–"}</td>
        <td class="name" title="{r.library_name} · {r.name}">{#if libs.length > 1}<span class="libtag">{r.library_name}</span>{/if}{r.name}</td>
        <td class="st"><span class="pill {r.status}">{r.status === "running" && r.phase ? r.phase : r.status}</span></td>
        <td class="pr">
          {#if r.status === "running" || r.status === "paused"}
            {#if r.percent !== undefined && r.percent > 0}
              <div class="bar"><div class="fill" style="width:{r.percent}%"></div></div>
              <span class="pct">{r.percent}%</span>
            {:else}
              <span class="muted small">{r.phase || "working"}</span>
            {/if}
          {:else if r.status === "done"}<span class="muted">✓</span>
          {:else}<span class="muted">—</span>{/if}
        </td>
        <td class="rt">{r.fps ? `${r.fps} fps` : ""}</td>
        <td class="et">{r.eta_s ? hhmm(r.eta_s) : ""}</td>
      </tr>
    {:else}
      <tr><td colspan="6" class="muted pad">{libs.length ? "nothing outstanding in the imported libraries" : "no libraries imported — press + to add one"}</td></tr>
    {/each}
  </tbody>
</table>

{#if showImport}
  <button class="scrim" aria-label="Close" onclick={() => (showImport = false)}></button>
  <div class="modal">
    <h2>Import a library</h2>
    <input class="search" bind:value={query} oninput={onQuery} placeholder="type to filter — bleach, gin, naruto…"
           use:focusOnMount
           onkeydown={(e) => {
             if (e.key === "ArrowDown") { hi = Math.min(hi + 1, results.length - 1); e.preventDefault(); }
             else if (e.key === "ArrowUp") { hi = Math.max(hi - 1, 0); e.preventDefault(); }
             else if (e.key === "Enter" && results[hi] && !results[hi].imported) importLib(results[hi].path);
           }} />
    <ul class="ac">
      {#each results as r, i}
        <li>
          <button class="acrow" class:hi={i === hi} class:dim={r.imported}
                  onmouseenter={() => (hi = i)}
                  onclick={() => !r.imported && importLib(r.path)} disabled={r.imported || !!busy}>
            <span class="acname">{r.name}</span>
            <span class="muted small">{r.files} files</span>
            {#if r.imported}<span class="pill done">imported</span>{/if}
          </button>
        </li>
      {:else}
        <li class="muted pad small">nothing under the media root matches</li>
      {/each}
    </ul>
    <p class="muted small">Importing only adds it to this queue. Nothing is copied, moved or upscaled until you press Start.</p>
  </div>
{/if}

{#if showStart}
  <button class="scrim" aria-label="Close" onclick={() => (showStart = false)}></button>
  <div class="modal">
    <h2>Start a run</h2>
    <div class="form">
      <label>Library
        <select bind:value={mLib}>
          {#each libs as l}<option value={l.path}>{l.name}</option>{/each}
        </select>
      </label>
      <label>Destination host
        <select bind:value={mHost} onchange={() => { mScratch = selHost?.default_scratch || ""; }}>
          {#each hosts as h}<option value={h.id}>{h.label}</option>{/each}
        </select>
      </label>
      <label>Scratch
        <select bind:value={mScratch}>
          {#each Object.entries(selHost?.scratch || {}) as [k, p]}<option value={k}>{k} — {p}</option>{/each}
        </select>
      </label>
      <label>Episodes
        <input bind:value={mRange} placeholder={runRange ? "everything not held" : "any · 3 · 3-5 · 20-"} />
      </label>
    </div>
    {#if !mRange.trim()}
      {@const L = libs.find((l) => l.path === mLib)}
      <p class="muted small">
        Runs everything in <b>{L?.name ?? "—"}</b> that is not held or deleted —
        <span class="mono">{(L?.run_range || "").length > 70 ? L.run_range.slice(0, 70) + "…" : (L?.run_range || "nothing outstanding")}</span>
      </p>
    {/if}
    <div class="row gap">
      <button class="primary" onclick={start} disabled={!mHost || !!busy}>
        {busy === "start" ? "starting…" : "Start"}
      </button>
      <button class="ghost" onclick={() => (showStart = false)}>Cancel</button>
    </div>
  </div>
{/if}

<style>
  :global(body) { margin: 0; background: #0b0d12; color: #e6e6e6;
    font: 14px/1.45 ui-sans-serif, system-ui, sans-serif; }

  .topbar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: .75rem;
    padding: .55rem .9rem; background: #12151d; border-bottom: 1px solid #232838; flex-wrap: wrap; }
  .brand { font-weight: 700; letter-spacing: -.01em; }
  .libpick { background: #0b0d12; color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .3rem .45rem; font: inherit; font-size: .85rem; max-width: 15rem; }
  .counts { display: flex; gap: .6rem; font-size: .8rem; align-items: center; }
  .c-running { color: #6ee7a0; } .c-held { color: #f5d76e; }
  .spacer { flex: 1; }
  .hostchip { display: inline-flex; align-items: center; gap: .4rem; font-size: .78rem;
    background: #171b26; border: 1px solid #232838; border-radius: 999px; padding: .2rem .3rem .2rem .6rem; }
  .hostchip em { font-style: normal; color: #7ab6f5; }
  .hostchip.down em { color: #f8899f; }
  .hostchip button { background: #232838; border: 0; color: #e6e6e6; border-radius: 999px;
    width: 1.6rem; height: 1.6rem; cursor: pointer; font-size: .7rem; }
  .tb { background: #1b2030; color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .3rem .6rem; font: inherit; font-size: .8rem; cursor: pointer; }
  .tb:hover:not(:disabled) { background: #232838; }
  .tb:disabled { opacity: .4; cursor: not-allowed; }
  .tb.danger, .selbar .danger { border-color: #5a2233; color: #f8899f; }
  .libbar { display: flex; gap: .4rem; padding: .4rem .9rem; background: #0e1119;
    border-bottom: 1px solid #171b26; flex-wrap: wrap; }
  .libchip { display: inline-flex; align-items: center; gap: .4rem; font-size: .78rem;
    background: #161a24; border: 1px solid #232838; border-radius: 999px; padding: .15rem .25rem .15rem .6rem; }
  .libchip button { background: transparent; border: 0; color: #8b93a7; cursor: pointer;
    font-size: .95rem; line-height: 1; padding: 0 .3rem; }
  .libchip button:hover { color: #f8899f; }
  .libtag { color: #7ab6f5; font-size: .72rem; margin-right: .45rem; }
  .search { width: 100%; box-sizing: border-box; margin-bottom: .6rem; }
  .ac { list-style: none; margin: 0; padding: 0; max-height: 46vh; overflow-y: auto;
    border: 1px solid #232838; border-radius: 8px; }
  .ac li + li { border-top: 1px solid #171b26; }
  .acrow { display: flex; align-items: center; gap: .6rem; width: 100%; text-align: left;
    background: transparent; border: 0; color: #e6e6e6; font: inherit; font-size: .85rem;
    padding: .45rem .6rem; cursor: pointer; }
  .acrow.hi { background: #16203a; }
  .acrow.dim { opacity: .5; cursor: default; }
  .acname { flex: 1; }
  .add { width: 2rem; height: 2rem; border-radius: 8px; background: #1d4ed8; color: #fff;
    border: 0; font-size: 1.2rem; line-height: 1; cursor: pointer; }
  .add:hover { background: #2563eb; }

  .bar.err { background: #2a1220; color: #f8899f; }
  .bar.notice { background: #10233a; color: #7ab6f5; cursor: pointer;
    display: block; width: 100%; text-align: left; border: 0; font: inherit; }
  p.bar { margin: 0; padding: .5rem .9rem; font-size: .85rem; }

  .selbar { display: flex; align-items: center; gap: .6rem; padding: .45rem .9rem;
    background: #16203a; border-bottom: 1px solid #232838; font-size: .82rem; }
  .selbar button { background: #232838; color: #e6e6e6; border: 1px solid #2b3244;
    border-radius: 6px; padding: .25rem .6rem; font: inherit; font-size: .8rem; cursor: pointer; }
  .selbar button:disabled { opacity: .4; cursor: not-allowed; }
  .ghost { background: transparent !important; }

  table { width: 100%; border-collapse: collapse; }
  thead th { position: sticky; top: 2.9rem; background: #12151d; text-align: left;
    font-size: .7rem; text-transform: uppercase; letter-spacing: .07em; color: #8b93a7;
    padding: .45rem .6rem; border-bottom: 1px solid #232838; }
  tbody td { padding: .34rem .6rem; border-bottom: 1px solid #171b26; font-size: .84rem; }
  tbody tr { cursor: default; }
  tbody tr:focus-visible { outline: 2px solid #3b82f6; outline-offset: -2px; }
  tbody tr:hover { background: #12151d; }
  tbody tr.sel { background: #16203a; }
  tbody tr.done { color: #6b7280; }
  .num { width: 3rem; color: #8b93a7; text-align: right; font-variant-numeric: tabular-nums; }
  .name { max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .st { width: 8rem; } .pr { width: 12rem; } .rt { width: 5.5rem; } .et { width: 5.5rem; }
  .rt, .et { color: #8b93a7; font-variant-numeric: tabular-nums; }

  .pill { font-size: .68rem; text-transform: uppercase; letter-spacing: .05em;
    padding: .1rem .4rem; border-radius: 999px; background: #232838; color: #aab; }
  .pill.running, .pill.processing { background: #12351f; color: #6ee7a0; }
  .pill.delivering, .pill.fetching, .pill.working { background: #17273f; color: #7ab6f5; }
  .pill.paused { background: #3a3212; color: #f5d76e; }
  .pill.held { background: #2e2a14; color: #d8c26a; }
  .pill.done { background: #1a1f1a; color: #6b8a6b; }

  .bar { height: 6px; background: #232838; border-radius: 3px; overflow: hidden; display: inline-block;
    width: 8rem; vertical-align: middle; }
  .fill { height: 100%; background: linear-gradient(90deg, #3b82f6, #6ee7a0); transition: width .4s; }
  .pct { font-size: .75rem; color: #8b93a7; margin-left: .4rem; font-variant-numeric: tabular-nums; }

  .scrim { position: fixed; inset: 0; background: #000a; z-index: 9;
    border: 0; padding: 0; cursor: default; }
  .modal { position: fixed; z-index: 10; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: min(640px, 92vw); background: #12151d; border: 1px solid #2b3244; border-radius: 12px;
    padding: 1.1rem; box-shadow: 0 20px 60px #000a; }
  .modal h2 { margin: 0 0 .9rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .08em; color: #8b93a7; }
  .form { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .7rem; }
  label { display: flex; flex-direction: column; gap: .25rem; font-size: .78rem; color: #8b93a7; }
  select, input { background: #0b0d12; color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .4rem .45rem; font: inherit; font-size: .84rem; }
  .row.gap { display: flex; gap: .5rem; margin-top: .9rem; }
  button.primary { background: #1d4ed8; color: #fff; border: 0; border-radius: 6px;
    padding: .45rem .9rem; font: inherit; font-size: .85rem; cursor: pointer; }
  .modal button.ghost { background: transparent; color: #e6e6e6; border: 1px solid #2b3244;
    border-radius: 6px; padding: .45rem .9rem; font: inherit; font-size: .85rem; cursor: pointer; }
  .muted { color: #8b93a7; } .small { font-size: .78rem; }
  .mono { font-family: ui-monospace, monospace; font-size: .76rem; }
  .pad { padding: 1.2rem .6rem; }
</style>
