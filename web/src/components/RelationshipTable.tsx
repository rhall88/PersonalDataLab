import { createSignal, For, Show } from "solid-js";
import { href } from "../router";
import { fmtNum } from "../state";
import { EvidenceButton, Sparkline } from "./ui";

const DIR_LABEL: Record<string, string> = {
  increased: "▲ increased",
  decreased: "▼ decreased",
  stable: "● steady",
  low_volume: "· low volume",
};

export function RelationshipTable(props: { data: any; windowQuery?: Record<string, string> }) {
  const [dir, setDir] = createSignal<string>("all");
  const [showUnresolved, setShowUnresolved] = createSignal(true);
  const rows = () =>
    (props.data.rows as any[]).filter(
      (r) => (dir() === "all" || r.direction === dir()) && (showUnresolved() || r.identity_status === "resolved"),
    );
  const w = () => props.data.window;
  return (
    <div>
      <div class="totals">
        <div>
          <b>{fmtNum(props.data.totals.active_correspondents)}</b> active correspondents
        </div>
        <div>
          <b>{fmtNum(props.data.totals.measurable_shifts)}</b> measurable shifts
        </div>
        <div>
          <b>{fmtNum(props.data.totals.unresolved_identities)}</b> unresolved identities
        </div>
        <div>
          <b>{fmtNum(props.data.totals.messages)}</b> messages
        </div>
      </div>
      <div class="filters">
        <label>
          Show{" "}
          <select value={dir()} onChange={(e) => setDir(e.currentTarget.value)}>
            <option value="all">all</option>
            <option value="increased">increased</option>
            <option value="decreased">decreased</option>
            <option value="stable">steady</option>
            <option value="low_volume">low volume</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={showUnresolved()} onChange={(e) => setShowUnresolved(e.currentTarget.checked)} />{" "}
          include unresolved identities
        </label>
        <span class="muted">
          Recent = last 6 complete months, compared with the 6 before (from {String(w().prior_from)})
        </span>
      </div>
      <table class="grid">
        <thead>
          <tr>
            <th>Correspondent</th>
            <th>Sources</th>
            <th class="num">Messages</th>
            <th class="num">Prior 6 mo</th>
            <th class="num">Last 6 mo</th>
            <th>Change</th>
            <th class="num" title="min(in,out)/max(in,out): 1 = perfectly balanced">Reciprocity</th>
            <th>Monthly</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <For each={rows()}>
            {(r) => (
              <tr>
                <td>
                  <a href={href(`/relationships/${r.key}`, props.windowQuery ?? {})}>{r.label}</a>
                  <Show when={r.identity_status === "unresolved"}>
                    <span class="badge unresolved" title="No confirmed person link">
                      unresolved
                    </span>
                    <Show when={r.contact_hint}>
                      <span class="muted"> contact: {r.contact_hint} (unconfirmed)</span>
                    </Show>
                  </Show>
                  <Show when={r.possible_other_source?.length}>
                    <span class="badge warn" title="An identity with the same name exists on another app">
                      also on {r.possible_other_source.join(", ")}?
                    </span>
                  </Show>
                  <Show when={r.claims?.length}>
                    <span class="badge cls-STATISTICAL">{r.claims.length} claim</span>
                  </Show>
                </td>
                <td>{r.sources.join(", ")}</td>
                <td class="num">{fmtNum(r.messages)}</td>
                <td class="num">{fmtNum(r.prior_messages)}</td>
                <td class="num">{fmtNum(r.recent_messages)}</td>
                <td class={`dir-${r.direction}`}>{DIR_LABEL[r.direction] ?? r.direction}</td>
                <td class="num">{r.reciprocity == null ? "—" : r.reciprocity.toFixed(2)}</td>
                <td>
                  <Sparkline values={r.monthly} />
                </td>
                <td>
                  <EvidenceButton evidence={r.evidence} label="Messages" title={`Messages with ${r.label}`} />
                </td>
              </tr>
            )}
          </For>
        </tbody>
      </table>
      <Show when={props.data.truncated_rows}>
        <p class="muted">{props.data.truncated_rows} more correspondents not shown.</p>
      </Show>
    </div>
  );
}
