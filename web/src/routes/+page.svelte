<script>
  import { onMount, onDestroy } from "svelte";

  let libraries = $state([]);
  let hosts = $state([]);
  let error = $state("");

  // form
  let lib = $state("");
  let host = $state("");
  let scratch = $state("");
  let range = $state("any");

  let outstanding = $state(null);
  let outLoading = $state(false);
  let busy = $state("");
  let notice = $state("");

  let timer;

  const j = async (url, opts) => {
    const r = await fetch(url, opts);
    if (!r.ok && r.status >= 500) throw new Error(`${url} → ${r.status}`);
    return r.json();
  };

  async function loadHosts() {
    try {
      const d = await j("/api/hosts");
      hosts = d.hosts || [];
      if (!host && hosts.length) {
        host = hosts[0].id;
        scratch = hosts[0].default_scratch || Object.keys(hosts[0].scratch || {})[0] || "";
      }
      error = "";
    } catch (e) {
      error = String(e);
    }
  }

  async function loadLibraries() {
    const d = await j("/api/libraries");
    libraries = d.libraries || [];
  }

  async function loadOutstanding() {
    if (!lib) { outstanding = null; return; }
    outLoading = true;
    try {
      outstanding = await j(`/api/outstanding?lib=${encodeURIComponent(lib)}`);
    } catch (e) {
      outstanding = { error: String(e), episodes: [] };
    } finally {
      outLoading = false;
    }
  }

  async function start() {
    busy = "start"; notice = "";
    try {
      const d = await j("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, library: lib, range, scratch }),
      });
      notice = d.ok
        ? `started ${range} on ${d.host} — scratch ${d.work}`
        : `refused: ${d.error}`;
      await loadHosts();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  async function act(id, action) {
    busy = action; notice = "";
    try {
      const d = await j(`/api/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: id }),
      });
      notice = d.ok ? (d.said || `${action} sent`) : `failed: ${d.error}`;
      await loadHosts();
    } catch (e) { notice = String(e); }
    finally { busy = ""; }
  }

  const hhmm = (s) => {
    if (!s || s < 0) return "–";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  };

  const selected = $derived(hosts.find((h) => h.id === host));

  onMount(async () => {
    await Promise.all([loadLibraries(), loadHosts()]);
    // 3s: fast enough that pause feels immediate, slow enough that each poll's
    // ssh round trip to every host has finished before the next one starts.
    timer = setInterval(loadHosts, 3000);
  });
  onDestroy(() => clearInterval(timer));
</script>

<svelte:head><title>upscale</title></svelte:head>

<main>
  <header>
    <h1>upscale</h1>
    <span class="sub">library on ubuntuserver · GPU elsewhere</span>
  </header>

  {#if error}<p class="err">{error}</p>{/if}

  <section class="panel">
    <h2>Hosts</h2>
    {#each hosts as h}
      <article class="host" class:down={!h.reachable}>
        <div class="row">
          <strong>{h.label}</strong>
          <span class="badge {h.reachable ? (h.state || 'idle') : 'down'}">
            {h.reachable ? (h.state || "idle") : "unreachable"}
          </span>
          {#if h.reachable && h.gpu_busy}<span class="badge gpu">gpu busy</span>{/if}
        </div>

        {#if !h.reachable}
          <p class="err">{h.error}</p>
        {:else if h.episode}
          <p class="ep">{h.episode}</p>
          <div class="bar"><div class="fill" style="width:{h.percent || 0}%"></div></div>
          <dl>
            <div><dt>progress</dt><dd>{h.chunks_done}/{h.chunks_total} chunks · {h.frames_done?.toLocaleString()}/{h.frames_total?.toLocaleString()} frames</dd></div>
            <div><dt>rate</dt><dd>{h.fps} fps</dd></div>
            <div><dt>eta</dt><dd>{hhmm(h.eta_s)}</dd></div>
            <div><dt>elapsed</dt><dd>{hhmm(h.elapsed_s)}</dd></div>
            <div><dt>scratch</dt><dd class="mono">{h.work}</dd></div>
          </dl>
        {:else}
          <p class="muted">no job on this host</p>
        {/if}

        {#if h.reachable}
          <p class="muted small">
            {h.queue_running ? `queue: ${h.queue_cmd}` : "no queue driving this host — it will stop after the current episode"}
          </p>
          <div class="row gap">
            <button onclick={() => act(h.id, "pause")} disabled={busy || h.state === "paused"}>Pause</button>
            <button onclick={() => act(h.id, "resume")} disabled={busy || h.state !== "paused"}>Resume</button>
            <button class="danger" onclick={() => act(h.id, "stop")} disabled={busy || !h.episode}>Stop after episode</button>
          </div>
        {/if}
      </article>
    {:else}
      <p class="muted">no hosts configured</p>
    {/each}
  </section>

  <section class="panel">
    <h2>Start a run</h2>
    <div class="form">
      <label>
        Source
        <select bind:value={lib} onchange={loadOutstanding}>
          <option value="">— pick a library —</option>
          {#each libraries as l}
            <option value={l.path}>{l.name} · {l.files} files{l.archived ? ` · ${l.archived} done` : ""} · .{l.src_ext}</option>
          {/each}
        </select>
      </label>

      <label>
        Destination host
        <select bind:value={host} onchange={() => { const h = hosts.find(x => x.id === host); scratch = h?.default_scratch || ""; }}>
          {#each hosts as h}<option value={h.id}>{h.label}</option>{/each}
        </select>
      </label>

      <label>
        Scratch
        <select bind:value={scratch}>
          {#each Object.entries(selected?.scratch || {}) as [k, p]}
            <option value={k}>{k} — {p}</option>
          {/each}
        </select>
      </label>

      <label>
        Episodes
        <input bind:value={range} placeholder="any · 3 · 3-5 · 20-" />
      </label>
    </div>

    {#if lib}
      <p class="muted small">
        {#if outLoading}checking what is outstanding…
        {:else if outstanding?.error}<span class="err">{outstanding.error}</span>
        {:else if outstanding}{outstanding.count} outstanding — next: {outstanding.episodes[0]?.name ?? "nothing"}
        {/if}
      </p>
    {/if}

    <button class="primary" onclick={start} disabled={!lib || !host || busy}>
      {busy === "start" ? "starting…" : "Start"}
    </button>
    {#if notice}<p class="notice">{notice}</p>{/if}
  </section>
</main>

<style>
  :global(body) { margin: 0; background: var(--bg, #0f1115); color: var(--fg, #e6e6e6);
    font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; }
  main { max-width: 900px; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
  header { display: flex; align-items: baseline; gap: .75rem; margin-bottom: 1.25rem; }
  h1 { font-size: 1.4rem; margin: 0; letter-spacing: -.01em; }
  h2 { font-size: .85rem; text-transform: uppercase; letter-spacing: .08em;
       color: #8b93a7; margin: 0 0 .75rem; }
  .sub { color: #8b93a7; font-size: .85rem; }
  .panel { background: #161923; border: 1px solid #232838; border-radius: 10px;
           padding: 1rem; margin-bottom: 1rem; }
  .host { border-top: 1px solid #232838; padding-top: .85rem; margin-top: .85rem; }
  .host:first-of-type { border-top: 0; padding-top: 0; margin-top: 0; }
  .host.down { opacity: .75; }
  .row { display: flex; align-items: center; gap: .6rem; }
  .row.gap { margin-top: .7rem; flex-wrap: wrap; }
  .ep { margin: .5rem 0 .4rem; font-size: .95rem; }
  .badge { font-size: .7rem; text-transform: uppercase; letter-spacing: .06em;
           padding: .12rem .45rem; border-radius: 999px; background: #232838; color: #aab; }
  .badge.running { background: #12351f; color: #6ee7a0; }
  .badge.paused  { background: #3a3212; color: #f5d76e; }
  .badge.down, .badge.stopping { background: #3a1620; color: #f8899f; }
  .badge.gpu { background: #17273f; color: #7ab6f5; }
  .bar { height: 7px; background: #232838; border-radius: 4px; overflow: hidden; }
  .fill { height: 100%; background: linear-gradient(90deg, #3b82f6, #6ee7a0); transition: width .4s; }
  dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
       gap: .3rem 1rem; margin: .7rem 0 0; }
  dl div { display: flex; gap: .4rem; font-size: .85rem; }
  dt { color: #8b93a7; } dd { margin: 0; }
  .mono { font-family: ui-monospace, monospace; font-size: .8rem; }
  .form { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: .75rem; }
  label { display: flex; flex-direction: column; gap: .3rem; font-size: .8rem; color: #8b93a7; }
  select, input { background: #0f1115; color: #e6e6e6; border: 1px solid #2b3244;
                  border-radius: 6px; padding: .45rem .5rem; font: inherit; font-size: .85rem; }
  button { background: #232838; color: #e6e6e6; border: 1px solid #2b3244;
           border-radius: 6px; padding: .45rem .8rem; font: inherit; font-size: .85rem; cursor: pointer; }
  button:hover:not(:disabled) { background: #2b3244; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.primary { background: #1d4ed8; border-color: #1d4ed8; margin-top: .9rem; }
  button.danger { border-color: #4a2130; }
  .muted { color: #8b93a7; } .small { font-size: .8rem; }
  .err { color: #f8899f; font-size: .85rem; }
  .notice { margin-top: .6rem; font-size: .85rem; color: #7ab6f5; }
</style>
