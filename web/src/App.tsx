import { createResource, createSignal, For, JSX, Match, onCleanup, onMount, Show, Switch } from "solid-js";
import { api } from "./api";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { go, href, route } from "./router";
import { ask } from "./runs";
import { askContext, closeEvidence, evidenceStack, includeText, setAskContext, setIncludeText } from "./state";
import { Ask } from "./views/Ask";
import { Claims, Search, Status, Topics } from "./views/Explore";
import { Investigations } from "./views/Investigations";
import { People } from "./views/People";
import { Relationships } from "./views/Relationships";

const NAV: [string, string][] = [
  ["ask", "Ask"],
  ["relationships", "Relationships"],
  ["topics", "Topics"],
  ["claims", "Claims"],
  ["search", "Search"],
  ["people", "People"],
  ["investigations", "Investigations"],
  ["status", "Status"],
];

function Composer() {
  const [q, setQ] = createSignal("");
  const [status] = createResource(() => api("/status").catch(() => null));
  let input!: HTMLInputElement;
  const submit = (e: Event) => {
    e.preventDefault();
    if (!q().trim()) return;
    ask(q());
    setQ("");
    if (route().path[0] !== "ask") go("/ask");
  };
  const onKey = (e: KeyboardEvent) => {
    const t = e.target as HTMLElement;
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
    if (e.key === "/" && !typing) {
      e.preventDefault();
      input.focus();
    }
    if (e.key === "Escape" && evidenceStack().length) closeEvidence();
  };
  onMount(() => window.addEventListener("keydown", onKey));
  onCleanup(() => window.removeEventListener("keydown", onKey));
  const agentMode = () => (status.latest as any)?.agent?.mode === "agent";
  return (
    <form class="composer" onSubmit={submit}>
      <input
        ref={input}
        value={q()}
        onInput={(e) => setQ(e.currentTarget.value)}
        placeholder="Ask about your data…  (press / to focus)"
        aria-label="Ask a question"
      />
      <Show when={askContext().relationship_key}>
        <span class="chip ctx" title="Follow-up questions use this relationship and window">
          about {askContext().label}
          <button type="button" class="link" aria-label="Clear context" onClick={() => setAskContext({})}>✕</button>
        </span>
      </Show>
      <Show when={agentMode()}>
        <label class="small" title="Off: the model sees numbers, claims, and labels only. On: it can also read message text for this question.">
          <input type="checkbox" checked={includeText()} onChange={(e) => setIncludeText(e.currentTarget.checked)} /> let the model read message text
        </label>
      </Show>
      <button type="submit" class="primary">Ask</button>
    </form>
  );
}

export function App(): JSX.Element {
  const section = () => route().path[0] ?? "ask";
  return (
    <div class={`shell ${evidenceStack().length ? "with-drawer" : ""}`}>
      <nav class="nav" aria-label="Main">
        <div class="brand">PersonalDataLab</div>
        <For each={NAV}>
          {([key, label]) => (
            <a href={href(`/${key}`)} class={section() === key ? "active" : ""}>
              {label}
            </a>
          )}
        </For>
      </nav>
      <main class="main">
        <Composer />
        <Switch fallback={<Ask />}>
          <Match when={section() === "relationships"}><Relationships /></Match>
          <Match when={section() === "topics"}><Topics /></Match>
          <Match when={section() === "claims"}><Claims /></Match>
          <Match when={section() === "search"}><Search /></Match>
          <Match when={section() === "people"}><People /></Match>
          <Match when={section() === "investigations"}><Investigations /></Match>
          <Match when={section() === "status"}><Status /></Match>
        </Switch>
      </main>
      <EvidenceDrawer />
    </div>
  );
}
