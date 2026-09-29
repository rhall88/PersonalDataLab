import { createEffect, createResource, createSignal, For, Show } from "solid-js";
import { api, qs } from "../api";
import { RelationshipTable } from "../components/RelationshipTable";
import { Async, Epistemic, EvidenceButton, MonthBars, Warnings } from "../components/ui";
import { go, href, route } from "../router";
import { fmtDate, fmtMonth, fmtNum, openEvidenceFresh, setAskContext } from "../state";

const PRESETS: [string, number | null][] = [["1 year", 1], ["3 years", 3], ["5 years", 5], ["10 years", 10], ["All", null]];

function presetWindow(years: number | null) {
  const now = new Date();
  const to = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const from = years == null ? new Date(Date.UTC(2005, 0, 1)) : new Date(Date.UTC(to.getUTCFullYear() - years, to.getUTCMonth(), 1));
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

function WindowControls(props: { base: string }) {
  const q = () => route().query;
  return (
    <div class="filters">
      <span>Window:</span>
      <For each={PRESETS}>
        {([label, y]) => (
          <button class="chip" onClick={() => go(props.base, { ...Object.fromEntries(q()), ...presetWindow(y) })}>
            {label}
          </button>
        )}
      </For>
      <label>
        from <input type="month" value={(q().get("from") ?? "").slice(0, 7)} onChange={(e) => go(props.base, { ...Object.fromEntries(q()), from: `${e.currentTarget.value}-01` })} />
      </label>
      <label>
        to <input type="month" value={(q().get("to") ?? "").slice(0, 7)} onChange={(e) => go(props.base, { ...Object.fromEntries(q()), to: `${e.currentTarget.value}-01` })} />
      </label>
      <label>
        source{" "}
        <select value={q().get("source") ?? ""} onChange={(e) => go(props.base, { ...Object.fromEntries(q()), source: e.currentTarget.value })}>
          <option value="">all</option>
          <option value="imessages">iMessage</option>
          <option value="facebook">Facebook</option>
        </select>
      </label>
      <span class="muted">(“to” is exclusive; the current month is excluded by default)</span>
    </div>
  );
}

function LinkIdentity(props: { identity: { source: string; value: string }; hint?: string | null }) {
  const [people] = createResource(() => api("/people"));
  const [pid, setPid] = createSignal("");
  const [msg, setMsg] = createSignal("");
  const link = async () => {
    try {
      let personId = Number(pid());
      if (pid() === "new") {
        const name = window.prompt("New person's name", props.hint ?? props.identity.value);
        if (!name) return;
        personId = (await api("/people", { method: "POST", body: { display_name: name } })).id;
      }
      await api(`/people/${personId}/identities`, { method: "POST", body: { source: props.identity.source, identity_value: props.identity.value } });
      setMsg("Linked. Reload the list to see it merged under the person.");
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  return (
    <div class="filters">
      <span>Link {props.identity.source} “{props.identity.value}” to:</span>
      <Async data={people}>
        {(list: any[]) => (
          <select value={pid()} onChange={(e) => setPid(e.currentTarget.value)}>
            <option value="">choose…</option>
            <option value="new">+ new person{props.hint ? ` (${props.hint})` : ""}</option>
            <For each={list}>{(p) => <option value={p.id}>{p.display_name}</option>}</For>
          </select>
        )}
      </Async>
      <button disabled={!pid()} onClick={link}>Link</button>
      <span class="muted">{msg()}</span>
    </div>
  );
}

function Detail(props: { relKey: string }) {
  const params = () => ({ from: route().query.get("from") ?? undefined, to: route().query.get("to") ?? undefined });
  const [data] = createResource(() => [props.relKey, params()] as const, ([k, p]) => api(`/relationships/${k}${qs(p)}`));
  createEffect(() => {
    const d: any = data.latest;
    if (d?.data?.row) {
      setAskContext({ relationship_key: props.relKey, label: d.data.row.label, from: String(d.data.window.from), to: String(d.data.window.to) });
    }
  });
  return (
    <Async data={data}>
      {(d: any) => {
        const row = d.data.row;
        const t = d.data.timeline;
        const sum = (k: "in" | "out") =>
          t.months.map((_: string, i: number) => Object.values(t.series).reduce((a: number, s: any) => a + s[k][i], 0));
        return (
          <section class="detail">
            <p><a href={href("/relationships", Object.fromEntries(route().query))}>← all relationships</a></p>
            <h2>
              {t.label} <Epistemic cls="COMPUTED" />
              <Show when={!t.person_id}><span class="badge unresolved">unresolved identity</span></Show>
            </h2>
            <Show when={row} fallback={<p class="muted">No measured one-to-one messages in this window.</p>}>
              <p>
                {fmtNum(row.messages)} messages ({fmtNum(row.messages_out)} from you, {fmtNum(row.messages_in)} from them) ·
                last 6 months {fmtNum(row.recent_messages)} vs {fmtNum(row.prior_messages)} before ({row.direction.replace("_", " ")}) ·
                reciprocity {row.reciprocity?.toFixed(2) ?? "—"} · you started {row.initiation_share == null ? "—" : `${Math.round(row.initiation_share * 100)}%`} of exchanges
              </p>
              <p>
                <EvidenceButton evidence={d.evidence} label="Open messages" title={`Messages with ${t.label}`} />{" "}
                <EvidenceButton evidence={row.metrics_evidence} label="Monthly metric rows" />
              </p>
              <Show when={row.contact_hint}>
                <p class="muted">Your contacts call this “{row.contact_hint}” (not yet confirmed).</p>
              </Show>
            </Show>
            <h3>Monthly messages</h3>
            <MonthBars months={t.months} a={sum("out")} b={sum("in")} aLabel="from you" bLabel="from them" height={140} />
            <Show when={Object.keys(t.series).length > 1}>
              <p class="muted">Sources combined: {Object.keys(t.series).join(" + ")} (linked identities of one person).</p>
            </Show>
            <Show when={row?.states?.length}>
              <h3>Interaction states <Epistemic cls="STATISTICAL" /></h3>
              <table class="grid">
                <thead><tr><th>From</th><th>To</th><th>Source</th><th class="num">Avg in/mo</th><th class="num">Avg out/mo</th><th class="num">Reciprocity</th></tr></thead>
                <tbody>
                  <For each={row.states}>
                    {(s: any) => (
                      <tr>
                        <td>{fmtDate(s.state_start)}</td><td>{fmtDate(s.state_end)}</td><td>{s.source}</td>
                        <td class="num">{Number(s.mean_messages_in).toFixed(0)}</td><td class="num">{Number(s.mean_messages_out).toFixed(0)}</td>
                        <td class="num">{Number(s.mean_reciprocity_index).toFixed(2)}</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </Show>
            <Show when={row?.claims?.length}>
              <h3>Claims</h3>
              <ul class="plain">
                <For each={row.claims}>
                  {(c: any) => (
                    <li>
                      <Epistemic cls={c.class} /> {c.statement}{" "}
                      <button class="link" onClick={() => openEvidenceFresh({ type: "claim", id: c.id })}>Why?</button>
                    </li>
                  )}
                </For>
              </ul>
            </Show>
            <Show when={!t.person_id && row}>
              <h3>Identity</h3>
              <LinkIdentity identity={row.identities[0]} hint={row.contact_hint} />
            </Show>
            <Show when={t.person_id}>
              <p><a href={href(`/people/${t.person_id}`)}>Person record, identities &amp; aliases →</a></p>
            </Show>
            <p class="muted small">Follow-up questions asked from the bar above now carry this relationship and window ({fmtMonth(String(d.data.window.from))} – {fmtMonth(String(d.data.window.to))}).</p>
          </section>
        );
      }}
    </Async>
  );
}

export function Relationships() {
  const relKey = () => route().path[1];
  const params = () => ({ from: route().query.get("from") ?? undefined, to: route().query.get("to") ?? undefined, source: route().query.get("source") ?? undefined });
  const [list] = createResource(() => (relKey() ? null : params()), (p) => api(`/relationships${qs({ ...p, limit: 1000 })}`));
  return (
    <div class="view">
      <h2>Relationships</h2>
      <p class="muted">
        Measured one-to-one Facebook and iMessage threads. Volume measures contact, not closeness. People merge across apps only through
        confirmed identity links.
      </p>
      <WindowControls base={relKey() ? `/relationships/${relKey()}` : "/relationships"} />
      <Show when={relKey()} fallback={
        <Async data={list}>
          {(d: any) => (
            <>
              <Warnings items={[`Window: ${fmtMonth(String(d.window.from))} – ${fmtMonth(String(d.window.to))} (exclusive)`]} />
              <RelationshipTable data={d} windowQuery={{ from: String(d.window.from), to: String(d.window.to) }} />
            </>
          )}
        </Async>
      }>
        <Detail relKey={relKey()!} />
      </Show>
    </div>
  );
}
