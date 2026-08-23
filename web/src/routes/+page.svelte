<script>
  // The page shows one run: what is queued, what is being worked on, what is
  // done. Everything it displays comes from /api/queue, which is the run's own
  // snapshot - there is nothing here that decides anything.
  let d = $state({ rows: [], devices: [], counts: {}, source: "", target: "", running: false });
  let err = $state("");
  let timer;

  async function refresh() {
    try {
      const r = await fetch("/api/queue");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      d = await r.json();
      err = "";
    } catch (e) { err = String(e); }
  }
  async function post(path, ask) {
    if (ask && !confirm(ask)) return;
    const r = await fetch(path, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (!j.ok) err = j.error || `${path} failed`;
    refresh();
  }
  const pause  = () => post("/api/pause");
  const resume = () => post("/api/resume");
  const stop   = () => post("/api/stop", "Stop after the current file?");
  $effect(() => { refresh(); timer = setInterval(refresh, 3000); return () => clearInterval(timer); });

  const gb = (b) => (b ? `${(b / 1073741824).toFixed(1)} GB` : "");
  const hhmm = (s) => (s ? `${Math.floor(s / 60)}m` : "");
  const dir = (p) => (p || "").replace(/\/[^/]*$/, "");
</script>

<nav class="topbar">
  <span class="brand">upscale</span>
  <span class="counts">
    {#if d.counts.running}<b class="c-run">{d.counts.running} running</b>{/if}
    {#if d.counts.paused}<b class="c-hold">{d.counts.paused} paused</b>{/if}
    <span>{d.counts.queued ?? 0} queued</span>
    <span class="muted">{d.counts.done ?? 0} done</span>
  </span>
  {#each d.devices as h}
    <span class="chip" class:down={!h.reachable}>
      {h.device}<em>{h.reachable ? (h.paused ? "paused" : (h.phase || "idle")) : "unreachable"}</em>
    </span>
  {/each}
  <span class="spacer"></span>
  {#if d.running}
    {#if d.paused}
      <button class="tb go" onclick={resume}>▶ Resume</button>
    {:else}
      <button class="tb" onclick={pause}>⏸ Pause</button>
    {/if}
    <button class="tb danger" onclick={stop}>■ Stop</button>
  {/if}
</nav>

{#if err}<div class="bar err">{err}</div>{/if}
{#if !d.running}<div class="bar notice">nothing running — start one with <code>upscale --source … --target … --device …</code></div>{/if}
{#if d.source}<div class="paths"><span>{d.source}</span> → <span>{d.target}</span></div>{/if}

<table>
  <thead><tr>
    <th class="num">#</th><th>File</th><th class="dev">Device</th>
    <th class="st">Status</th><th class="pr">Progress</th><th class="rt">Rate</th><th class="et">ETA</th>
  </tr></thead>
  <tbody>
    {#each d.rows as r}
      <tr class={r.status}>
        <td class="num">{r.n}</td>
        <td class="name" title={r.path}>
          <span class="fname">{r.name}</span>
          <span class="fpath">{dir(r.path)}</span>
        </td>
        <td class="dev">{#if r.device}<span class="devtag">{r.device}</span>{:else}<span class="muted">—</span>{/if}</td>
        <td class="st"><span class="pill {r.status}">{r.status === "running" && r.phase ? r.phase : r.status}</span></td>
        <td class="pr">
          {#if r.status === "running"}
            <span class="bar2"><span class="fill" style="width:{r.percent}%"></span></span>
            <span class="pct">{r.percent}%</span>
          {:else}<span class="muted">{gb(r.size)}</span>{/if}
        </td>
        <td class="rt">{r.fps ? `${(+r.fps).toFixed(1)} fps` : ""}</td>
        <td class="et">{hhmm(r.eta_s)}</td>
      </tr>
    {:else}
      <tr><td colspan="7" class="muted pad">nothing in the source or target directory</td></tr>
    {/each}
  </tbody>
</table>

<style>
  :global(body) { margin: 0; background: #0b0d12; color: #e6e6e6;
    font: 14px/1.45 ui-sans-serif, system-ui, sans-serif; }
  .topbar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: .6rem;
    padding: .55rem .9rem; background: #12151d; border-bottom: 1px solid #232838; flex-wrap: wrap; }
  .brand { font-weight: 700; }
  .counts { display: flex; gap: .55rem; font-size: .8rem; align-items: center; }
  .c-run { color: #6ee7a0; }
  .spacer { flex: 1; }
  .chip { display: inline-flex; gap: .4rem; align-items: center; font-size: .78rem;
    background: #171b26; border: 1px solid #232838; border-radius: 999px; padding: .15rem .6rem; }
  .chip em { font-style: normal; color: #7ab6f5; }
  .chip.down em { color: #f8899f; }
  .tb { background: #1b2030; color: #e6e6e6; border: 1px solid #2b3244; border-radius: 6px;
    padding: .3rem .7rem; font: inherit; font-size: .82rem; cursor: pointer; }
  .tb.danger { border-color: #5a2233; color: #f8899f; }
  .tb.go { border-color: #1f4d33; color: #6ee7a0; }
  .c-hold { color: #f5d76e; }
  .pill.paused { background: #3a3212; color: #f5d76e; }
  .bar { padding: .5rem .9rem; font-size: .85rem; }
  .bar.err { background: #2a1220; color: #f8899f; }
  .bar.notice { background: #10233a; color: #7ab6f5; }
  .paths { padding: .4rem .9rem; font-family: ui-monospace, monospace; font-size: .75rem; color: #8b93a7; }
  table { width: 100%; border-collapse: collapse; }
  thead th { position: sticky; top: 2.9rem; background: #12151d; text-align: left; font-size: .7rem;
    text-transform: uppercase; letter-spacing: .07em; color: #8b93a7; padding: .45rem .6rem;
    border-bottom: 1px solid #232838; }
  tbody td { padding: .34rem .6rem; border-bottom: 1px solid #171b26; font-size: .84rem; }
  tbody tr.done { color: #6b7280; }
  .num { width: 3rem; color: #8b93a7; text-align: right; font-variant-numeric: tabular-nums; }
  .name { max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fname, .fpath { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fpath { font-family: ui-monospace, monospace; font-size: .7rem; color: #6b7280; }
  .dev { width: 14rem; } .st { width: 8rem; } .pr { width: 11rem; }
  .rt { width: 6rem; } .et { width: 5rem; }
  .rt, .et { color: #8b93a7; font-variant-numeric: tabular-nums; }
  .devtag { font-size: .72rem; background: #17273f; color: #7ab6f5; padding: .1rem .45rem;
    border-radius: 999px; font-family: ui-monospace, monospace; }
  .pill { font-size: .68rem; text-transform: uppercase; letter-spacing: .05em; padding: .1rem .4rem;
    border-radius: 4px; background: #232838; color: #8b93a7; }
  .pill.running, .pill.upscaling { background: #12351f; color: #6ee7a0; }
  .pill.done { background: #1a1f1a; color: #6b8a6b; }
  .bar2 { display: inline-block; height: 6px; width: 7rem; background: #232838; border-radius: 3px;
    overflow: hidden; vertical-align: middle; }
  .fill { display: block; height: 100%; background: linear-gradient(90deg, #3b82f6, #6ee7a0); }
  .pct { font-size: .75rem; color: #8b93a7; margin-left: .4rem; font-variant-numeric: tabular-nums; }
  .muted { color: #8b93a7; }
  .pad { padding: 1.2rem .6rem; }

  /* phone: the table becomes one card per row */
  @media (max-width: 760px) {
    thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
    table, tbody, tr, td { display: block; width: auto; }
    tbody tr { border: 1px solid #232838; border-radius: 10px; margin: .5rem .55rem; padding: .5rem .7rem; }
    tbody td { padding: .1rem 0; border-bottom: 0; }
    .name { max-width: none; white-space: normal; font-weight: 600; }
    .fname, .fpath { white-space: normal; overflow-wrap: anywhere; }
    .num, .dev, .st, .rt, .et { display: inline-flex; width: auto; text-align: left;
      margin: .1rem .5rem .1rem 0; }
    .pr { display: flex; align-items: center; gap: .4rem; }
    .bar2 { flex: 1; width: auto; }
  }
</style>
