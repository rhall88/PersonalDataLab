"""Audio de-duplication: containment is found in re-encoded, trimmed copies; the most complete
copy is kept; unrelated audio and silence never match."""

from __future__ import annotations

import numpy as np

from personal_data_lab.media_dedup import (
    SAMPLE_RATE,
    Candidate,
    Fingerprint,
    decide,
    find_containments,
    fingerprint_samples,
)


def _speechlike(seconds: float, seed: int) -> np.ndarray:
    """Noise shaped by random syllable-rate envelopes and moving formant-ish tones."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    envelope = np.repeat(rng.uniform(0.05, 1.0, n // 1600 + 1), 1600)[:n]
    tones = sum(np.sin(2 * np.pi * np.cumsum(rng.uniform(300, 1800) + 200 * np.sin(2 * np.pi * rng.uniform(0.2, 2) * t)) / SAMPLE_RATE)
                for _ in range(3))
    return (0.2 * envelope * (tones + 0.5 * rng.standard_normal(n))).astype(np.float32)


def _candidate(asset_id: int, samples: np.ndarray, *, codec: str = "aac", sha: str | None = None) -> Candidate:
    fp = fingerprint_samples(samples)
    fp.codec, fp.sample_rate = codec, 48000 if codec.startswith("pcm") else 44100
    return Candidate(asset_id, "ios_backup", sha or f"sha{asset_id}", fp)


def test_trimmed_reencoded_copy_is_superseded_by_the_full_recording() -> None:
    full = _speechlike(120, seed=1)
    rng = np.random.default_rng(9)
    clip = full[20 * SAMPLE_RATE: 70 * SAMPLE_RATE] * 0.6 + 0.002 * rng.standard_normal(50 * SAMPLE_RATE)
    clip = np.round(clip * 256) / 256  # coarse requantisation stands in for lossy re-encoding
    other = _speechlike(90, seed=2)
    candidates = [_candidate(1, full, codec="pcm_s24le"), _candidate(2, clip.astype(np.float32)), _candidate(3, other)]
    containments = find_containments(candidates)
    assert {(c.inner, c.outer) for c in containments} == {(2, 1)}
    (edge,) = containments
    assert abs(edge.offset_frames / (SAMPLE_RATE / 256) - 20) < 0.1
    (decision,) = decide(candidates, containments)
    assert (decision.asset_id, decision.kept_asset_id, decision.relation) == (2, 1, "contained_in")


def test_same_audio_keeps_the_lossless_copy_and_exact_bytes_collapse() -> None:
    audio = _speechlike(60, seed=3)
    wav, m4a = _candidate(10, audio, codec="pcm_s24le"), _candidate(11, audio * 0.9)
    twin = _candidate(12, audio * 0.9, sha="sha11")
    m4a.sha256 = "sha11"
    decisions = {d.asset_id: d for d in decide([wav, m4a, twin], find_containments([wav, m4a, twin]))}
    assert decisions[11].kept_asset_id == 10 and decisions[12].kept_asset_id == 10
    assert decisions[12].relation == "exact_duplicate"
    assert 10 not in decisions


def test_silence_never_matches() -> None:
    quiet = np.zeros(60 * SAMPLE_RATE, dtype=np.float32)
    fp = fingerprint_samples(quiet)
    assert fp.voiced_seconds == 0
    speech = _speechlike(60, seed=4)
    candidates = [Candidate(1, "x", "a", fp), _candidate(2, speech)]
    assert find_containments(candidates) == []
    assert isinstance(fp, Fingerprint)
