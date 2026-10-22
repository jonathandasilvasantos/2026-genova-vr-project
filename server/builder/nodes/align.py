"""Estimate WHEN words are spoken in a narration clip, so the player can highlight an object exactly when the
narrator names it. The TTS service returns no timestamps, so this aligns the known text to the audio:

1. find the pauses in the audio (energy valleys),
2. split the text into clauses at punctuation and put the clause boundaries at the longest pauses,
3. spread the words of each clause over its voiced time in proportion to their length.

Good to a few hundred milliseconds, which is plenty for highlighting. Nothing here is subject-specific.
"""
import io
import re
import wave

import numpy as np

_WORD = re.compile(r"[A-Za-z0-9À-ɏ']+")


def _norm(w):
    return re.sub(r"[^a-z0-9à-ɏ]", "", w.lower())


def _energy(wav_bytes, win_ms=10):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        sr, n = w.getframerate(), w.getnframes()
        ch, sw = w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
    x = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    x /= float(np.iinfo(dtype).max)
    hop = int(sr * win_ms / 1000)
    m = len(x) // hop
    e = np.sqrt((x[: m * hop].reshape(m, hop) ** 2).mean(axis=1))
    return e, win_ms / 1000.0, len(x) / sr


def _pauses(e, hop_s, min_pause_s=0.14):
    """List of (start_s, end_s) low-energy gaps between speech, ignoring the leading/trailing silence."""
    if len(e) < 10:
        return [], 0.0, len(e) * hop_s
    thr = max(1e-4, 0.08 * float(np.percentile(e, 95)))
    voiced = e > thr
    idx = np.where(voiced)[0]
    if len(idx) == 0:
        return [], 0.0, len(e) * hop_s
    first, last = idx[0], idx[-1]
    gaps, i = [], first
    while i <= last:
        if not voiced[i]:
            j = i
            while j <= last and not voiced[j]:
                j += 1
            if (j - i) * hop_s >= min_pause_s:
                gaps.append((i * hop_s, j * hop_s))
            i = j
        else:
            i += 1
    return gaps, first * hop_s, (last + 1) * hop_s


def word_times(wav_bytes, text):
    """[(word, start_s, end_s)] for every word of `text`."""
    e, hop, total = _energy(wav_bytes)
    gaps, t0, t1 = _pauses(e, hop)
    # clauses: split after . , ; : ! ? and dashes, keeping words
    clauses, cur = [], []
    for tok in re.findall(r"[^\s]+", text):
        cur.append(tok)
        if re.search(r"[.,;:!?—–]$", tok):
            clauses.append(cur)
            cur = []
    if cur:
        clauses.append(cur)
    clauses = [c for c in clauses if any(_WORD.search(t) for t in c)]
    if not clauses:
        return []
    # boundaries between clauses -> the longest pauses, in time order
    need = len(clauses) - 1
    chosen = sorted(sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:need]) if need > 0 else []
    edges = [t0] + [(g[0] + g[1]) / 2 for g in chosen]
    while len(edges) < len(clauses):          # fewer pauses than clauses: share the remaining time evenly
        last = edges[-1]
        edges.append(last + (t1 - last) / (len(clauses) - len(edges) + 1))
    edges.append(t1)
    out = []
    for c, a, b in zip(clauses, edges[:-1], edges[1:]):
        words = [t for t in c if _WORD.search(t)]
        weights = [max(2, len(_norm(w))) + (1 if re.search(r"[.,;:!?]$", w) else 0) * 0 for w in words]
        tot = float(sum(weights))
        pos = a
        for w, wt in zip(words, weights):
            dur = (b - a) * wt / tot
            out.append((w, float(round(pos, 3)), float(round(pos + dur, 3))))
            pos += dur
    return out


def phrase_time(words, phrase, lead=0.12):
    """Start time (seconds) of the first occurrence of `phrase` in the aligned words, a little early. None if absent."""
    target = [_norm(w) for w in re.findall(r"[^\s]+", phrase) if _norm(w)]
    if not target:
        return None
    norm_words = [_norm(w[0]) for w in words]
    for i in range(len(norm_words) - len(target) + 1):
        if norm_words[i: i + len(target)] == target:
            return max(0.0, round(words[i][1] - lead, 2))
    # fall back to the first word of the phrase
    for i, w in enumerate(norm_words):
        if w == target[0]:
            return max(0.0, round(words[i][1] - lead, 2))
    return None
