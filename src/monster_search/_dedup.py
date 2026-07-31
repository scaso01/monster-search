"""MinHash-based content deduplication for search results."""

from __future__ import annotations

from datasketch import MinHash, MinHashLSH

from monster_search.fusion import DEFAULT_ENGINE_WEIGHT, ENGINE_WEIGHTS
from monster_search.models import SearchResult
from monster_search._normalize import normalize_url

# Number of permutations for MinHash (higher = more accurate, slower)
_NUM_PERM = 128

# Minimum text length for MinHash; shorter texts fall back to URL dedup
_MIN_TEXT_LENGTH = 20


def _shingle(text: str, n: int = 3) -> list[str]:
    """Generate character n-gram shingles from text."""
    text = text.lower().strip()
    if len(text) < n:
        return [text] if text else []
    return [text[i:i + n] for i in range(len(text) - n + 1)]


def _build_minhash(text: str) -> MinHash:
    """Build a MinHash signature from text using 3-gram shingles."""
    mh = MinHash(num_perm=_NUM_PERM)
    for shingle in _shingle(text):
        mh.update(shingle.encode("utf-8"))
    return mh


def _result_text(result: SearchResult) -> str:
    """Combine title and snippet for content comparison."""
    parts = []
    if result.title:
        parts.append(result.title)
    if result.snippet:
        parts.append(result.snippet)
    return " ".join(parts)


def _engine_weight(result: SearchResult) -> float:
    """Get the engine weight for a result, using source field."""
    return ENGINE_WEIGHTS.get(result.source, DEFAULT_ENGINE_WEIGHT)


def deduplicate_results(
    results: list[SearchResult],
    threshold: float = 0.5,
) -> list[SearchResult]:
    """Deduplicate results using MinHash LSH for content similarity.

    For each cluster of near-duplicates, keeps the result from the
    highest-weighted engine (per ENGINE_WEIGHTS). Short texts (<20 chars)
    fall back to URL-based dedup.

    Args:
        results: List of search results to deduplicate.
        threshold: Jaccard similarity threshold for duplicate detection (0-1).

    Returns:
        Deduplicated list preserving original order.
    """
    if len(results) <= 1:
        return list(results)

    # Phase 1: URL-based dedup for short texts; collect MinHash for longer ones
    url_seen: dict[str, int] = {}  # normalized_url -> index of best result
    minhashes: dict[int, MinHash] = {}  # index -> minhash (only for long texts)
    keep_indices: set[int] = set()
    removed: set[int] = set()

    for i, result in enumerate(results):
        norm_url = normalize_url(result.url)
        text = _result_text(result)

        if len(text) < _MIN_TEXT_LENGTH:
            # URL-only dedup for short text
            if norm_url in url_seen:
                existing_idx = url_seen[norm_url]
                if _engine_weight(result) > _engine_weight(results[existing_idx]):
                    removed.add(existing_idx)
                    keep_indices.discard(existing_idx)
                    url_seen[norm_url] = i
                    keep_indices.add(i)
                else:
                    removed.add(i)
            else:
                url_seen[norm_url] = i
                keep_indices.add(i)
        else:
            # Also check URL dedup first
            if norm_url in url_seen:
                existing_idx = url_seen[norm_url]
                if _engine_weight(result) > _engine_weight(results[existing_idx]):
                    removed.add(existing_idx)
                    keep_indices.discard(existing_idx)
                    url_seen[norm_url] = i
                    keep_indices.add(i)
                    minhashes[i] = _build_minhash(text)
                    minhashes.pop(existing_idx, None)
                else:
                    removed.add(i)
                continue

            url_seen[norm_url] = i
            keep_indices.add(i)
            minhashes[i] = _build_minhash(text)

    # Phase 2: MinHash LSH for content dedup among remaining candidates
    if len(minhashes) > 1:
        try:
            lsh = MinHashLSH(threshold=threshold, num_perm=_NUM_PERM)
        except ValueError:
            # Extreme thresholds can cause MinHashLSH parameter errors;
            # skip content dedup and return URL-deduped results only.
            return [r for i, r in enumerate(results) if i not in removed]

        for idx in sorted(minhashes.keys()):
            if idx in removed:
                continue
            mh = minhashes[idx]
            key = str(idx)

            # Query for similar items already in the LSH
            candidates = lsh.query(mh)
            if candidates:
                # Find the best candidate among duplicates
                dup_idx = int(candidates[0])
                if _engine_weight(results[idx]) > _engine_weight(results[dup_idx]):
                    # New result is from better engine; replace
                    removed.add(dup_idx)
                    keep_indices.discard(dup_idx)
                    # Remove old from LSH and insert new
                    try:
                        lsh.remove(str(dup_idx))
                    except ValueError:
                        pass
                    lsh.insert(key, mh)
                else:
                    # Existing is from better or equal engine; skip new
                    removed.add(idx)
                    keep_indices.discard(idx)
                    continue
            else:
                lsh.insert(key, mh)

    # Return results in original order, excluding removed
    return [r for i, r in enumerate(results) if i not in removed]
