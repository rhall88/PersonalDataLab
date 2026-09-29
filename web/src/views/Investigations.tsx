import { createResource, createSignal, For, Show } from "solid-js";
import { api } from "../api";
import { InsightView } from "../components/InsightView";
import { Async, KV } from "../components/ui";
import { href, route } from "../router";
import { fmtNum } from "../state";

function Diff(props: { diff: any }) {
  const d = () => props.diff;
  return (
    <div class="diff">
      <Show when={d().unchanged}>
        <p><b>Unchanged.</b> The current data gives the same answer and the same evidence.</p>
      </Show>
      <Show when={!d().unchanged}>
        <ul>
          <Show when={d().objects_added.length}><li><b>Added:</b> {d().objects_added.length} objects</li></Show>
          <Show when={d().objects_removed.length}><li><b>Removed:</b> {d().objects_removed.length} objects</li></Show>
          <Show when={d().objects_changed.length}>
            <li>
              <b>Changed:</b> {d().objects_changed.length} objects
              <ul class="small">
                <For each={d().objects_changed.slice(0, 20)}>
                  {(c: any) => <li>{c.after.label ?? c.key}: {JSON.stringify(c.before)} → {JSON.stringify(c.after)}</li>}
                </For>
              </ul>
            </li>
          </Show>
          <Show when={d().evidence_changes.length}>
            <li><b>Evidence changed</b> for {d().evidence_changes.length} findings</li>
          </Show>
          <Show when={Object.keys(d().derivation_changed).length}>
            <li><b>Derivation changed:</b> {Object.keys(d().derivation_changed).join(", ")}</li>
          </Show>
          <Show when={d().answer_shape_changed}><li><b>The answer's shape changed.</b></li></Show>
        </ul>
        <details><summary>Full diff</summary><KV value={d()} /></details>
      </Show>
    </div>
  );
}

function Detail(props: { id: number }) {
  const [inv, { refetch }] = createResource(() => props.id, (id) => api(`/investigations/${id}`));
  const [rev, setRev] = createSignal<number | null>(null);
  const [refresh, setRefresh] = createSignal<any>(null);
  const [busy, setBusy] = createSignal(false);
  const doRefresh = async () => {
    setBusy(true);
    try {
      setRefresh(await api(`/investigations/${props.id}/refresh`, { method: "POST" }));
    } finally {
      setBusy(false);
    }
  };
  const saveRevision = async () => {
    await api(`/investigations/${props.id}/revisions`, { method: "POST", body: { insight: refresh().current } });
    setRefresh(null);
    setRev(null);
    refetch();
  };
  return (
    <Async data={inv}>
      {(d: any) => {
        const shown = () => d.revisions.find((r: any) => r.revision === (rev() ?? d.revisions[d.revisions.length - 1].revision));
        return (
          <section class="detail">
            <p><a href={href("/investigations")}>← all investigations</a></p>
            <h2>{d.title}</h2>
            <p class="muted">“{d.question}” · saved {String(d.created_at).slice(0, 16)}</p>
            <div class="filters">
              <label>
                revision{" "}
                <select value={shown()?.revision} onChange={(e) => setRev(Number(e.currentTarget.value))}>
                  <For each={d.revisions}>{(r: any) => <option value={r.revision}>{r.revision} — {String(r.created_at).slice(0, 16)} (epoch {r.epoch_id})</option>}</For>
                </select>
              </label>
              <button onClick={doRefresh} disabled={busy()}>{busy() ? "Re-running…" : "Compare with current data"}</button>
            </div>
            <Show when={refresh()}>
              {(r) => (
                <section class="refresh">
                  <h3>Current data vs revision {r().based_on_revision}</h3>
                  <Diff diff={r().diff} />
                  <Show when={!r().diff.unchanged}>
                    <button class="primary" onClick={saveRevision}>Save current answer as revision {r().based_on_revision + 1}</button>
                  </Show>
                  <details>
                    <summary>Current answer</summary>
                    <InsightView insight={r().current} />
                  </details>
                </section>
              )}
            </Show>
            <h3>Saved answer (revision {shown()?.revision})</h3>
            <p class="muted small">
              Snapshot: {fmtNum(Object.keys(shown()?.evidence_snapshot?.objects ?? {}).length)} objects,{" "}
              {fmtNum(shown()?.evidence_snapshot?.evidence?.length ?? 0)} evidence references. Shown exactly as saved.
            </p>
            <Show when={shown()}>{(s) => <InsightView insight={s().insight} />}</Show>
          </section>
        );
      }}
    </Async>
  );
}

export function Investigations() {
  const id = () => route().path[1];
  const [list] = createResource(() => (id() ? null : true), () => api("/investigations"));
  return (
    <div class="view">
      <Show when={id() === undefined} fallback={<Detail id={Number(id())} />}>
        <h2>Saved investigations</h2>
        <p class="muted">Save any answer from Ask. Reopening shows it exactly as saved; “Compare with current data” re-runs it and shows what changed.</p>
        <Async data={list}>
          {(items: any[]) => (
            <Show when={items.length} fallback={<p class="muted">Nothing saved yet.</p>}>
              <table class="grid">
                <thead><tr><th>Title</th><th>Question</th><th class="num">Revisions</th><th>Updated</th></tr></thead>
                <tbody>
                  <For each={items}>
                    {(i) => (
                      <tr>
                        <td><a href={href(`/investigations/${i.id}`)}>{i.title}</a></td>
                        <td>{i.question}</td>
                        <td class="num">{i.revisions}</td>
                        <td>{String(i.updated_at).slice(0, 16)}</td>
                      </tr>
                    )}
                  </For>
                </tbody>
              </table>
            </Show>
          )}
        </Async>
      </Show>
    </div>
  );
}
