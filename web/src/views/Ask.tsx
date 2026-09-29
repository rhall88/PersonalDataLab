import { createResource, For, Show } from "solid-js";
import { api } from "../api";
import { InsightView } from "../components/InsightView";
import { Async, Warnings } from "../components/ui";
import { href } from "../router";
import { ask, cancelRun, insight, lastQuestion, runError, runEvents, runStatus } from "../runs";
import { fmtNum } from "../state";

export const FIRST_QUESTION = "How have my relationships changed over the past 3 years?";

const LABELS: Record<string, (d: any) => string> = {
  "run.started": () => "Question received",
  "plan.ready": (d) => (d.mode === "agent" ? `Agent planning with ${d.model}` : `Plan: ${d.calls.map((c: any) => c.tool).join(", ") || "none"}`),
  "tool.started": (d) => `${d.label}…`,
  "tool.completed": (d) => `Done: ${d.tool} (${d.class})`,
  "answer.composing": () => "Composing the answer from tool results",
  "answer.validated": (d) => (d.rejected ? `Model answer rejected: ${d.rejected.join("; ")}` : `Validated ${d.findings} findings against evidence`),
  "run.completed": () => "Complete",
  "run.failed": (d) => `Failed: ${d.error}`,
};

export async function saveInsight(i: any) {
  const title = window.prompt("Name this investigation", i.title ?? i.question);
  if (!title) return;
  const saved = await api("/investigations", { method: "POST", body: { title, insight: i } });
  location.hash = `#/investigations/${saved.id}`;
}

export function Ask() {
  const [status] = createResource(() => api("/status"));
  const [invs] = createResource(() => api("/investigations"));
  const running = () => ["planning", "running", "composing"].includes(runStatus());
  return (
    <div class="view">
      <Async data={status}>
        {(s: any) => (
          <section class="status-strip">
            <span>
              <b>{fmtNum(s.counts.events)}</b> events · <b>{fmtNum(s.counts.text_units)}</b> message text units ·{" "}
              <b>{fmtNum(s.counts.people)}</b> people · <b>{fmtNum(s.counts.claims_active)}</b> claims ·{" "}
              <b>{fmtNum(s.counts.topic_clusters)}</b> topics · epoch {s.epoch?.id}
            </span>
            <span class="badge mode">{s.agent.mode === "agent" ? `Agent: ${s.agent.model}` : "Guided evidence mode (no model configured)"}</span>
            <a href={href("/status")}>details</a>
            <Warnings items={s.warnings} />
          </section>
        )}
      </Async>

      <Show when={!insight() && !running()}>
        <section class="welcome">
          <h2>Ask about your data</h2>
          <p>Answers come from named tools over your own database, and every finding opens to its evidence.</p>
          <button class="primary" onClick={() => ask(FIRST_QUESTION)}>{FIRST_QUESTION}</button>
          <div class="suggestions">
            <For each={["What topics did I talk about most?", "Show my stored claims", "What does my data coverage look like?"]}>
              {(q) => <button class="chip" onClick={() => ask(q)}>{q}</button>}
            </For>
          </div>
        </section>
      </Show>

      <Show when={runEvents().length || running()}>
        <section class="progress" aria-live="polite">
          <div class="progress-head">
            <b>{lastQuestion()}</b>
            <span class="muted">{runStatus()}</span>
            <Show when={running()}>
              <button onClick={cancelRun}>Cancel</button>
            </Show>
          </div>
          <ol class="steps">
            <For each={runEvents()}>{(e) => <li>{LABELS[e.type]?.(e.data) ?? e.type}</li>}</For>
          </ol>
          <Show when={runError()}>
            <div class="error" role="alert">{runError()}</div>
          </Show>
        </section>
      </Show>

      <Show when={insight()}>{(i) => <InsightView insight={i()} onSave={() => saveInsight(i())} />}</Show>

      <Async data={invs}>
        {(list: any[]) => (
          <Show when={list.length}>
            <section>
              <h3>Saved investigations</h3>
              <ul class="plain">
                <For each={list.slice(0, 8)}>
                  {(inv) => (
                    <li>
                      <a href={href(`/investigations/${inv.id}`)}>{inv.title}</a>{" "}
                      <span class="muted">{inv.revisions} revision(s) · {String(inv.updated_at).slice(0, 16)}</span>
                    </li>
                  )}
                </For>
              </ul>
            </section>
          </Show>
        )}
      </Async>
    </div>
  );
}
