import { createResource, createSignal, For, Show } from "solid-js";
import { api, qs } from "../api";
import { Async, Epistemic } from "../components/ui";
import { href, route } from "../router";
import { fmtDate, fmtNum, openEvidenceFresh } from "../state";

function Maintenance() {
  const [msg, setMsg] = createSignal("");
  const run = async () => {
    const m = await api("/maintenance/refresh-links", { method: "POST" });
    setMsg(`Re-running the name matcher and event/decision resolvers: ${m.state}. Check Status for the result.`);
  };
  return (
    <div class="filters">
      <button onClick={run}>Re-run name matching &amp; resolvers</button>
      <span class="muted">{msg() || "Do this after linking people or confirming mentions (takes seconds)."}</span>
    </div>
  );
}

function MentionReview(props: { personId: number; name: string }) {
  const [rows, { refetch }] = createResource(() => props.personId, (id) => api(`/people/${id}/mentions`));
  const [samples, setSamples] = createSignal<{ surface: string; items: any[] } | null>(null);
  const [excluded, setExcluded] = createSignal<string[]>([]);
  const [msg, setMsg] = createSignal("");
  const surfaces = () => {
    const by: Record<string, { total: number; sources: Record<string, number> }> = {};
    for (const r of (rows.latest as any[]) ?? []) {
      by[r.surface_text] ??= { total: 0, sources: {} };
      by[r.surface_text].total += r.mentions;
      by[r.surface_text].sources[r.source] = r.mentions;
    }
    return Object.entries(by).sort((a, b) => b[1].total - a[1].total);
  };
  const sources = () => [...new Set(((rows.latest as any[]) ?? []).map((r) => r.source))];
  const confirm = async (surface_texts: string[] | null) => {
    const label = surface_texts ? surface_texts.map((s) => `“${s}”`).join(", ") : "every pending name";
    if (!window.confirm(`Confirm ${label} as ${props.name}${excluded().length ? `, except in ${excluded().join(", ")}` : ""}?`)) return;
    const r = await api(`/people/${props.personId}/mentions/confirm`, {
      method: "POST", body: { surface_texts, exclude_sources: excluded().length ? excluded() : null },
    });
    setMsg(`Confirmed ${fmtNum(r.confirmed)} mentions. Re-run the resolvers (above) so event and decision threads use them.`);
    refetch();
  };
  const showSamples = async (surface: string) =>
    setSamples({ surface, items: await api(`/people/${props.personId}/mentions/samples${qs({ surface })}`) });
  return (
    <section>
      <h3>Name mentions pending confirmation <Epistemic cls="SEMANTIC" /></h3>
      <p class="muted">
        Places your messages mention a name matching {props.name}. Nothing counts until you confirm. Check samples first —
        nicknames like “Mom” can mean someone else's mom in another person's messages.
      </p>
      <Async data={rows}>
        {(_) => (
          <Show when={surfaces().length} fallback={<p class="muted">No pending mentions.</p>}>
            <div class="filters">
              <span>Exclude sources:</span>
              <For each={sources()}>
                {(s) => (
                  <label>
                    <input type="checkbox" checked={excluded().includes(s)}
                      onChange={(e) => setExcluded((x) => (e.currentTarget.checked ? [...x, s] : x.filter((y) => y !== s)))} /> {s}
                  </label>
                )}
              </For>
              <button onClick={() => confirm(null)}>Confirm all shown</button>
            </div>
            <table class="grid">
              <thead><tr><th>Name as written</th><th class="num">Mentions</th><th>By source</th><th /></tr></thead>
              <tbody>
                <For each={surfaces()}>
                  {([surface, info]) => (
                    <tr>
                      <td>{surface}</td>
                      <td class="num">{fmtNum(info.total)}</td>
                      <td class="small">{Object.entries(info.sources).map(([s, n]) => `${s} ${n}`).join(" · ")}</td>
                      <td>
                        <button class="link" onClick={() => showSamples(surface)}>samples</button>{" "}
                        <button class="link" onClick={() => confirm([surface])}>confirm</button>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
          </Show>
        )}
      </Async>
      <p class="muted">{msg()}</p>
      <Show when={samples()}>
        {(s) => (
          <div class="samples">
            <h4>Samples for “{s().surface}”</h4>
            <ul class="ev-list">
              <For each={s().items}>
                {(it) => (
                  <li class="ev-item">
                    <div class="ev-meta">
                      <span>{it.source}</span><span>{fmtDate(it.started_at)}</span>
                      <button class="link" onClick={() => openEvidenceFresh({ type: "text_unit", id: it.text_unit_id })}>open</button>
                    </div>
                    <div class="ev-text">{it.text}</div>
                  </li>
                )}
              </For>
            </ul>
          </div>
        )}
      </Show>
    </section>
  );
}

function PersonDetail(props: { id: number }) {
  const [people, { refetch }] = createResource(() => api("/people"));
  const [alias, setAlias] = createSignal("");
  const addAlias = async () => {
    if (!alias().trim()) return;
    await api(`/people/${props.id}/aliases`, { method: "POST", body: { alias: alias() } });
    setAlias("");
    refetch();
  };
  const split = async (source: string, value: string) => {
    if (!window.confirm(`Unlink ${source} “${value}” from this person? (Reversible: you can link it again.)`)) return;
    await api("/identity/split", { method: "POST", body: { source, identity_value: value } });
    refetch();
  };
  return (
    <Async data={people}>
      {(list: any[]) => {
        const p = list.find((x) => x.id === props.id);
        return (
          <Show when={p} fallback={<p class="error">No person {props.id}.</p>}>
            <section class="detail">
              <p><a href={href("/people")}>← all people</a></p>
              <h2>{p.display_name} <Epistemic cls="DECLARED" /></h2>
              <p>
                <a href={href(`/relationships/${p.key}`)}>Relationship over time →</a> · {fmtNum(p.events)} events from linked identities ·{" "}
                {fmtNum(p.mentions_confirmed)} confirmed mentions
              </p>
              <h3>Linked identities</h3>
              <table class="grid">
                <thead><tr><th>Source</th><th>Identity</th><th>Tier</th><th class="num">Events</th><th /></tr></thead>
                <tbody>
                  <For each={p.identities}>
                    {(i: any) => (
                      <tr>
                        <td>{i.source}</td><td>{i.identity_value}</td><td>{i.tier}</td><td class="num">{fmtNum(i.events)}</td>
                        <td><button class="link" onClick={() => split(i.source, i.identity_value)}>unlink</button></td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
              <h3>Aliases</h3>
              <p>{p.aliases.join(" · ") || <span class="muted">none</span>}</p>
              <div class="filters">
                <input value={alias()} onInput={(e) => setAlias(e.currentTarget.value)} placeholder="another name they go by" />
                <button onClick={addAlias}>Add alias</button>
              </div>
              <Maintenance />
              <MentionReview personId={p.id} name={p.display_name} />
            </section>
          </Show>
        );
      }}
    </Async>
  );
}

function ReviewQueue() {
  const [q, { refetch }] = createResource(() => api(`/identity/candidates${qs({ limit: 200 })}`));
  const [people] = createResource(() => api("/people"));
  const [msg, setMsg] = createSignal("");
  const confirmContact = async (p: any) => {
    if (!window.confirm(`Link ${p.matches.filter((m: any) => !m.linked_person_id).length} identities to “${p.display_name}”?`)) return;
    const r = await api(`/identity/contacts/${p.key}/confirm`, { method: "POST", body: {} });
    setMsg(`${p.display_name}: linked ${r.linked.length}, aliases +${r.aliases_added.length}${r.skipped_conflicts.length ? `, ${r.skipped_conflicts.length} skipped (already another person's)` : ""}.`);
    refetch();
  };
  const linkRaw = async (r: any, value: string) => {
    let pid = Number(value);
    if (value === "new") {
      const name = window.prompt("New person's name", r.correspondent);
      if (!name) return;
      pid = (await api("/people", { method: "POST", body: { display_name: name } })).id;
    }
    await api(`/people/${pid}/identities`, { method: "POST", body: { source: r.source, identity_value: r.correspondent } });
    setMsg(`Linked ${r.correspondent}.`);
    refetch();
  };
  return (
    <section>
      <h3>Contact-card suggestions</h3>
      <p class="muted">Your own contact cards connect names to numbers and emails. Confirming links every listed identity and records the names as aliases.</p>
      <p class="muted">{msg()}</p>
      <Async data={q}>
        {(d: any) => (
          <>
            <table class="grid">
              <thead><tr><th>Contact</th><th class="num">Events</th><th>Matches</th><th /></tr></thead>
              <tbody>
                <For each={d.contact_proposals}>
                  {(p: any) => (
                    <tr>
                      <td>{p.display_name}<Show when={p.existing_person_ids.length}><span class="muted"> (existing person)</span></Show></td>
                      <td class="num">{fmtNum(p.total_events)}</td>
                      <td class="small">
                        <For each={p.matches}>
                          {(m: any) => <div>{m.source} {m.identity_value} <span class="muted">[{m.match}, {m.events}{m.linked_person_id ? ", linked" : ""}]</span></div>}
                        </For>
                      </td>
                      <td><button onClick={() => confirmContact(p)}>Confirm</button></td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
            <h3>Unresolved correspondents (no contact card)</h3>
            <table class="grid">
              <thead><tr><th>Source</th><th>Identity</th><th class="num">Messages</th><th>Last month</th><th>Link to</th></tr></thead>
              <tbody>
                <For each={d.unresolved}>
                  {(r: any) => (
                    <tr>
                      <td>{r.source}</td>
                      <td><a href={href(`/relationships/${r.key}`)}>{r.correspondent}</a></td>
                      <td class="num">{fmtNum(r.messages)}</td>
                      <td>{fmtDate(r.last_month)}</td>
                      <td>
                        <Async data={people}>
                          {(list: any[]) => (
                            <select onChange={(e) => e.currentTarget.value && linkRaw(r, e.currentTarget.value)}>
                              <option value="">choose…</option>
                              <option value="new">+ new person</option>
                              <For each={list}>{(p) => <option value={p.id}>{p.display_name}</option>}</For>
                            </select>
                          )}
                        </Async>
                      </td>
                    </tr>
                  )}
                </For>
              </tbody>
            </table>
          </>
        )}
      </Async>
    </section>
  );
}

export function People() {
  const id = () => route().path[1];
  const tab = () => route().query.get("tab") ?? "people";
  const [people] = createResource(() => (id() ? null : true), () => api("/people"));
  return (
    <div class="view">
      <Show when={id() === undefined} fallback={<PersonDetail id={Number(id())} />}>
        <h2>People</h2>
        <div class="tabs">
          <a class={tab() === "people" ? "active" : ""} href={href("/people")}>People</a>
          <a class={tab() === "review" ? "active" : ""} href={href("/people", { tab: "review" })}>Identity review</a>
        </div>
        <Show when={tab() === "review"} fallback={
          <Async data={people}>
            {(list: any[]) => (
              <>
                <Maintenance />
                <table class="grid">
                  <thead><tr><th>Person</th><th>Identities</th><th>Aliases</th><th class="num">Events</th><th class="num">Mentions pending</th><th class="num">Confirmed</th></tr></thead>
                  <tbody>
                    <For each={list}>
                      {(p) => (
                        <tr>
                          <td><a href={href(`/people/${p.id}`)}>{p.display_name}</a></td>
                          <td class="small">{p.identities.map((i: any) => i.source).join(", ")}</td>
                          <td class="small">{p.aliases.slice(0, 4).join(" · ")}</td>
                          <td class="num">{fmtNum(p.events)}</td>
                          <td class="num">{fmtNum(p.mention_candidates)}</td>
                          <td class="num">{fmtNum(p.mentions_confirmed)}</td>
                        </tr>
                      )}
                    </For>
                  </tbody>
                </table>
              </>
            )}
          </Async>
        }>
          <ReviewQueue />
        </Show>
      </Show>
    </div>
  );
}
