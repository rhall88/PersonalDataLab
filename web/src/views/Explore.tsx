import { createResource, createSignal, For, Show } from "solid-js";
import { api, qs } from "../api";
import { ClaimWhy } from "../components/EvidenceDrawer";
import { Async, Epistemic, EvidenceButton, KV, MonthBars } from "../components/ui";
import { go, href, route } from "../router";
import { fmtDate, fmtNum, openEvidenceFresh } from "../state";

// --- Topics ---------------------------------------------------------------------------------

function TopicDetail(props: { ci: number }) {
  const [d] = createResource(() => props.ci, (ci) => api(`/topics/${ci}${qs({ sample_size: 20 })}`));
  return (
    <Async data={d}>
      {(t: any) => (
        <section class="detail">
          <p><a href={href("/topics")}>← all topics</a></p>
          <h2>
            Topic #{t.cluster_index}: {t.cluster_label} <Epistemic cls="STATISTICAL" />
          </h2>
          <p class="muted">{t.note}</p>
          <p>
            {fmtNum(t.size)} text units · sources:{" "}
            {Object.entries(t.sources).map(([s, n]) => `${s} ${fmtNum(n as number)}`).join(", ")}{" "}
            <EvidenceButton evidence={t.evidence} label="All units" title={`Topic ${t.cluster_index}`} />
          </p>
          <h3>Over time</h3>
          <MonthBars months={t.by_month.map((m: any) => String(m.month))} a={t.by_month.map((m: any) => m.count)} aLabel="units" />
          <h3>Representative units (nearest the cluster centre)</h3>
          <ul class="ev-list">
            <For each={t.samples}>
              {(s: any) => (
                <li class="ev-item">
                  <div class="ev-meta">
                    <span>{s.source}</span><span>{fmtDate(s.started_at)}</span>
                    <button class="link" onClick={() => openEvidenceFresh({ type: "text_unit", id: s.id })}>open</button>
                  </div>
                  <div class="ev-text">{s.text}</div>
                </li>
              )}
            </For>
          </ul>
        </section>
      )}
    </Async>
  );
}

export function Topics() {
  const ci = () => route().path[1];
  const [noise, setNoise] = createSignal(false);
  const [minSize, setMinSize] = createSignal(0);
  const [filter, setFilter] = createSignal("");
  const [source, setSource] = createSignal("");
  const [list] = createResource(() => (ci() ? null : { include_noise: noise(), min_size: minSize() }), (p) => api(`/topics${qs(p)}`));
  return (
    <div class="view">
      <Show when={ci() !== undefined} fallback={
        <>
          <h2>Topics</h2>
          <p class="muted">Unsupervised clusters of your message text. Labels are machine-generated keywords, not names.</p>
          <div class="filters">
            <label>filter <input value={filter()} onInput={(e) => setFilter(e.currentTarget.value)} placeholder="keyword" /></label>
            <label>source{" "}
              <select value={source()} onChange={(e) => setSource(e.currentTarget.value)}>
                <option value="">any</option>
                <For each={["imessages", "facebook", "google", "openai", "anthropic", "apple"]}>{(s) => <option value={s}>{s}</option>}</For>
              </select>
            </label>
            <label>min size <input type="number" min="0" value={minSize()} onChange={(e) => setMinSize(Number(e.currentTarget.value))} /></label>
            <label><input type="checkbox" checked={noise()} onChange={(e) => setNoise(e.currentTarget.checked)} /> include noise (unclustered)</label>
          </div>
          <Async data={list}>
            {(d: any) => (
              <table class="grid">
                <thead><tr><th>#</th><th>Keywords</th><th class="num">Units</th><th>Sources</th><th>Span</th><th /></tr></thead>
                <tbody>
                  <For each={d.topics.filter((t: any) => (!filter() || t.cluster_label.toLowerCase().includes(filter().toLowerCase())) && (!source() || t.sources.includes(source())))}>
                    {(t: any) => (
                      <tr>
                        <td><a href={href(`/topics/${t.cluster_index}`)}>{t.cluster_index}</a></td>
                        <td><a href={href(`/topics/${t.cluster_index}`)}>{t.cluster_label}</a></td>
                        <td class="num">{fmtNum(t.size)}</td>
                        <td>{t.sources.join(", ")}</td>
                        <td>{fmtDate(t.first_at)} – {fmtDate(t.last_at)}</td>
                        <td><EvidenceButton evidence={t.evidence} label="Units" /></td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            )}
          </Async>
        </>
      }>
        <TopicDetail ci={Number(ci())} />
      </Show>
    </div>
  );
}

// --- Claims ---------------------------------------------------------------------------------

export function Claims() {
  const id = () => route().path[1];
  const q = () => route().query;
  const params = () => ({ template: q().get("template") ?? undefined, class: q().get("class") ?? undefined, status: q().get("status") ?? "active", limit: 500 });
  const [list] = createResource(() => (id() ? null : params()), (p) => api(`/claims${qs(p)}`));
  const [text, setText] = createSignal("");
  const set = (k: string, v: string) => go("/claims", { ...Object.fromEntries(q()), [k]: v });
  return (
    <div class="view">
      <Show when={id() !== undefined} fallback={
        <>
          <h2>Claims</h2>
          <p class="muted">Stored, evidence-backed statements computed from your data. “Why?” refetches each one's evidence.</p>
          <Async data={list}>
            {(d: any) => (
              <>
                <div class="filters">
                  <label>template{" "}
                    <select value={q().get("template") ?? ""} onChange={(e) => set("template", e.currentTarget.value)}>
                      <option value="">all</option>
                      <For each={d.templates}>{(t: string) => <option value={t}>{t}</option>}</For>
                    </select>
                  </label>
                  <label>class{" "}
                    <select value={q().get("class") ?? ""} onChange={(e) => set("class", e.currentTarget.value)}>
                      <option value="">all</option>
                      <For each={["STATISTICAL", "DECLARED", "COMPUTED", "SEMANTIC", "HYPOTHESIS"]}>{(c) => <option value={c}>{c}</option>}</For>
                    </select>
                  </label>
                  <label>status{" "}
                    <select value={q().get("status") ?? "active"} onChange={(e) => set("status", e.currentTarget.value)}>
                      <option value="active">active</option>
                      <option value="">any</option>
                    </select>
                  </label>
                  <label>contains <input value={text()} onInput={(e) => setText(e.currentTarget.value)} /></label>
                  <span class="muted">{fmtNum(d.total)} claims</span>
                </div>
                <table class="grid">
                  <thead><tr><th>Statement</th><th>Class</th><th>Template</th><th>Subject</th><th>Epoch</th><th /></tr></thead>
                  <tbody>
                    <For each={d.items.filter((c: any) => !text() || c.statement.toLowerCase().includes(text().toLowerCase()))}>
                      {(c: any) => (
                        <tr>
                          <td>{c.statement}</td>
                          <td><Epistemic cls={c.class} /></td>
                          <td>{c.template}</td>
                          <td class="small">{Object.values(c.subject ?? {}).join(" · ")}</td>
                          <td>{c.computed_epoch_id}</td>
                          <td><a href={href(`/claims/${c.id}`)}>Why?</a></td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </>
            )}
          </Async>
        </>
      }>
        <section class="detail">
          <p><a href={href("/claims")}>← all claims</a></p>
          <ClaimWhy id={Number(id())} />
        </section>
      </Show>
    </div>
  );
}

// --- Search ---------------------------------------------------------------------------------

export function Search() {
  const q = () => route().query;
  const [input, setInput] = createSignal(q().get("q") ?? "");
  const [results] = createResource(
    () => (q().get("q") ? { q: q().get("q")!, mode: q().get("mode") ?? "full_text", source: q().get("source") ?? "", docs: q().get("docs") !== "0" } : null),
    (p) =>
      p.mode === "semantic"
        ? api("/search/semantic", { method: "POST", body: { query: p.q, include_documents: p.docs, limit: 50 } })
        : api("/search/full-text", { method: "POST", body: { query: p.q, sources: p.source ? [p.source] : [], limit: 100 } }),
  );
  const submit = (e: Event) => {
    e.preventDefault();
    go("/search", { ...Object.fromEntries(q()), q: input() });
  };
  const open = (h: any) =>
    h.kind === "text_unit"
      ? openEvidenceFresh({ type: "text_unit", id: h.id })
      : h.kind === "document_text_unit"
        ? h.record_id && openEvidenceFresh({ type: "canonical", kind: "record", id: h.record_id })
        : h.kind === "conversation"
          ? openEvidenceFresh({ type: "conversation", id: h.id })
          : openEvidenceFresh({ type: "canonical", kind: h.kind, id: h.id });
  return (
    <div class="view">
      <h2>Search</h2>
      <form class="filters" onSubmit={submit}>
        <input class="wide" value={input()} onInput={(e) => setInput(e.currentTarget.value)} placeholder="words, “exact phrase”, -exclude" />
        <label><input type="radio" name="mode" checked={(q().get("mode") ?? "full_text") === "full_text"} onChange={() => go("/search", { ...Object.fromEntries(q()), mode: "full_text" })} /> Full text</label>
        <label><input type="radio" name="mode" checked={q().get("mode") === "semantic"} onChange={() => go("/search", { ...Object.fromEntries(q()), mode: "semantic" })} /> Semantic</label>
        <Show when={q().get("mode") !== "semantic"}>
          <select value={q().get("source") ?? ""} onChange={(e) => go("/search", { ...Object.fromEntries(q()), source: e.currentTarget.value })}>
            <option value="">all sources</option>
            <For each={["imessages", "facebook", "google", "apple", "openai", "anthropic"]}>{(s) => <option value={s}>{s}</option>}</For>
          </select>
        </Show>
        <Show when={q().get("mode") === "semantic"}>
          <label><input type="checkbox" checked={q().get("docs") !== "0"} onChange={(e) => go("/search", { ...Object.fromEntries(q()), docs: e.currentTarget.checked ? "1" : "0" })} /> include documents</label>
        </Show>
        <button type="submit" class="primary">Search</button>
      </form>
      <Show when={q().get("q")}>
        <Async data={results}>
          {(r: any) => (
            <>
              <p class="muted">
                {q().get("mode") === "semantic" ? "Semantic lookup — nearest passages by meaning. These are lookup results, not findings." : "Full-text matches, best first."}{" "}
                {fmtNum(r.hits.length)} results <Epistemic cls="OBSERVED" />
              </p>
              <ul class="ev-list">
                <For each={r.hits}>
                  {(h: any) => (
                    <li class="ev-item">
                      <div class="ev-meta">
                        <span>{h.source}</span><span>{h.kind}</span><span>{fmtDate(h.occurred_at)}</span>
                        <Show when={h.distance != null}><span>distance {h.distance}</span></Show>
                        <button class="link" onClick={() => open(h)}>open</button>
                      </div>
                      <Show when={h.title && h.kind !== "event"}><div class="ev-title">{h.title}</div></Show>
                      <div class="ev-text">{String(h.snippet ?? h.text ?? "").replace(/<\/?b>/g, "")}</div>
                    </li>
                  )}
                </For>
              </ul>
            </>
          )}
        </Async>
      </Show>
    </div>
  );
}

// --- Status ---------------------------------------------------------------------------------

export function Status() {
  const [status] = createResource(() => api("/status"));
  const [cov] = createResource(() => api("/coverage"));
  const [ext] = createResource(() => api("/extraction-health"));
  return (
    <div class="view">
      <h2>Status</h2>
      <Async data={status}>
        {(s: any) => (
          <>
            <p>Schema {s.schema_version} · epoch {s.epoch?.id} ({String(s.epoch?.closed_at ?? "").slice(0, 16)}) · agent: {s.agent.mode}{s.agent.model ? ` (${s.agent.model})` : ""}</p>
            <KV value={{ counts: s.counts, extraction: s.extraction, derive_partitions: s.derive_health, maintenance: s.maintenance }} />
            <h3>Sources</h3>
            <table class="grid">
              <thead><tr><th>Source</th><th class="num">Events</th><th class="num">Undated</th><th>First</th><th>Last</th></tr></thead>
              <tbody>
                <For each={s.sources}>{(r: any) => <tr><td>{r.source}</td><td class="num">{fmtNum(r.events)}</td><td class="num">{fmtNum(r.undated)}</td><td>{fmtDate(r.first_at)}</td><td>{fmtDate(r.last_at)}</td></tr>}</For>
              </tbody>
            </table>
          </>
        )}
      </Async>
      <h3>Coverage <Epistemic cls="COMPUTED" /></h3>
      <p class="muted">Share of days where each source's export actually contains the kind of object it should (low = likely missing data, not inactivity).</p>
      <Async data={cov}>
        {(rows: any[]) => (
          <table class="grid">
            <thead><tr><th>Source</th><th>Object</th><th class="num">Days</th><th class="num">Mean completeness</th><th class="num">Low days</th><th>First</th><th>Last</th></tr></thead>
            <tbody>
              <For each={rows}>{(r) => <tr><td>{r.source}</td><td>{r.object_kind}</td><td class="num">{fmtNum(r.days)}</td><td class="num">{r.mean_completeness == null ? "—" : `${Math.round(r.mean_completeness * 100)}%`}</td><td class="num">{fmtNum(r.low_days)}</td><td>{fmtDate(r.first_day)}</td><td>{fmtDate(r.last_day)}</td></tr>}</For>
            </tbody>
          </table>
        )}
      </Async>
      <h3>Life-graph extraction <Epistemic cls="SEMANTIC" /></h3>
      <Async data={ext}>
        {(e: any) => (
          <>
            <ul class="warnings"><For each={e.notes}>{(n: string) => <li>{n}</li>}</For></ul>
            <p>Status: {Object.entries(e.by_status).map(([k, v]) => `${k} ${fmtNum(v as number)}`).join(" · ")}</p>
            <div class="dist-grid">
              <For each={Object.entries(e.distributions)}>
                {([field, vals]: any) => (
                  <div>
                    <h4>{field}</h4>
                    <table class="grid small"><tbody><For each={vals}>{(v: any) => <tr><td>{v.value}</td><td class="num">{fmtNum(v.count)}</td></tr>}</For></tbody></table>
                  </div>
                )}
              </For>
            </div>
          </>
        )}
      </Async>
    </div>
  );
}
