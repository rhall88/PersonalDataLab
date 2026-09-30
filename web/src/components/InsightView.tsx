import { For, Match, Show, Switch } from "solid-js";
import { href } from "../router";
import { ask } from "../runs";
import { fmtDate, fmtNum, openEvidenceFresh } from "../state";
import { RelationshipTable } from "./RelationshipTable";
import { Epistemic, EvidenceButton, KV, MonthBars, Warnings } from "./ui";

function View(props: { view: any }) {
  const v = () => props.view;
  return (
    <Switch fallback={<KV value={v().data} />}>
      <Match when={v().type === "relationship_table"}>
        <RelationshipTable
          data={v().data}
          windowQuery={{ from: String(v().data.window.from), to: String(v().data.window.to) }}
        />
      </Match>
      <Match when={v().type === "relationship_detail"}>
        <Show when={v().data.timeline}>
          <h4>Monthly messages</h4>
          <MonthBars
            months={v().data.timeline.months}
            a={Object.values(v().data.timeline.series).reduce((acc: number[], s: any) => s.out.map((x: number, i: number) => x + (acc[i] ?? 0)), [])}
            b={Object.values(v().data.timeline.series).reduce((acc: number[], s: any) => s.in.map((x: number, i: number) => x + (acc[i] ?? 0)), [])}
            aLabel="from you"
            bLabel="from them"
          />
          <p>
            <a href={href(`/relationships/${v().data.timeline.key}`)}>Open in the relationship explorer →</a>
          </p>
        </Show>
      </Match>
      <Match when={v().type === "topic_list"}>
        <table class="grid">
          <thead>
            <tr><th>Topic</th><th>Keywords (machine-generated)</th><th class="num">Size</th><th>Span</th><th /></tr>
          </thead>
          <tbody>
            <For each={v().data.topics}>
              {(t: any) => (
                <tr>
                  <td><a href={href(`/topics/${t.cluster_index}`)}>#{t.cluster_index}</a></td>
                  <td>{t.cluster_label}</td>
                  <td class="num">{fmtNum(t.size)}</td>
                  <td>{fmtDate(t.first_at)} – {fmtDate(t.last_at)}</td>
                  <td><EvidenceButton evidence={t.evidence} label="Units" /></td>
                </tr>
              )}
            </For>
          </tbody>
        </table>
      </Match>
      <Match when={v().type === "claim_list"}>
        <ul class="plain">
          <For each={v().data.claims}>
            {(c: any) => (
              <li>
                <Epistemic cls={c.class} /> {c.statement}{" "}
                <button class="link" onClick={() => openEvidenceFresh({ type: "claim", id: c.id })}>Why?</button>
              </li>
            )}
          </For>
        </ul>
      </Match>
      <Match when={v().type === "claim_why"}>
        <button class="link" onClick={() => openEvidenceFresh({ type: "claim", id: v().data.claim.id })}>
          Open the evidence chain
        </button>
      </Match>
      <Match when={v().type === "search_hits"}>
        <h4>
          {v().data.mode === "semantic" ? "Semantic lookup (lookup results, not findings)" : "Full-text matches"}
        </h4>
        <ul class="ev-list">
          <For each={v().data.hits}>
            {(h: any) => (
              <li class="ev-item">
                <div class="ev-meta">
                  <span>{h.source}</span>
                  <span>{fmtDate(h.occurred_at)}</span>
                  <span>{h.kind}</span>
                  <button
                    class="link"
                    onClick={() =>
                      h.kind === "text_unit"
                        ? openEvidenceFresh({ type: "text_unit", id: h.id })
                        : h.kind === "conversation"
                          ? openEvidenceFresh({ type: "conversation", id: h.id })
                          : h.kind === "document_text_unit"
                            ? h.record_id
                              ? openEvidenceFresh({ type: "canonical", kind: "record", id: h.record_id })
                              : h.asset_id && openEvidenceFresh({ type: "canonical", kind: "asset", id: h.asset_id })
                            : openEvidenceFresh({ type: "canonical", kind: h.kind, id: h.id })
                    }
                  >
                    open
                  </button>
                </div>
                <div class="ev-text">{String(h.snippet ?? h.text ?? h.title ?? "").replace(/<\/?b>/g, "")}</div>
              </li>
            )}
          </For>
        </ul>
      </Match>
    </Switch>
  );
}

export function InsightView(props: { insight: any; onSave?: () => void }) {
  const i = () => props.insight;
  return (
    <section class="insight">
      <div class="insight-head">
        <h2>{i().title}</h2>
        <span class="badge mode">
          {i().mode === "agent" ? `AI agent (${i().model})` : "Guided evidence mode"}
        </span>
        <Epistemic cls={i().epistemic?.class} />
        <Show when={props.onSave}>
          <button onClick={props.onSave}>Save investigation</button>
        </Show>
      </div>
      <p class="summary">{i().summary}</p>
      <Show when={i().message_text_shared}>
        <p class="muted">Message text was shared with the model for this answer.</p>
      </Show>
      <Warnings items={i().warnings} />
      <Show when={i().findings?.length}>
        <h3>Findings</h3>
        <ul class="findings">
          <For each={i().findings}>
            {(f: any) => (
              <li>
                <Epistemic cls={f.epistemic_class} /> {f.text}{" "}
                <EvidenceButton evidence={f.evidence} />
              </li>
            )}
          </For>
        </ul>
      </Show>
      <For each={i().views}>{(v) => <View view={v} />}</For>
      <Show when={i().suggestions?.length}>
        <div class="suggestions">
          <span class="muted">Try:</span>
          <For each={i().suggestions}>{(s: string) => <button class="chip" onClick={() => ask(s)}>{s}</button>}</For>
        </div>
      </Show>
      <p class="muted small">
        Computed at epoch {i().computed_epoch_id ?? "—"} · tools {i().tool_version} · plan:{" "}
        {(i().plan ?? []).map((c: any) => c.tool).join(", ") || "none"}
      </p>
    </section>
  );
}
