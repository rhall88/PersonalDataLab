import { createSignal } from "solid-js";
import { api } from "./api";
import { askContext, includeText } from "./state";

export type RunEvent = { seq: number; type: string; data: any; at: string };

const [runId, setRunId] = createSignal<string | null>(null);
const [runStatus, setRunStatus] = createSignal<string>("idle");
const [runEvents, setRunEvents] = createSignal<RunEvent[]>([]);
const [insight, setInsight] = createSignal<any | null>(null);
const [runError, setRunError] = createSignal<string | null>(null);
const [lastQuestion, setLastQuestion] = createSignal<string>("");
export { runId, runStatus, runEvents, insight, runError, lastQuestion, setInsight };

const EVENT_TYPES = [
  "run.started", "plan.ready", "tool.started", "tool.completed",
  "answer.composing", "answer.validated", "run.completed", "run.failed",
];

let source: EventSource | null = null;

export async function ask(question: string): Promise<void> {
  const q = question.trim();
  if (!q) return;
  source?.close();
  setRunError(null);
  setRunEvents([]);
  setRunStatus("planning");
  setLastQuestion(q);
  let run: any;
  try {
    run = await api("/agent/runs", {
      method: "POST",
      body: { question: q, context: askContext(), include_message_text: includeText() },
    });
  } catch (e: any) {
    setRunStatus("failed");
    setRunError(e.message);
    return;
  }
  const id = run.id as string;
  setRunId(id);
  const es = new EventSource(`/api/v1/agent/runs/${id}/events`);
  source = es;
  const onEvent = async (msg: MessageEvent) => {
    if (runId() !== id) return; // a newer question superseded this run: never commit it
    const ev: RunEvent = JSON.parse(msg.data);
    setRunEvents((list) => (list.some((e) => e.seq === ev.seq) ? list : [...list, ev]));
    if (ev.type === "run.completed" || ev.type === "run.failed") {
      es.close();
      const full = await api(`/agent/runs/${id}`);
      if (runId() !== id) return;
      setRunStatus(full.status);
      if (full.result) setInsight(full.result);
      if (full.error) setRunError(full.error);
    } else {
      setRunStatus(ev.type === "answer.composing" ? "composing" : "running");
    }
  };
  for (const t of EVENT_TYPES) es.addEventListener(t, (e) => void onEvent(e as MessageEvent));
  es.onerror = () => {
    // The server closes the stream after the final event; only report a failure mid-run.
    if (runId() === id && !["complete", "failed", "cancelled"].includes(runStatus())) {
      api(`/agent/runs/${id}`).then((full) => {
        if (runId() !== id) return;
        setRunStatus(full.status);
        if (full.result) setInsight(full.result);
        if (full.error) setRunError(full.error);
      });
    }
    es.close();
  };
}

export async function cancelRun(): Promise<void> {
  const id = runId();
  if (!id) return;
  source?.close();
  await api(`/agent/runs/${id}/cancel`, { method: "POST" }).catch(() => undefined);
  setRunStatus("cancelled");
}
