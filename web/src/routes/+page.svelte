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
  $effect(() => { refresh(); loadBook(); timer = setInterval(refresh, 3000); return () => clearInterval(timer); });

  // --- starting a run -------------------------------------------------------
  // The form assembles arguments; the server execs `upscale` with them. Nothing
  // here decides what the command would decide - it shows the exact line it ran.
  let form = $state({ source: "", target: "", archive: "", delete: false,
                      size: 2, workers: 8, scratch: "", devices: [""] });
  let picking = $state("");        // which field the browser is choosing for
  let br = $state({ path: "", up: "", dirs: [], files: 0 });
  let lastCmd = $state("");
  let log = $state("");

  async function openPicker(field) {
    picking = field;
    await go(form[field] || "");
  }
  async function go(path) {
    const r = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    br = await r.json();
  }
  function choose(path) { form[picking] = path; picking = ""; }

  async function start() {
    err = "";
    const r = await fetch("/api/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form) });
    const j = await r.json();
    if (!j.ok) { err = j.error; return; }
    lastCmd = j.command;
    refresh();
  }
  async function loadLog() {
    const r = await fetch("/api/log");
    log = (await r.json()).log || "";
  }
  const addDevice = () => (form.devices = [...form.devices, ""]);
  const dropDevice = (i) => (form.devices = form.devices.filter((_, k) => k !== i));

  // --- machines: an address book, so a rented box is not a row of digits -----
  let book = $state({});
  let showMachines = $state(false);
  let nd = $state({ name: "", ssh: "", scratch: "", workers: "" });
  let probed = $state(null);

  async function loadBook() { book = (await (await fetch("/api/devices")).json()).devices || {}; }
  async function probeDevice() {
    probed = null;
    const r = await fetch("/api/devices/probe", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ssh: nd.ssh }) });
    const j = await r.json();
    probed = j.probe || { reachable: false, error: j.error };
  }
  async function saveDevice() {
    const r = await fetch("/api/devices/add", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(nd) });
    const j = await r.json();
    if (!j.ok) { err = j.error; return; }
    book = j.devices; nd = { name: "", ssh: "", scratch: "", workers: "" }; probed = null;
  }
  async function removeDevice(name) {
    if (!confirm(`Forget ${name}?`)) return;
    const r = await fetch("/api/devices/remove", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    book = (await r.json()).devices || {};
    form.devices = form.devices.filter((d) => d !== name);
  }
  function toggleDevice(name) {
    const has = form.devices.includes(name);
    form.devices = has ? form.devices.filter((d) => d !== name)
                       : [...form.devices.filter((d) => d), name];
  }

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
  <button class="tb" onclick={() => (showMachines = !showMachines)}>⚙ Machines</button>
  {#if d.running}
    <button class="tb" onclick={loadLog}>Log</button>
    {#if d.paused}
      <button class="tb go" onclick={resume}>▶ Resume</button>
    {:else}
      <button class="tb" onclick={pause}>⏸ Pause</button>
    {/if}
    <button class="tb danger" onclick={stop}>■ Stop</button>
  {/if}
</nav>

{#if err}<div class="bar err">{err}</div>{/if}
{#if showMachines}
  <div class="starter">
    <h2>Machines</h2>
    {#each Object.entries(book) as [name, m]}
      <div class="mrow">
        <strong>{name}</strong>
        <code class="mssh">{m.ssh}</code>
        <span class="spacer"></span>
        <button class="tb danger" onclick={() => removeDevice(name)}>Forget</button>
      </div>
    {:else}
      <p class="muted" style="font-size:.83rem;margin:0">No machines yet. Add one below.</p>
    {/each}
    <div class="fields" style="border-top:1px solid #232838;padding-top:.8rem">
      <div class="opts">
        <label><span class="lbl">Name</span><input bind:value={nd.name} placeholder="rental" /></label>
        <label class="grow"><span class="lbl">SSH destination</span>
          <input bind:value={nd.ssh} placeholder="desktop   or   -p 31174 root@1.2.3.4" /></label>
      </div>
      <div class="opts">
        <label class="grow"><span class="lbl">Scratch <em>optional</em></span><input bind:value={nd.scratch} placeholder="/home/kirill/upscale-scratch" /></label>
        <label><span class="lbl">Workers <em>optional</em></span><input type="number" bind:value={nd.workers} /></label>
      </div>
      <div class="pathrow">
        <button type="button" class="tb" onclick={probeDevice} disabled={!nd.ssh}>Test connection</button>
        <button type="button" class="tb go" onclick={saveDevice} disabled={!nd.name || !nd.ssh}>Save machine</button>
      </div>
      {#if probed}
        <div class="probe" class:bad={!probed.reachable}>
          {#if probed.reachable}
            <div><strong>{probed.host}</strong> reachable</div>
            <div class="muted">{probed.gpu || "no NVIDIA GPU reported"} · {probed.cores} cores · {probed.free} GB free</div>
            {#each probed.warnings as w}<div class="warn">⚠ {w}</div>{/each}
          {:else}
            <div class="warn">unreachable — {probed.error}</div>
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

{#if !d.running}
  <form class="starter" onsubmit={(e) => { e.preventDefault(); start(); }}>
    <h2>New run</h2>
    <div class="fields">
      {#each [["source","Source","files to upscale"],["target","Target","where results go"],["archive","Archive","where sources move when done"]] as [k, label, hint]}
        <label>
          <span class="lbl">{label} <em>{hint}</em></span>
          <span class="pathrow">
            <input bind:value={form[k]} placeholder={k === "archive" ? "(or tick Delete)" : "/mnt/media/…"} disabled={k === "archive" && form.delete} />
            <button type="button" class="tb" onclick={() => openPicker(k)} disabled={k === "archive" && form.delete}>Browse</button>
          </span>
        </label>
      {/each}
      <label class="inline">
        <input type="checkbox" bind:checked={form.delete} />
        <span>Delete sources instead of archiving</span>
      </label>
      <div class="opts">
        <label><span class="lbl">Scale</span><input type="number" min="1" max="4" bind:value={form.size} /></label>
        <label><span class="lbl">Workers</span><input type="number" min="1" max="32" bind:value={form.workers} /></label>
        <label class="grow"><span class="lbl">Scratch <em>on the device</em></span><input bind:value={form.scratch} placeholder="(worker default)" /></label>
      </div>
      <div class="devs">
        <span class="lbl">Devices <em>pick a machine, or type an ssh destination</em></span>
        {#if Object.keys(book).length}
          <div class="chips">
            {#each Object.entries(book) as [name, m]}
              <button type="button" class="pickchip" class:on={form.devices.includes(name)}
                      onclick={() => toggleDevice(name)} title={m.ssh}>{name}</button>
            {/each}
          </div>
        {/if}
        {#each form.devices as _, i}
          <span class="pathrow">
            <input bind:value={form.devices[i]} placeholder={"desktop   or   -p 31174 root@1.2.3.4"} />
            {#if form.devices.length > 1}<button type="button" class="tb" onclick={() => dropDevice(i)}>−</button>{/if}
          </span>
        {/each}
        <button type="button" class="tb" onclick={addDevice}>+ device</button>
      </div>
    </div>
    <button class="tb go start" type="submit">▶ Start run</button>
  </form>

  {#if picking}
    <div class="picker">
      <div class="pickhead">
        <strong>{br.path || "Pick a directory"}</strong>
        <span class="spacer"></span>
        {#if br.up}<button class="tb" onclick={() => go(br.up)}>↑ up</button>{/if}
        {#if br.path}<button class="tb go" onclick={() => choose(br.path)}>Use this</button>{/if}
        <button class="tb" onclick={() => (picking = "")}>Cancel</button>
      </div>
      <ul class="dirs">
        {#each br.dirs as x}
          <li><button onclick={() => go(x.path)}>{x.name}</button><span class="muted">{x.files} files</span></li>
        {/each}
      </ul>
    </div>
  {/if}
{/if}

{#if lastCmd}<div class="bar cmd"><code>{lastCmd}</code></div>{/if}
{#if d.source}<div class="paths"><span>{d.source}</span> → <span>{d.target}</span></div>{/if}

{#if log}<pre class="runlog">{log}</pre>{/if}

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
  .bar.cmd { background: #10233a; color: #7ab6f5; font-family: ui-monospace, monospace;
    font-size: .78rem; overflow-x: auto; }
  .starter { margin: 1rem .9rem; padding: 1rem 1.1rem; background: #12151d;
    border: 1px solid #232838; border-radius: 8px; display: flex;
    flex-direction: column; gap: .9rem; }
  .starter h2 { margin: 0; font-size: .95rem; font-weight: 600; }
  .fields { display: flex; flex-direction: column; gap: .7rem; }
  label { display: flex; flex-direction: column; gap: .25rem; font-size: .8rem; }
  .lbl { color: #8b93a7; }
  .lbl em { font-style: normal; color: #5c6474; }
  input { background: #0b0d12; color: #e6e6e6; border: 1px solid #2b3244;
    border-radius: 5px; padding: .35rem .5rem; font: inherit; font-size: .82rem; }
  input:disabled { opacity: .45; }
  .pathrow { display: flex; gap: .4rem; align-items: center; }
  .pathrow input { flex: 1; }
  .inline { flex-direction: row; align-items: center; gap: .45rem; }
  .inline input { width: auto; }
  .opts { display: flex; gap: .7rem; flex-wrap: wrap; }
  .opts input { width: 6rem; }
  .opts .grow { flex: 1; min-width: 12rem; }
  .opts .grow input { width: 100%; }
  .devs { display: flex; flex-direction: column; gap: .35rem; }
  .start { align-self: flex-start; padding: .45rem 1rem; }
  .picker { margin: 0 .9rem 1rem; border: 1px solid #232838; border-radius: 8px;
    background: #12151d; }
  .pickhead { display: flex; gap: .4rem; align-items: center; padding: .6rem .8rem;
    border-bottom: 1px solid #232838; font-size: .82rem; }
  .dirs { list-style: none; margin: 0; padding: .3rem 0; max-height: 40vh;
    overflow-y: auto; }
  .dirs li { display: flex; align-items: center; gap: .6rem; padding: 0 .8rem; }
  .dirs button { flex: 1; text-align: left; background: none; border: 0;
    color: #e6e6e6; font: inherit; font-size: .84rem; padding: .3rem 0; cursor: pointer; }
  .dirs button:hover { color: #7ab6f5; }
  .dirs .muted { font-size: .74rem; font-variant-numeric: tabular-nums; }
  .mrow { display: flex; align-items: center; gap: .6rem; font-size: .85rem;
    padding: .3rem 0; border-bottom: 1px solid #171b26; }
  .mssh { font-family: ui-monospace, monospace; font-size: .74rem; color: #8b93a7;
    background: #0b0d12; padding: .1rem .4rem; border-radius: 4px; }
  .chips { display: flex; gap: .4rem; flex-wrap: wrap; margin-bottom: .3rem; }
  .pickchip { background: #171b26; border: 1px solid #2b3244; color: #8b93a7;
    border-radius: 999px; padding: .2rem .7rem; font: inherit; font-size: .8rem; cursor: pointer; }
  .pickchip.on { background: #17273f; border-color: #3b82f6; color: #7ab6f5; }
  .probe { background: #0b0d12; border: 1px solid #232838; border-radius: 6px;
    padding: .6rem .8rem; font-size: .82rem; display: flex; flex-direction: column; gap: .15rem; }
  .probe.bad { border-color: #5a2233; }
  .warn { color: #f5d76e; }
  .runlog { margin: 0 .9rem 1rem; padding: .8rem; background: #0b0d12;
    border: 1px solid #232838; border-radius: 8px; font-size: .74rem;
    max-height: 30vh; overflow: auto; white-space: pre-wrap; }
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
