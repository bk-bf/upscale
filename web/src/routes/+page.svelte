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

  let lib = $state("");
  let selected = $state(new Set());
  let lastClicked = $state(null);
  let showStart = $state(false);

  // start-modal fields
  let mHost = $state("");
  let mScratch = $state("");
  let mRange = $state("");

  let timer;

  const j = async (url, opts) => {
    const r = await fetch(url, opts);
    if (!r.ok && r.status >= 500) throw new Error(`${url} → ${r.status}`);
    return r.json();
  };

  async function loadLibraries() {
    const d = await j("/api/libraries");
    libraries = d.libraries || [];
    if (!lib && libraries.length) {
      // open on something with work in it rather than the alphabetical first
      lib = (libraries.find((l) => l.archived > 0) || libraries[0]).path;
    }
  }

  async function refresh() {
    if (!lib) return;
    try {
      const d = await j(`/api/queue?lib=${encodeURIComponent(lib)}`);
      if (d.error) { error = d.error; return; }
      rows = d.rows || [];
      counts = d.counts || {};
      hosts = d.hosts || [];
      runRange = d.run_range || "";
      if (!mHost && hosts.length) {
        mHost = hosts[0].id;
        mScratch = hosts[0].default_scratch || "";
      }
      error = "";
    } catch (e) { error = String(e); }
  }

  async function hold(eps, on) {
    if (!eps.length) return;
    busy = "hold";
    try {
      const d = await j("/api/hold", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ library: lib, episodes: eps, hold: on }),
      });
      notice = d.ok ? `${on ? "held" : "released"} ${eps.length} episode${eps.length > 1 ? "s" : ""}` : `failed: ${d.error}`;
      await refresh();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

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
        body: JSON.stringify({ host: mHost, library: lib, scratch: mScratch,
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
      for (let k = a; k <= b; k++) if (rows[k].status !== "done") s.add(rows[k].n);
    } else if (ev.ctrlKey || ev.metaKey) {
      s.has(r.n) ? s.delete(r.n) : s.add(r.n);
      lastClicked = i;
    } else {
      s.clear(); s.add(r.n); lastClicked = i;
    }
    selected = s;
  }

  const selectedRows = $derived(rows.filter((r) => selected.has(r.n)));
  const canHold    = $derived(selectedRows.some((r) => r.status === "queued"));
  const canRelease = $derived(selectedRows.some((r) => r.status === "held"));
  const selHost = $derived(hosts.find((h) => h.id === mHost));

  const hhmm = (s) => {
    if (!s || s < 0) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  };

  onMount(async () => {
    await loadLibraries();
    await refresh();
    timer = setInterval(refresh, 3000);
  });
  onDestroy(() => clearInterval(timer));
</script>

<svelte:window onkeydown={(e) => { if (e.key === "Escape") showStart = false; }} />

<svelte:head><title>upscale</title></svelte:head>

<nav class="topbar">
  <span class="brand">upscale</span>
  <select class="libpick" bind:value={lib} onchange={() => { selected = new Set(); refresh(); }}>
    {#each libraries as l}
      <option value={l.path}>{l.name}</option>
    {/each}
  </select>

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
      {#if h.reachable}
        <button onclick={() => act(h.id, h.state === "paused" ? "resume" : "pause")} disabled={!!busy}>
          {h.state === "paused" ? "▶" : "❚❚"}
        </button>
      {/if}
    </span>
  {/each}

  <button class="add" onclick={() => { mRange = ""; showStart = true; }} title="Start a run">+</button>
</nav>

{#if error}<p class="err bar">{error}</p>{/if}
{#if notice}<button class="notice bar" onclick={() => (notice = "")}>{notice}</button>{/if}

{#if selected.size}
  <div class="selbar">
    <span>{selected.size} selected</span>
    <button onclick={() => hold([...selected], true)} disabled={!canHold || !!busy}>❚❚ Hold</button>
    <button onclick={() => hold([...selected], false)} disabled={!canRelease || !!busy}>▶ Release</button>
    <button class="ghost" onclick={() => (selected = new Set())}>Clear</button>
    <span class="muted small">shift-click for a range · ctrl-click to add</span>
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
        <td class="name" title={r.name}>{r.name}</td>
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
      <tr><td colspan="6" class="muted pad">nothing here — pick another library</td></tr>
    {/each}
  </tbody>
</table>

{#if showStart}
  <button class="scrim" aria-label="Close" onclick={() => (showStart = false)}></button>
  <div class="modal">
    <h2>Start a run</h2>
    <div class="form">
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
    <p class="muted small">
      Source: <b>{libraries.find((l) => l.path === lib)?.name ?? lib}</b>.
      {#if !mRange.trim()}
        Runs everything not held{counts.held ? ` (${counts.held} held will be skipped)` : ""} —
        <span class="mono">{runRange.length > 70 ? runRange.slice(0, 70) + "…" : runRange || "nothing outstanding"}</span>
      {/if}
    </p>
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
