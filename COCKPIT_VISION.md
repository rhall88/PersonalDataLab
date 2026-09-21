# PersonalDataLab: Private Adaptive Data Cockpit

This is an end-state product vision for a future frontend/UI layer on top of PersonalDataLab. It is
authored by the project owner (Ryan) as a north-star for where the project is eventually headed —
**it is not a current implementation target**. Nothing in this repository today implements any part
of this vision; the repository is presently a backend-only, provenance-first ingestion and canonical
database (see [`PROJECT_STATE.md`](PROJECT_STATE.md) and [`ROADMAP.md`](ROADMAP.md) for what
actually exists and what's being built next).

This document exists so the vision survives across sessions and isn't lost to chat history. It is
recorded verbatim as given, not summarized, to preserve its full intent and detail for whoever
(human or LLM) eventually picks up frontend/cockpit work.

**Where this fits today's roadmap**: `ROADMAP.md` section 3 ("Build query and understanding
layers") and section 4 ("Build user and agent experiences") are the backend foundation this vision
depends on. In this document's own "Architectural Separation" terms (section 18 below), the
ingestion/canonical work completed so far (Anthropic, OpenAI, iMessages, Apple, Google, Facebook,
all in one canonical database) is the **Canonical Ingestion** layer; the unified full-text query
work is the first piece of the **Analytics Layer**. Everything from **Semantic/Entity Layer**
onward — entity resolution, embeddings, InsightSpec/VizSpec/LayoutSpec, and the adaptive cockpit
itself — is future work this vision describes, not yet started.

---

## 1. Product Vision

Build a private, local-first visual intelligence interface for PersonalDataLab (PDL).

This is not primarily a dashboard.

It is a personal data cockpit whose interface changes shape according to the question being asked.

The user should be able to ask natural-language questions about their own life, behavior,
communications, work, projects, relationships, habits, locations, media, finances where available,
creative work, and digital history.

PDL should determine:

- what data is relevant
- which sources are trustworthy
- what analytical method is appropriate
- what visual representation best answers the question
- what supporting evidence should remain inspectable
- what uncertainty or missing data matters
- how the interface should arrange itself around the result

The long-term experience should feel less like opening Tableau and more like entering a visual
command center for one human life.

The application is initially built only for Ryan. Do not optimize this version for public users,
multi-tenancy, SEO, generic onboarding, social sharing, or internet exposure. Optimize it for
depth, speed, visual quality, trust, exploration, and usefulness to one technically sophisticated
user with a large personal-data corpus.

## 2. Core Principle: The Question Determines the Interface

Traditional dashboards require the designer to predict the questions beforehand.

PDL should work in the opposite direction.

The user asks: "Who have I communicated with most over the last five years, and how has that
changed?"

The system may construct:

- a relationship network
- a longitudinal stacked timeline
- interaction-volume sparklines
- a selected-person detail drawer
- notable inflection points
- source coverage
- supporting messages/events

Another question: "Show me the major eras of my career."

The UI may become:

- a horizontal life timeline
- job/project bands
- technologies used by period
- GitHub activity
- documents and communications
- inferred transition points
- supporting evidence cards

Another: "What was happening in my life when I was making the most art?"

The UI may become:

- an art gallery
- creation timeline
- life-event overlays
- communication/activity patterns
- geographic context
- clusters of related work

Another: "What do I spend the most time talking about?"

The interface may become:

- semantic topic landscape
- topic clusters
- time-series evolution
- people associated with each theme
- representative conversations
- source filters

The core abstraction should therefore be:

Question → Analysis Plan → Evidence → Insight Model → Visualization Model → Adaptive Workspace

The UI should not merely insert different charts into a fixed dashboard grid. The workspace itself
should be capable of changing composition.

## 3. Experience Goal

The application should feel like a hybrid of:

- mission-control cockpit
- personal research laboratory
- interactive documentary
- knowledge graph explorer
- AI research assistant
- visual analytics system

It should feel visually sophisticated without becoming decorative nonsense. Every visual element
should exist because it improves understanding.

The user should frequently experience: "Holy shit, I did not know my data could show me that."

## 4. Interaction Model

### Persistent Shell

A small portion of the interface remains stable:

- global search / question input
- conversation/history
- date-range control
- source controls
- global entity search
- saved investigations
- provenance/evidence toggle
- command palette
- system/data-health status

Everything else may transform.

### Adaptive Workspace

The main workspace is dynamically composed for the current question. Possible modules include:

- timeline
- graph
- map
- heatmap
- scatterplot
- sankey
- treemap
- matrix
- calendar
- media gallery
- semantic cluster visualization
- ranked table
- document/message viewer
- comparison panel
- statistical summary
- narrative insight
- event sequence
- correlation explorer
- entity profile
- source/provenance inspector

Modules should be composable. The system may generate one dominant visualization plus supporting
views rather than fifteen equally weighted widgets.

## 5. Analytical Contract

The LLM must not directly invent arbitrary frontend UI. Introduce structured intermediate
representations.

### QueryPlan

Describes what the system intends to investigate. Example concepts:

- interpreted user question
- entities
- time range
- source requirements
- filters
- metrics
- transformations
- comparisons
- analytical methods
- confidence requirements

### InsightSpec

Represents analytical findings independent of presentation. Contains:

- claims
- metric values
- entities
- trends
- anomalies
- relationships
- uncertainty
- provenance references
- supporting records
- caveats

### VizSpec

Defines how an InsightSpec should be visualized. Possible fields:

- visualization type
- dimensions
- measures
- encodings
- grouping
- ordering
- annotations
- interaction behaviors
- drill-down targets

### LayoutSpec

Defines how multiple visualizations form a coherent workspace. Examples:

- hero visualization
- supporting panels
- evidence panel
- contextual sidebar
- comparison layout
- exploration layout
- chronological layout

The frontend should render these deterministic specifications. The model proposes structure. The
application owns rendering. This prevents the product from becoming an LLM-generated HTML
hallucination machine.

## 6. Evidence-First Design

Every meaningful conclusion should be inspectable. The user should be able to move from:

Insight → visualization → underlying records → original source

Example: "Communication with Person X declined sharply after March 2024."

Clicking the claim should reveal:

- chart segment
- counts
- comparison period
- messages/events contributing to the metric
- original source
- importer
- canonical record ID
- source file/export
- import timestamp

PDL already places strong importance on provenance. The UI should turn that backend strength into
a visible product advantage. Never make provenance feel like an audit screen hidden in developer
settings. Make it an integral part of exploration.

## 7. Major Exploration Modes

These should not necessarily exist as static pages. They are conceptual capabilities that the
adaptive workspace may invoke.

### Timeline

Explore life chronologically. Possible layers:

- communication
- jobs
- projects
- GitHub
- travel
- photos
- documents
- purchases
- music
- search activity
- major relationships
- creative work
- health/wellness data where intentionally included

Support zooming from decades to individual days.

### People / Relationship Graph

Nodes represent people. Edges may represent:

- messages
- calls
- co-occurrence
- shared locations
- projects
- frequency
- recency
- duration

Allow temporal playback so the network changes over time.

### Semantic Universe

Cluster:

- conversations
- searches
- documents
- projects
- interests
- concepts

Show how topics emerge, disappear, merge, or become dominant.

### Geographic Life Map

Show:

- places lived
- travel
- frequently visited locations
- events
- photos
- activity

Time should be a first-class dimension.

### Projects & Career

Connect:

- repositories
- commits
- technologies
- documents
- employment periods
- major projects
- learning
- creative production

Eventually this becomes extremely useful for resume generation and portfolio evidence.

### Media / Creative Archive

Provide a visually rich gallery for:

- artwork
- photography
- writing
- generated media
- creative eras

Allow semantic and chronological exploration.

### Communication Explorer

Analyze:

- volume
- reciprocity
- response patterns
- conversation topics
- relationship changes
- periods of intensity or silence

Always distinguish measured facts from model interpretation.

## 8. Ask PDL

The command bar is the primary interface. Examples:

- "Show me the people who have been consistently present in my life for the longest."
- "What technologies have I actually used heavily versus merely mentioned?"
- "Find the biggest changes in my social network."
- "What were my most productive software-development periods?"
- "Show my creative output over time."
- "What topics did I become obsessed with and then abandon?"
- "Compare my online behavior in 2018, 2022, and 2026."
- "Build me a visual biography from my data."
- "What significant periods appear in my life without me telling you where to look?"

PDL should support follow-up questions that modify the existing workspace:

- "Only show close relationships."
- "Now restrict it to 2021–2024."
- "Overlay major projects."
- "Add geographic movement."
- "Show me the evidence behind that spike."

The interface should mutate rather than reset whenever possible.

## 9. Spatial UI Philosophy

Avoid conventional SaaS dashboard aesthetics. Do not build:

- giant grids of identical cards
- endless KPI tiles
- generic admin panels
- excessive rounded rectangles
- meaningless gradients
- decorative glassmorphism everywhere

Prefer:

- strong visual hierarchy
- large analytical canvases
- spatial continuity
- smooth transitions between representations
- cinematic but restrained motion
- zoomable information spaces
- high-density detail when requested
- progressive disclosure

When the question changes, transitions should help preserve mental context. For example, a person
node in a graph may expand into a timeline rather than abruptly navigating to another page. A
cluster may spatially unfold into its underlying records. A timeline period may zoom into daily
activity. Think continuous information space, not page navigation.

## 10. Visual Intelligence

Visualization choice should depend on the analytical problem. Examples:

- Temporal change → timeline / streamgraph
- Relationships → network
- Movement → map
- Composition → stacked chart / treemap
- Sequence → event flow
- Correlation → scatterplot
- Topic structure → semantic landscape
- Comparison → aligned small multiples
- Density → heatmap
- Hierarchy → tree / sunburst where appropriate
- Evidence → table / source viewer

Use sophisticated visualization libraries where beneficial, potentially including: D3, ECharts,
Sigma.js, deck.gl, React Flow, MapLibre, WebGL-based custom rendering. Do not select technologies
merely because they are visually impressive. Choose them based on the interaction required.

## 11. AI Responsibilities

AI may:

- interpret questions
- identify relevant sources
- create query plans
- propose analyses
- summarize results
- detect interesting patterns
- generate InsightSpecs
- propose VizSpecs
- explain relationships
- suggest follow-up investigations

AI must not:

- fabricate records
- silently infer unsupported facts
- hide missing data
- confuse correlation with causation
- present uncertain identity resolution as certainty
- directly query arbitrary raw data without controlled interfaces
- generate unrestricted executable frontend code

## 12. Trust & Uncertainty

The cockpit must make uncertainty visible. Possible states:

- directly observed
- computed
- strongly inferred
- weakly inferred
- incomplete coverage
- conflicting sources
- unknown

If a period lacks source coverage, visualizations should indicate absence of data rather than
implying absence of activity. This matters enormously in personal datasets, where export
completeness varies by platform and period.

## 13. Entity Resolution

A serious PDL UI eventually requires canonical entities. Examples: Person, Organization, Location,
Project, Repository, Device, Account, Topic, Artifact, Event.

Multiple source identities may resolve to one canonical entity. Example: Ryan Hallgarth — Facebook
identity, Google identity, Apple contact, GitHub identity, email addresses, phone numbers.

The interface should allow inspecting and correcting entity resolution. Human correction outranks
automated inference.

## 14. Local-First / Private Architecture

Initial deployment: localhost / private network only. No public ingress. No telemetry unless
intentionally enabled. No third-party analytics. No cloud dependency for basic browsing. Sensitive
original data stays local.

AI access should occur through defined PDL APIs/tools rather than handing models unrestricted
filesystem access.

The application should be designed so a public version could eventually exist, but public exposure
is explicitly not the current objective.

## 15. Saved Investigations

Allow interesting analytical sessions to become persistent objects. A saved investigation contains:

- original question
- subsequent questions
- QueryPlans
- visual layout
- selected entities
- filters
- date ranges
- resulting insights
- provenance
- annotations

Example saved investigations: "Career Evolution", "Creative Eras", "My Social Network Over Time",
"Technology Skill Evidence", "Major Life Transitions".

This allows the user to build a personal analytical library rather than repeatedly starting from
zero.

## 16. Serendipity Engine

PDL should eventually surface patterns the user did not explicitly request. Examples:

- unusually intense communication periods
- new recurring people
- disappearing relationships
- major topic transitions
- bursts of creative work
- geographic changes
- technology adoption
- activity regime changes
- unexplained gaps
- strongly correlated behaviors

These should be presented as "Worth exploring", not as authoritative conclusions. The system should
invite investigation.

## 17. Performance

The interface should feel immediate even with a very large corpus. Prefer:

- precomputed aggregates
- materialized analytical views
- embeddings indexes
- graph projections
- cached InsightSpecs
- progressive loading
- client-side rendering for interaction where appropriate

Do not force the LLM to repeatedly rediscover basic statistics from raw records. PDL should
increasingly become an analytical engine, not merely an archive.

## 18. Architectural Separation

Maintain clear layers:

```
Raw Sources
    Immutable exports.
↓
Canonical Ingestion
    Normalized durable records with provenance.
↓
Semantic / Entity Layer
    Resolved entities, relationships, embeddings, topics.
↓
Analytics Layer
    Aggregations, metrics, graph projections, temporal features.
↓
AI Planning Layer
    Question interpretation and analysis planning.
↓
Insight Layer
    Evidence-backed findings.
↓
Visualization Layer
    VizSpec and LayoutSpec.
↓
Adaptive Cockpit
```

The UI must never compensate for poor data architecture by inventing logic client-side.

## 19. Development Phases

**Phase 1 — Cockpit Skeleton**: application shell, question bar, workspace renderer, timeline
visualization, table/evidence viewer, source/provenance drawer, deterministic VizSpec format, one
end-to-end analytical question. Goal: prove Question → Data → Insight → Visualization → Evidence.

**Phase 2 — Adaptive Visualization**: multiple visualization types, LayoutSpec, graph exploration,
follow-up questions, workspace transitions, saved investigations.

**Phase 3 — Semantic Intelligence**: entity resolution, topic clustering, semantic search,
relationship graph, temporal semantic analysis.

**Phase 4 — Personal Observatory**: geographic exploration, media gallery, career/project explorer,
creative history, anomaly/transition detection, cross-domain analysis.

**Phase 5 — Generative Analytical UI**: the system can reliably select and compose visual
experiences based on arbitrary supported questions. At this point PDL becomes a genuine adaptive
personal intelligence environment rather than an application with predefined dashboards.

## 20. Long-Term Public Possibility

Do not build the public product now. However, preserve the architectural possibility of a future
sanitized projection. Eventually:

Private Canonical PDL → explicit whitelist/redaction layer → PublicDataSnapshot → portfolio
experience

The private cockpit should remain vastly more capable than the public view. The public system would
be a curated projection of PDL, never direct access to the canonical database.

## 21. North-Star Experience

Ryan opens PDL and types: "Show me who I became over the last fifteen years."

The system does not respond with a wall of text. The screen transforms. A timeline emerges. Career
periods appear. People enter and leave. Locations move. Projects emerge. Technologies accumulate.
Creative work clusters into eras. Topics rise and disappear. Important transitions become visible.

Ryan selects one period. The entire interface smoothly reorganizes around it.

He asks: "Why does this period look different?"

PDL analyzes the evidence and restructures the view again.

He clicks an insight. The original records appear.

Nothing is magic. Everything is traceable.

The goal is not simply to store a life. The goal is to make a life queryable, explorable, visually
understandable, and capable of revealing structure its owner could not otherwise see.

---

## Visual & Interaction Standard

The PDL cockpit must be exceptional visually. This is not a secondary requirement. The UI should be
one of the strongest demonstrations of the entire project: proof that sophisticated data
engineering, AI, visualization, interaction design, and visual design can exist as one coherent
system.

The target is not "good developer UI." The target is: a product that looks and behaves like it came
from an elite visualization, interaction-design, and graphics engineering team.

### Design Principle: One World, Not Many Widgets

The application must feel like one continuous information environment. Avoid the visual language of
conventional dashboards: grids of unrelated cards, dozens of bordered containers, generic KPI
tiles, dashboard-template aesthetics, random chart libraries with different visual languages,
gratuitous gradients, excessive glassmorphism, UI chrome competing with the data.

Instead, build a unified visual grammar. Everything should share: typography, spacing, motion,
depth, interaction behavior, selection states, focus states, visual hierarchy, annotation styles,
data color semantics, temporal semantics, entity semantics.

A timeline, graph, map, message viewer, and semantic landscape should feel like different views
into the same underlying universe.

### Adaptive Composition

When the user asks a question, the application should not simply add another widget. The workspace
should recompose itself around the answer. A graph may become dominant. A timeline may expand
underneath it. Selecting a person may cause surrounding information to reorganize around that
person. A semantic cluster may unfold into conversations. A life period may expand spatially while
unrelated periods recede. A map may become the primary canvas when geography becomes explanatory.

The experience should feel transformational rather than navigational. Prefer morph → expand →
focus → reveal over click → load another page.

### Spatial Continuity

Preserve the user's mental map. Objects should remain recognizable when the representation changes.
Examples: a person node in a relationship graph can animate into that person's timeline; a timeline
event can expand into its source records; a topic cluster can separate into the conversations that
created it; a geographic cluster can unfold into individual places and events.

Transitions should communicate relationships between representations. Motion is not decoration.
Motion explains structure.

### Visual Hierarchy

Every analytical response should have a clear focal point. Avoid presenting eight visualizations
with identical importance. Prefer one hero visualization, one or two contextual views, supporting
evidence, and progressive disclosure for deeper inspection.

The user should know immediately: What am I looking at? What matters? Why does it matter? Where did
it come from?

### High Information Density

Do not equate beauty with emptiness. Ryan is the only initial user and is technically sophisticated.
The cockpit may become information-dense when useful. Support layered detail through: semantic
zoom, hover inspection, brushing, cross-filtering, focus+context, expandable annotations, linked
selections, drill-down, keyboard commands, smooth zooming.

Large displays should feel especially powerful. This application should reward having screen real
estate.

### Modern Rendering Architecture

Use modern GPU-accelerated rendering where the problem justifies it. Candidate technologies
include: SVG/DOM for accessible low-density interface elements, Canvas for high-volume 2D
rendering, WebGL2 for large interactive visualizations, WebGPU as a progressively enabled
next-generation rendering path, deck.gl for large spatial datasets, Sigma.js or equivalent
GPU graph rendering for large relationship networks, MapLibre for geographic exploration, D3
primitives for bespoke analytical visualization, React-based application composition, modern
animation/layout tooling for spatial transitions.

Do not force every visualization through one library. Build a shared PDL visualization grammar
above specialized renderers.

### WebGPU Strategy

PDL should be WebGPU-ready, not WebGPU-dependent. Abstract GPU rendering sufficiently that selected
visualizations may adopt WebGPU as ecosystem support matures. The application should detect
capabilities and gracefully use WebGL2 or Canvas alternatives where required. Use experimental
graphics technology deliberately rather than turning the application into a technology demo.

### Semantic Zoom

Zoom should reveal meaning, not merely magnify pixels. Example timeline:

- 15 years: career eras, relationships, major projects, locations
- 2 years: projects, people, travel, major topics
- 3 months: communication intensity, commits, events, media
- 1 week: individual conversations, files, searches, commits
- 1 day: source records and event sequences

The representation itself should change as scale changes. This should become a signature
interaction of PDL.

### Cross-View Linking

All visible representations should behave as parts of one analytical environment. Selecting an
entity in one view highlights it everywhere. Hovering a period in the timeline may highlight
people, locations, topics, projects, messages, artifacts. Filtering should propagate through the
entire workspace.

The cockpit should feel alive because every visible component understands the same current
analytical state.

### Design System

Create an explicit PDL design system rather than styling screens individually. Define: typography
scale, spacing scale, density modes, elevation/depth, panel behavior, animation curves, transition
durations, entity visual language, chart annotation language, uncertainty encoding, provenance
encoding, selection/focus states, temporal encoding, semantic color tokens.

Never assign arbitrary colors at the chart level. Visual meaning must remain stable throughout the
application. If a color or visual treatment means "inferred," "selected," "uncertain," "person,"
"project," or "source evidence," it should mean that everywhere.

### Motion System

Develop a small motion grammar. Possible meanings:

- Fade = context removed or introduced
- Morph = same object, new representation
- Expand = drill into detail
- Collapse = return to context
- Flow = temporal or causal progression
- Pulse = newly discovered or updated information

Avoid gratuitous animation. The ideal motion is noticeable primarily because removing it would make
the interface harder to understand.

### Typography

Typography should carry much of the interface hierarchy. Avoid excessive boxes by using: scale,
weight, spacing, alignment, opacity, grouping. The application should remain elegant even if
borders around most components are removed.

### Dark Environment

The primary cockpit may use a sophisticated dark environment because it supports long sessions,
high-density visualization, maps, graphs, imagery, and luminous data overlays well. However: dark
theme does not mean black background + neon cyberpunk. Avoid cliché hacker aesthetics. Aim for
restrained, mature, high-end scientific/creative tooling.

### Microinteractions

Small interactions should feel exceptionally polished: selections, hover states, dragging,
resizing, graph expansion, timeline zoom, command palette, filters, evidence opening, loading
states, empty states, errors.

No sudden layout jumps. No unstyled browser-default transitions. No janky chart redraws.

### Performance Is Visual Design

Animations should remain smooth. Dragging should remain responsive. Large graphs should not freeze.
Zooming should feel immediate. Analysis may take time, but interaction with already-present data
should not. Target fluid rendering on the primary development workstation. Use: workers, binary
columnar data where useful, incremental computation, virtualization, GPU rendering, precomputed
aggregates, level-of-detail techniques, progressive rendering.

A beautiful interface running at 12 FPS is not beautiful.

### Visualization Quality Gate

Before accepting any major visualization, ask:

1. Does this representation answer the question better than text?
2. Is the important information visually dominant?
3. Can the user inspect the evidence?
4. Does it remain understandable with real-world data volume?
5. Does it integrate visually with the rest of PDL?
6. Does interaction reveal additional understanding?
7. Does animation preserve context?
8. Could anything be removed?
9. Does it look intentionally designed rather than library-default?
10. Would this still look impressive in a screenshot while remaining genuinely useful?

If not, iterate.

### Anti-Generic-AI Rule

PDL must not look like a product generated from a generic AI application prompt. Specifically
avoid: generic sidebar + cards + chatbot layout, purple/blue AI gradients, glowing assistant orb as
the primary visual identity, excessive rounded cards, canned chart-library themes, chat occupying
half the screen, "AI insights" boxes everywhere, decorative visualizations without analytical
purpose.

AI should feel like the intelligence operating the cockpit, not like a chatbot pasted beside it.

### Signature Experience

PDL should eventually have interactions that are recognizably its own. One likely signature is:
Ask → Transform → Explore. The user asks a question. The existing information space reorganizes.
Relevant data emerges. Unrelated information retreats. The answer becomes a navigable visual
environment. The user then explores the answer directly instead of returning immediately to the
prompt box.

That experience should become the defining characteristic of PersonalDataLab.

The benchmark is not: "Is this a nice dashboard?"

The benchmark is: "I have never interacted with my own data like this before."
