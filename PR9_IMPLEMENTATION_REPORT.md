# PR9 implementation report: AI, knowledge, and notes

## Scope

PR9 implements Gemini, Gemini in Workspace, NotebookLM, Flow, Workspace Studio, Keep, Journal,
Pinpoint, and Google Cloud Search. It contains no Apple/iMessages or PR10 account/commerce work.

## Implementation

- Flow project and generated-media provider IDs produce durable records and explicit project/media
  relationships. Generation inputs, including prompts and model fields, remain lossless.
- Gemini HTML and Gemini Workspace text become searchable conversation documents without guessing
  unsupported turn boundaries.
- NotebookLM uses a path-stable notebook identity so notebook metadata, HTML/Markdown/PPTX sources,
  audio overviews, video, PDFs, images, and extensionless assets remain connected across split ZIPs.
- Keep JSON produces notes with provider microsecond timestamps and lifecycle flags. HTML remains a
  separate provider representation rather than being weakly merged by title.
- Journal seconds/nanoseconds timestamps are normalized exactly while the native timestamp objects
  remain in metadata.
- Empty Workspace Studio members import as zero-record evidence. Pinpoint and Google Cloud Search
  are fully routed and tested synthetically because they are absent from this export.
- All binary assets are SHA-256 streamed and externally referenced; no binary content enters the DB.

## Real-export acceptance

| Product | Real inventory / canary | Normalized result | Failures |
|---|---|---|---:|
| Flow | all 23 JSON + representative JPG/MP4 from 46 members / 35.65 MB | 23 records, 4 assets, 22 relationships | 0 |
| Gemini | all 2 HTML members | 2 conversation documents | 0 |
| Gemini in Workspace | all 9 TXT members | 9 conversation documents | 0 |
| NotebookLM | all 698 structured + 10 representative binary members from 815 / 5.34 GB | 698 records, 10 assets, 314 relationships | 0 |
| Keep | all 4 JSON/HTML members | 4 note representations | 0 |
| Journal | 1 JSON member | 1 note | 0 |
| Workspace Studio | 2 empty JSON members | zero records, explicit empty evidence | 0 |
| Pinpoint / Cloud Search | absent | synthetic contracts | 0 |

## Next frontier

PR10 is exactly account, commerce, device, and home: Google Account, One, Pay, Wallet, Purchases &
Reservations, Shopping, Store, Play Store, Finance, Fi Wireless, Pixel, Android Device
Configuration Service, Home App, Nest, and Profile.
