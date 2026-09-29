import { For, JSX, Match, Resource, Show, Switch } from "solid-js";
import { openEvidenceFresh } from "../state";

const CLASS_HELP: Record<string, string> = {
  OBSERVED: "Directly from your exported data",
  COMPUTED: "Calculated from your data by fixed rules",
  STATISTICAL: "A statistical pattern (clustering, change detection)",
  SEMANTIC: "Model-generated interpretation — may be wrong",
  DECLARED: "Something you confirmed yourself",
  HYPOTHESIS: "A tentative hypothesis",
};

export function Epistemic(props: { cls?: string | null }) {
  return (
    <Show when={props.cls}>
      <span class={`badge cls-${props.cls}`} title={CLASS_HELP[props.cls!] ?? ""}>
        {props.cls}
      </span>
    </Show>
  );
}

export function EvidenceButton(props: { evidence?: any; label?: string; title?: string }) {
  return (
    <Show when={props.evidence?.query_handle}>
      <button
        class="link"
        onClick={() => openEvidenceFresh({ type: "handle", handle: props.evidence.query_handle, title: props.title })}
      >
        {props.label ?? "Evidence"} ({Number(props.evidence.count ?? 0).toLocaleString()})
      </button>
    </Show>
  );
}

export function Async<T>(props: { data: Resource<T>; children: (v: T) => JSX.Element }) {
  return (
    <Switch>
      <Match when={props.data.error}>
        <div class="error" role="alert">
          {String(props.data.error?.message ?? props.data.error)}
        </div>
      </Match>
      <Match when={props.data.latest === undefined}>
        <div class="muted">Loading…</div>
      </Match>
      <Match when={props.data.latest !== undefined}>{props.children(props.data.latest as T)}</Match>
    </Switch>
  );
}

export function Sparkline(props: { values: number[]; width?: number; height?: number }) {
  const w = () => props.width ?? 120;
  const h = () => props.height ?? 22;
  const pts = () => {
    const v = props.values;
    const max = Math.max(1, ...v);
    const step = v.length > 1 ? w() / (v.length - 1) : 0;
    return v.map((y, i) => `${(i * step).toFixed(1)},${(h() - 1 - (y / max) * (h() - 2)).toFixed(1)}`).join(" ");
  };
  return (
    <svg width={w()} height={h()} class="spark" aria-hidden="true">
      <polyline points={pts()} fill="none" stroke="currentColor" stroke-width="1.5" />
    </svg>
  );
}

// Monthly stacked bars (a below, b above) with a month label every few ticks.
export function MonthBars(props: {
  months: string[];
  a: number[];
  b?: number[];
  aLabel?: string;
  bLabel?: string;
  height?: number;
}) {
  const h = () => props.height ?? 120;
  const n = () => props.months.length;
  const bw = () => Math.max(4, Math.min(24, Math.floor(760 / Math.max(1, n()))));
  const max = () => Math.max(1, ...props.months.map((_, i) => (props.a[i] ?? 0) + (props.b?.[i] ?? 0)));
  const every = () => Math.max(1, Math.ceil(n() / 12));
  return (
    <div class="bars-wrap">
      <svg width={Math.max(40, n() * bw())} height={h() + 18} role="img" aria-label="Monthly counts">
        <For each={props.months}>
          {(m, i) => {
            const a = () => props.a[i()] ?? 0;
            const b = () => props.b?.[i()] ?? 0;
            const ha = () => (a() / max()) * h();
            const hb = () => (b() / max()) * h();
            return (
              <g>
                <title>{`${m.slice(0, 7)}: ${props.aLabel ?? "count"} ${a()}${props.b ? `, ${props.bLabel ?? ""} ${b()}` : ""}`}</title>
                <rect x={i() * bw()} y={h() - ha()} width={bw() - 1} height={ha()} class="bar-a" />
                <rect x={i() * bw()} y={h() - ha() - hb()} width={bw() - 1} height={hb()} class="bar-b" />
                <Show when={i() % every() === 0}>
                  <text x={i() * bw()} y={h() + 13} class="tick">
                    {m.slice(2, 7)}
                  </text>
                </Show>
              </g>
            );
          }}
        </For>
      </svg>
      <Show when={props.b}>
        <div class="legend">
          <span class="sw bar-a" /> {props.aLabel} <span class="sw bar-b" /> {props.bLabel}
        </div>
      </Show>
    </div>
  );
}

export function Warnings(props: { items?: string[] }) {
  return (
    <Show when={props.items?.length}>
      <ul class="warnings">
        <For each={props.items}>{(w) => <li>{w}</li>}</For>
      </ul>
    </Show>
  );
}

// Readable key/value rendering for arbitrary JSON (claim evidence, metadata).
export function KV(props: { value: any; depth?: number }): JSX.Element {
  const d = () => props.depth ?? 0;
  return (
    <Switch fallback={<span>{String(props.value)}</span>}>
      <Match when={props.value === null || props.value === undefined}>
        <span class="muted">—</span>
      </Match>
      <Match when={Array.isArray(props.value)}>
        <Show when={props.value.length} fallback={<span class="muted">[]</span>}>
          <ol class="kv-list">
            <For each={props.value.slice(0, 50)}>
              {(x) => (
                <li>
                  <KV value={x} depth={d() + 1} />
                </li>
              )}
            </For>
          </ol>
        </Show>
      </Match>
      <Match when={typeof props.value === "object"}>
        <dl class="kv">
          <For each={Object.entries(props.value)}>
            {([k, x]) => (
              <>
                <dt>{k}</dt>
                <dd>
                  {d() > 3 ? <code>{JSON.stringify(x).slice(0, 300)}</code> : <KV value={x} depth={d() + 1} />}
                </dd>
              </>
            )}
          </For>
        </dl>
      </Match>
    </Switch>
  );
}
