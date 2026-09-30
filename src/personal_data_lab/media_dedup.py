"""Audio de-duplication: keep one copy of every recording, the most complete one.

Byte hashes only catch identical files. The copies that matter here were trimmed, re-exported, or
re-encoded (a MOTIV WAV cut down in Audio Editor, the same memo in the iPhone backup and in the
Apple export), so this compares the audio itself:

1. **Fingerprint** every readable audio asset: decode to 8 kHz mono, and for each 128 ms window
   (32 ms hop, 31.25 per second) take the signs of how 33 log-spaced band energies (300-2000 Hz)
   change across frequency and time -- 32 bits per frame, the Haitsma-Kalker construction, which
   survives lossy re-encoding and volume changes. A voiced mask (frame level above -45 dBFS)
   keeps silence from ever counting as a match.
2. **Match**: every asset's voiced frames go into one sorted index. Each asset queries a sample of
   its own frames, votes on (other asset, time offset), and the best candidates are verified in
   10-second blocks: a block matches when its bit error rate is under 0.33 (unrelated audio sits
   near 0.5). An asset is *contained in* another when at least 90 percent of its voiced blocks match
   at one offset.
3. **Keep the most complete copy**: an asset contained in a longer one is superseded by it; two
   assets contained in each other are the same audio, and the better copy wins (lossless over lossy,
   higher sample rate, the device original over an export). Chains resolve to one final keeper.

Nothing is deleted: `media.audio_supersession` records which asset is redundant, to which keeper,
and the evidence (coverage, offset, bit error rate). Transcription and search skip superseded assets.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

FINGERPRINT_VERSION = "bandsign-8k-v1"
DETECTOR_VERSION = "contain-v2"
SAMPLE_RATE = 8000
WINDOW = 1024
HOP = 256
FRAMES_PER_SECOND = SAMPLE_RATE / HOP
VOICED_DBFS = -45.0
BLOCK_FRAMES = int(10 * FRAMES_PER_SECOND)
MIN_BLOCK_VOICED = 20
BLOCK_MAX_BER = 0.33
MIN_COVERAGE = 0.90
MIN_VOICED_FRAMES = int(5 * FRAMES_PER_SECOND)
QUERY_STRIDE = 4
CODE_FREQUENCY_CAP = 400
LOSSLESS_CODECS = frozenset({"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_s16be", "pcm_s24be",
                             "flac", "alac"})
SOURCE_PREFERENCE = {"ios_backup": 3, "apple": 2, "google": 1}


def _band_matrix() -> np.ndarray:
    freqs = np.fft.rfftfreq(WINDOW, 1 / SAMPLE_RATE)
    edges = np.geomspace(300, 2000, 34)
    matrix = np.zeros((len(freqs), 33), dtype=np.float32)
    for band in range(33):
        matrix[(freqs >= edges[band]) & (freqs < edges[band + 1]), band] = 1.0
    return matrix


_BANDS = _band_matrix()
_HANN = np.hanning(WINDOW).astype(np.float32)
_BIT_WEIGHTS = (1 << np.arange(32, dtype=np.uint64))


@dataclass
class Fingerprint:
    codes: np.ndarray  # uint32 per frame
    voiced: np.ndarray  # bool per frame
    duration_seconds: float
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None

    @property
    def voiced_seconds(self) -> float:
        return float(self.voiced.sum()) / FRAMES_PER_SECOND


class _Framer:
    """Streaming STFT -> sub-fingerprints, so an eight-hour file never sits in memory."""

    def __init__(self) -> None:
        self.buffer = np.empty(0, dtype=np.float32)
        self.previous_diff: np.ndarray | None = None
        self.codes: list[np.ndarray] = []
        self.voiced: list[np.ndarray] = []
        self.samples = 0

    def feed(self, samples: np.ndarray) -> None:
        self.samples += len(samples)
        self.buffer = np.concatenate([self.buffer, samples.astype(np.float32, copy=False)])
        if len(self.buffer) < WINDOW:
            return
        count = 1 + (len(self.buffer) - WINDOW) // HOP
        frames = np.lib.stride_tricks.sliding_window_view(self.buffer, WINDOW)[::HOP][:count]
        power = np.abs(np.fft.rfft(frames * _HANN, axis=1)).astype(np.float32) ** 2
        log_energy = np.log(power @ _BANDS + 1e-9)
        diff = log_energy[:, :-1] - log_energy[:, 1:]
        previous = self.previous_diff if self.previous_diff is not None else diff[:1]
        stacked = np.concatenate([previous, diff])
        bits = (stacked[1:] - stacked[:-1]) > 0
        self.codes.append((bits.astype(np.uint64) * _BIT_WEIGHTS).sum(axis=1).astype(np.uint32))
        rms = np.sqrt(np.mean(frames * frames, axis=1)) + 1e-12
        self.voiced.append(20 * np.log10(rms) > VOICED_DBFS)
        self.previous_diff = diff[-1:]
        self.buffer = self.buffer[count * HOP:].copy()

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.codes:
            return np.empty(0, np.uint32), np.empty(0, bool)
        return np.concatenate(self.codes), np.concatenate(self.voiced)


def fingerprint_samples(samples: np.ndarray) -> Fingerprint:
    """Fingerprint 8 kHz mono float samples (used by tests and by `fingerprint_stream`)."""
    framer = _Framer()
    framer.feed(samples)
    codes, voiced = framer.result()
    return Fingerprint(codes=codes, voiced=voiced, duration_seconds=len(samples) / SAMPLE_RATE)


def fingerprint_stream(stream) -> Fingerprint:
    import av

    framer = _Framer()
    with av.open(stream) as container:
        audio = container.streams.audio[0]
        context = audio.codec_context
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio):
            for out in resampler.resample(frame):
                framer.feed(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            framer.feed(out.to_ndarray().reshape(-1))
        codes, voiced = framer.result()
        return Fingerprint(
            codes=codes, voiced=voiced, duration_seconds=framer.samples / SAMPLE_RATE, codec=context.name,
            sample_rate=context.sample_rate, channels=getattr(context, "channels", None),
        )


# --- matching ---------------------------------------------------------------------------------


@dataclass
class Candidate:
    asset_id: int
    source: str
    sha256: str | None
    fingerprint: Fingerprint
    voiced_positions: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.voiced_positions = np.flatnonzero(self.fingerprint.voiced)

    @property
    def lossless(self) -> bool:
        return (self.fingerprint.codec or "") in LOSSLESS_CODECS


def _quality(c: Candidate) -> tuple:
    return (c.lossless, c.fingerprint.sample_rate or 0, SOURCE_PREFERENCE.get(c.source, 0), -c.asset_id)


def better(a: Candidate, b: Candidate) -> Candidate:
    """The more complete copy: clearly longer wins; otherwise the better-quality copy."""
    if abs(a.fingerprint.duration_seconds - b.fingerprint.duration_seconds) > 2.0:
        return a if a.fingerprint.duration_seconds > b.fingerprint.duration_seconds else b
    return a if _quality(a) > _quality(b) else b


def _popcount(values: np.ndarray) -> np.ndarray:
    return np.unpackbits(values.view(np.uint8)).reshape(len(values), 32).sum(axis=1)


@dataclass
class Containment:
    inner: int
    outer: int
    coverage: float
    offset_frames: int
    bit_error_rate: float


def verify(inner: Candidate, outer: Candidate, offset: int) -> tuple[float, float]:
    """(coverage, bit error rate) of `inner` placed at `offset` frames inside `outer`."""
    codes_in, codes_out = inner.fingerprint.codes, outer.fingerprint.codes
    matched = eligible = 0
    errors: list[float] = []
    positions = inner.voiced_positions
    for start in range(0, len(codes_in), BLOCK_FRAMES):
        block = positions[(positions >= start) & (positions < start + BLOCK_FRAMES)]
        if len(block) < MIN_BLOCK_VOICED:
            continue
        eligible += 1
        target = block + offset
        if target[0] < 0 or target[-1] >= len(codes_out):
            continue
        ber = float(_popcount(codes_in[block] ^ codes_out[target]).mean()) / 32
        if ber < BLOCK_MAX_BER:
            matched += 1
            errors.append(ber)
    if eligible == 0:
        return 0.0, 1.0
    return matched / eligible, (float(np.mean(errors)) if errors else 1.0)


def find_containments(candidates: list[Candidate]) -> list[Containment]:
    usable = [c for c in candidates if len(c.voiced_positions) >= MIN_VOICED_FRAMES]
    if len(usable) < 2:
        return []
    all_codes = np.concatenate([c.fingerprint.codes[c.voiced_positions] for c in usable])
    all_owner = np.concatenate([np.full(len(c.voiced_positions), i, np.int32) for i, c in enumerate(usable)])
    all_pos = np.concatenate([c.voiced_positions.astype(np.int64) for c in usable])
    order = np.argsort(all_codes, kind="stable")
    sorted_codes, sorted_owner, sorted_pos = all_codes[order], all_owner[order], all_pos[order]

    found: list[Containment] = []
    for q, query in enumerate(usable):
        q_pos = query.voiced_positions[::QUERY_STRIDE]
        q_codes = query.fingerprint.codes[q_pos]
        lo = np.searchsorted(sorted_codes, q_codes, "left")
        hi = np.searchsorted(sorted_codes, q_codes, "right")
        counts = hi - lo
        keep = (counts > 0) & (counts <= CODE_FREQUENCY_CAP)
        if not keep.any():
            continue
        lo, counts, q_pos = lo[keep], counts[keep], q_pos[keep]
        index = np.repeat(lo - np.cumsum(np.concatenate([[0], counts[:-1]])), counts) + np.arange(counts.sum())
        owners = sorted_owner[index]
        offsets = sorted_pos[index] - np.repeat(q_pos, counts)
        other = owners != q
        if not other.any():
            continue
        owners, offsets = owners[other], offsets[other]
        buckets = offsets // 2
        keys = owners.astype(np.int64) * (1 << 40) + (buckets + (1 << 39))
        unique, votes = np.unique(keys, return_counts=True)
        # An absolute floor, not a share of the query: a processed copy (noise reduction, EQ) keeps
        # few exact 32-bit codes (at a 0.2 bit error rate about 1 frame in 1,300), so a
        # length-proportional bar missed a 35-minute recording against its own edited twin.
        # Block verification below is what rules out false matches.
        threshold = max(6, min(int(0.02 * len(q_pos)), 10))
        best_per_owner: dict[int, tuple[int, int]] = {}
        for key, vote in zip(unique[votes >= threshold], votes[votes >= threshold], strict=True):
            owner = int(key >> 40)
            bucket = int(key - (owner << 40)) - (1 << 39)
            if vote > best_per_owner.get(owner, (0, 0))[0]:
                best_per_owner[owner] = (int(vote), bucket)
        for owner, (_, bucket) in best_per_owner.items():
            outer = usable[owner]
            best = max(
                ((*verify(query, outer, off), off) for off in range(bucket * 2 - 1, bucket * 2 + 3)),
                key=lambda r: (r[0], -r[1]),
            )
            coverage, ber, offset = best
            if coverage >= MIN_COVERAGE:
                found.append(Containment(query.asset_id, outer.asset_id, coverage, offset, ber))
    return found


@dataclass
class Supersession:
    asset_id: int
    kept_asset_id: int
    relation: str
    coverage: float | None = None
    offset_seconds: float | None = None
    bit_error_rate: float | None = None


def decide(candidates: list[Candidate], containments: Iterable[Containment]) -> list[Supersession]:
    """Exact duplicates first, then containment; every chain resolves to one final keeper."""
    by_id = {c.asset_id: c for c in candidates}
    parent: dict[int, Supersession] = {}

    by_sha: dict[str, list[Candidate]] = {}
    for c in candidates:
        if c.sha256:
            by_sha.setdefault(c.sha256, []).append(c)
    for group in by_sha.values():
        keeper = group[0]
        for c in group[1:]:
            keeper = better(keeper, c)
        for c in group:
            if c is not keeper:
                parent[c.asset_id] = Supersession(c.asset_id, keeper.asset_id, "exact_duplicate")

    edges = {(c.inner, c.outer): c for c in containments}
    for (inner, outer), edge in sorted(edges.items(), key=lambda kv: -kv[1].coverage):
        if inner in parent:
            continue
        a, b = by_id[inner], by_id[outer]
        mutual = (outer, inner) in edges
        if mutual:
            winner = better(a, b)
            if winner is b:
                parent.setdefault(inner, Supersession(inner, outer, "same_audio", edge.coverage,
                                                      edge.offset_frames / FRAMES_PER_SECOND, edge.bit_error_rate))
        elif b.fingerprint.duration_seconds >= a.fingerprint.duration_seconds:
            parent.setdefault(inner, Supersession(inner, outer, "contained_in", edge.coverage,
                                                  edge.offset_frames / FRAMES_PER_SECOND, edge.bit_error_rate))

    def root(asset_id: int, seen: set[int]) -> int:
        step = parent.get(asset_id)
        if step is None or step.kept_asset_id in seen:
            return asset_id
        seen.add(asset_id)
        return root(step.kept_asset_id, seen)

    resolved = []
    for asset_id, step in parent.items():
        keeper = root(asset_id, set())
        if keeper != asset_id:
            resolved.append(Supersession(asset_id, keeper, step.relation, step.coverage, step.offset_seconds,
                                         step.bit_error_rate))
    return resolved


# --- database ---------------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], None]


def _fingerprint_job(job: tuple[int, str, str, str]) -> tuple[int, dict]:
    """Worker-process entry point: decode and fingerprint one asset, never raising."""
    from .derive.derivers.media import _open_asset_bytes

    asset_id, source, original_path, filename = job
    started = time.monotonic()
    try:
        fp = _fingerprint_asset(
            type("Row", (), {"source": source, "original_path": original_path, "filename": filename}),
            _open_asset_bytes,
        )
        values = {
            "status": "ok", "error": None, "duration": fp.duration_seconds, "voiced": fp.voiced_seconds,
            "codec": fp.codec, "rate": fp.sample_rate, "channels": fp.channels,
            "codes": fp.codes.astype("<u4").tobytes(), "mask": np.packbits(fp.voiced).tobytes(),
        }
    except Exception as error:  # noqa: BLE001 -- recorded, never silently skipped
        values = {"status": "unreadable", "error": f"{type(error).__name__}: {error}"[:500], "duration": None,
                  "voiced": None, "codec": None, "rate": None, "channels": None, "codes": None, "mask": None}
    values["elapsed"] = time.monotonic() - started
    return asset_id, values


def fingerprint_assets(engine: Engine, *, sources: list[str] | None = None, workers: int | None = None,
                       on_progress: ProgressCallback | None = None) -> dict:
    """Fingerprint every active audio asset that has no current fingerprint yet, decoding in
    parallel worker processes (decoding, not the fingerprint maths, is the cost)."""
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT a.id, a.source, a.original_path, a.sha256, a.filename FROM assets a "
            "WHERE a.mime_type LIKE 'audio/%' AND a.lifecycle_state = 'active' "
            "AND (CAST(:sources AS text[]) IS NULL OR a.source = ANY(CAST(:sources AS text[]))) "
            "AND NOT EXISTS (SELECT 1 FROM media.audio_fingerprint f WHERE f.asset_id = a.id "
            "                AND f.fingerprint_version = :v) ORDER BY a.id"
        ), {"sources": sources, "v": FINGERPRINT_VERSION}).all()
    by_id = {row.id: row for row in rows}
    done = failed = 0
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fingerprint_job, (r.id, r.source, r.original_path, r.filename)) for r in rows]
        for index, future in enumerate(as_completed(futures), 1):
            asset_id, values = future.result()
            elapsed = values.pop("elapsed")
            done += values["status"] == "ok"
            failed += values["status"] != "ok"
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO media.audio_fingerprint (asset_id, sha256, fingerprint_version, status, error, "
                    "duration_seconds, voiced_seconds, codec, sample_rate, channels, codes, voiced) VALUES "
                    "(:id, :sha, :v, :status, :error, :duration, :voiced, :codec, :rate, :channels, :codes, :mask) "
                    "ON CONFLICT (asset_id) DO UPDATE SET sha256 = EXCLUDED.sha256, "
                    "fingerprint_version = EXCLUDED.fingerprint_version, status = EXCLUDED.status, "
                    "error = EXCLUDED.error, duration_seconds = EXCLUDED.duration_seconds, "
                    "voiced_seconds = EXCLUDED.voiced_seconds, codec = EXCLUDED.codec, "
                    "sample_rate = EXCLUDED.sample_rate, channels = EXCLUDED.channels, codes = EXCLUDED.codes, "
                    "voiced = EXCLUDED.voiced, created_at = CURRENT_TIMESTAMP"
                ), {"id": asset_id, "sha": by_id[asset_id].sha256, "v": FINGERPRINT_VERSION, **values})
            if on_progress is not None:
                on_progress(index, len(rows), f"{by_id[asset_id].filename} ({elapsed:.1f}s)")
    return {"fingerprinted": done, "unreadable": failed}


def _fingerprint_asset(row, open_asset_bytes) -> Fingerprint:
    """A plain file is decoded in place; anything inside an export ZIP is copied to a temp file
    first, because the decoder seeks and every seek inside a compressed ZIP entry re-decompresses
    it from the start (the same reason `media_transcribe_whisper` uses a temp file)."""
    import os
    import shutil
    import tempfile

    from .derive.derivers.media import _temp_file_suffix

    if os.path.isfile(row.original_path):
        return fingerprint_stream(row.original_path)
    tmp_path = None
    try:
        with open_asset_bytes(row.source, row.original_path) as stream, tempfile.NamedTemporaryFile(
            suffix=_temp_file_suffix(row.filename), delete=False
        ) as tmp:
            shutil.copyfileobj(stream, tmp, 1024 * 1024)
            tmp_path = tmp.name
        return fingerprint_stream(tmp_path)
    finally:
        if tmp_path is not None:
            os.unlink(tmp_path)


def load_candidates(engine: Engine) -> list[Candidate]:
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT f.asset_id, a.source, a.sha256, f.duration_seconds, f.codec, f.sample_rate, f.channels, "
            "f.codes, f.voiced FROM media.audio_fingerprint f JOIN assets a ON a.id = f.asset_id "
            "WHERE f.status = 'ok' AND f.fingerprint_version = :v AND a.lifecycle_state = 'active'"
        ), {"v": FINGERPRINT_VERSION}).all()
    out = []
    for r in rows:
        codes = np.frombuffer(r.codes, dtype="<u4").astype(np.uint32)
        voiced = np.unpackbits(np.frombuffer(r.voiced, dtype=np.uint8))[: len(codes)].astype(bool)
        out.append(Candidate(r.asset_id, r.source, r.sha256, Fingerprint(
            codes=codes, voiced=voiced, duration_seconds=r.duration_seconds or 0.0, codec=r.codec,
            sample_rate=r.sample_rate, channels=r.channels)))
    return out


def rebuild_supersessions(engine: Engine) -> dict:
    """Recompute every supersession from the stored fingerprints (plus identical-byte groups)."""
    candidates = load_candidates(engine)
    containments = find_containments(candidates)
    decisions = decide(candidates, containments)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM media.audio_supersession"))
        if decisions:
            connection.execute(text(
                "INSERT INTO media.audio_supersession (asset_id, kept_asset_id, relation, coverage, offset_seconds, "
                "bit_error_rate, detector_version) VALUES (:asset_id, :kept_asset_id, :relation, :coverage, "
                ":offset_seconds, :bit_error_rate, :v)"
            ), [{**d.__dict__, "v": DETECTOR_VERSION} for d in decisions])
    relations: dict[str, int] = {}
    for d in decisions:
        relations[d.relation] = relations.get(d.relation, 0) + 1
    return {"fingerprinted_assets": len(candidates), "containments_found": len(containments),
            "superseded": len(decisions), "by_relation": relations,
            "keepers": len(candidates) - len(decisions)}
