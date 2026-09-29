import { createResource, For, Show } from "solid-js";
import { api, qs } from "../api";
import { closeEvidence, evidenceBack, evidenceStack, fmtDate, openEvidence } from "../state";
import { Async, Epistemic, KV } from "./ui";

const stripMarks = (s: string) => s.replace(/<\/?b>/g, "");

function ItemRow(props: { item: any }) {
  const it = () => props.item;
  const target = () => {
    const i = it();
    if (i.kind === "text_unit") return () => openEvidence({ type: "text_unit", id: i.id });
    if (["event", "record", "asset"].includes(i.kind)) return () => openEvidence({ type: "canonical", kind: i.kind, id: i.id });
    if (i.kind === "conversation") return () => openEvidence({ type: "conversation", id: i.id });
    if (i.kind === "document_text_unit" && i.record_id)
      return () => openEvidence({ type: "canonical", kind: "record", id: i.record_id });
    return null;
  };
  return (
    <li class={`ev-item ${it().direction ? `dir-${it().direction}` : ""}`}>
      <div class="ev-meta">
        <Show when={it().source}>
          <span>{it().source}</span>
        </Show>
        <span>{fmtDate(it().occurred_at ?? it().started_at ?? it().month)}</span>
        <Show when={it().direction}>
          <span class="dir">{it().direction === "out" ? "you →" : "→ you"}</span>
        </Show>
        <Show when={it().kind === "metric"}>
          <span>
            in {it().messages_in} · out {it().messages_out}
          </span>
        </Show>
        <Show when={target()}>
          {(fn) => (
            <button class="link" onClick={fn()}>
              open {String(it().kind).replace(/_/g, " ")}
            </button>
          )}
        </Show>
      </div>
      <Show when={it().title && it().kind !== "event"}>
        <div class="ev-title">{it().title}</div>
      </Show>
      <Show when={it().text}>
        <div class="ev-text">{stripMarks(String(it().text))}</div>
      </Show>
      <Show when={it().kind === "claim_evidence"}>
        <KV value={{ role: it().role, table: it().source_table, cited: it().cited, refetched: it().refetched }} />
      </Show>
    </li>
  );
}

function Paged(props: { load: (cursor: number | null) => Promise<any>; row?: (it: any) => any }) {
  const [pages, { mutate }] = createResource(async () => [await props.load(null)]);
  const more = async () => {
    const ps = pages()!;
    mutate([...ps, await props.load(ps[ps.length - 1].next_cursor)]);
  };
  return (
    <Async data={pages}>
      {(ps) => (
        <>
          <Show when={ps[0].title}>
            <h3>{ps[0].title}</h3>
          </Show>
          <Show when={ps[0].total !== undefined}>
            <p class="muted">
              <Epistemic cls={ps[0].epistemic_class} /> {Number(ps[0].total).toLocaleString()} items
            </p>
          </Show>
          <ul class="ev-list">
            <For each={ps.flatMap((p: any) => p.items)}>
              {(it) => <ItemRow item={props.row ? props.row(it) : it} />}
            </For>
          </ul>
          <Show when={ps[ps.length - 1].next_cursor != null}>
            <button onClick={more}>Load more</button>
          </Show>
        </>
      )}
    </Async>
  );
}

function CanonicalView(props: { kind: string; id: number }) {
  const [data] = createResource(
    () => [props.kind, props.id] as const,
    ([k, id]) => api(`/canonical/${k}/${id}`),
  );
  return (
    <Async data={data}>
      {(d: any) => (
        <>
          <h3>
            {d.kind} {d.id} <Epistemic cls="OBSERVED" />
          </h3>
          <p class="muted">
            {d.source} · {d.event_type ?? d.record_type ?? d.mime_type ?? ""} ·{" "}
            {fmtDate(d.occurred_at ?? d.started_at ?? d.created_at)}
          </p>
          <Show when={d.actor_identity}>
            <p>From: {d.actor_identity}</p>
          </Show>
          <Show when={d.title}>
            <p>
              <b>{d.title}</b>
            </p>
          </Show>
          <Show when={d.text}>
            <pre class="ev-body">{d.text}</pre>
          </Show>
          <Show when={d.conversation_id}>
            <button class="link" onClick={() => openEvidence({ type: "conversation", id: d.conversation_id })}>
              Open the whole thread
            </button>
          </Show>
          <details>
            <summary>Provenance &amp; metadata</summary>
            <KV
              value={{
                namespace: d.source_record_namespace ?? d.asset_namespace,
                source_id: d.source_record_id ?? d.source_conversation_id ?? d.source_asset_id,
                canonical_run_id: d.canonical_run_id,
                lifecycle_state: d.lifecycle_state,
                metadata: d.metadata,
              }}
            />
          </details>
        </>
      )}
    </Async>
  );
}

function ConversationView(props: { id: number }) {
  const [conv] = createResource(() => props.id, (id) => api(`/canonical/conversation/${id}`));
  return (
    <>
      <Async data={conv}>
        {(c: any) => (
          <>
            <h3>
              Thread {c.id} <Epistemic cls="OBSERVED" />
            </h3>
            <p class="muted">
              {c.source} · {c.title ?? ""} · {c.event_count} events (oldest first)
            </p>
          </>
        )}
      </Async>
      <Paged
        load={(cursor) => api(`/canonical/conversation/${props.id}/events${qs({ cursor, limit: 200 })}`)}
        row={(it) => ({ ...it, source: it.event_type === "message.outgoing" ? "you" : it.actor_identity })}
      />
    </>
  );
}

function TextUnitView(props: { id: number }) {
  const [data] = createResource(() => props.id, (id) => api(`/text-units/${id}`));
  return (
    <Async data={data}>
      {(d: any) => (
        <>
          <h3>
            Text unit {d.id} <Epistemic cls="OBSERVED" />
          </h3>
          <p class="muted">
            {d.source} · {fmtDate(d.started_at)} → {fmtDate(d.ended_at)} · {d.event_ids?.length ?? 0} events
          </p>
          <pre class="ev-body">{d.text}</pre>
          <Show when={d.conversation_id}>
            <button class="link" onClick={() => openEvidence({ type: "conversation", id: d.conversation_id })}>
              Open the whole thread
            </button>
          </Show>
          <Show when={d.topic}>
            <p>
              Topic: <a href={`#/topics/${d.topic.cluster_index}`}>{d.topic.cluster_label}</a>{" "}
              <Epistemic cls="STATISTICAL" />
            </p>
          </Show>
          <Show when={d.extraction}>
            <details>
              <summary>
                Life-graph extraction <Epistemic cls="SEMANTIC" />
                {d.extraction.extraction_status === "truncated" ? " (incomplete — hit the generation cap)" : ""}
              </summary>
              <KV value={d.extraction} />
            </details>
          </Show>
          <Show when={d.mentions?.length}>
            <details>
              <summary>Mentions ({d.mentions.length})</summary>
              <ul>
                <For each={d.mentions}>
                  {(m: any) => (
                    <li>
                      {m.surface_text}{" "}
                      <span class="muted">
                        ({m.kind}
                        {m.resolved_kind ? ` → ${m.resolved_kind} ${m.resolved_id}` : ", unconfirmed"})
                      </span>
                    </li>
                  )}
                </For>
              </ul>
            </details>
          </Show>
        </>
      )}
    </Async>
  );
}

export function ClaimWhy(props: { id: number }) {
  const [data] = createResource(() => props.id, (id) => api(`/claims/${id}/why`));
  return (
    <Async data={data}>
      {(d: any) => (
        <>
          <h3>
            Claim {d.claim.id} <Epistemic cls={d.claim.class} />
          </h3>
          <p>{d.claim.statement}</p>
          <p class="muted">
            {d.claim.template} · epoch {d.claim.computed_epoch_id} · {d.claim.status}
          </p>
          <h4>Evidence (refetched from its source tables)</h4>
          <ul class="ev-list">
            <For each={d.evidence_detail}>{(e: any) => <ItemRow item={{ kind: "claim_evidence", ...e }} />}</For>
          </ul>
          <details>
            <summary>Confidence dimensions</summary>
            <KV value={d.claim.confidence} />
          </details>
          <details>
            <summary>Active derivation versions</summary>
            <KV value={d.deriver_version_state} />
          </details>
        </>
      )}
    </Async>
  );
}

export function EvidenceDrawer() {
  const top = () => evidenceStack()[evidenceStack().length - 1];
  const body = (t: any) => {
    switch (t.type) {
      case "handle":
        return <Paged load={(cursor) => api(`/evidence/${t.handle}${qs({ cursor, limit: 50 })}`)} />;
      case "canonical":
        return <CanonicalView kind={t.kind} id={t.id} />;
      case "conversation":
        return <ConversationView id={t.id} />;
      case "text_unit":
        return <TextUnitView id={t.id} />;
      case "claim":
        return <ClaimWhy id={t.id} />;
    }
    return null;
  };
  return (
    <Show when={top()} keyed>
      {(t) => (
        <aside class="drawer" aria-label="Evidence inspector">
          <div class="drawer-bar">
            <button onClick={evidenceBack} disabled={evidenceStack().length < 2}>
              ← Back
            </button>
            <span class="muted">Evidence</span>
            <button onClick={closeEvidence} aria-label="Close evidence">
              ✕
            </button>
          </div>
          <div class="drawer-body">{body(t)}</div>
        </aside>
      )}
    </Show>
  );
}
