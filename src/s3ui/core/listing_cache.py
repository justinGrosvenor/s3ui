"""Stale-while-revalidate listing cache for S3 prefix listings."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from s3ui.constants import LISTING_CACHE_MAX_ENTRIES, LISTING_CACHE_STALE_SECONDS
from s3ui.models.s3_objects import S3Item

logger = logging.getLogger("s3ui.listing_cache")


@dataclass
class CachedListing:
    """A single cached listing result."""

    prefix: str
    items: list[S3Item]
    fetched_at: float  # monotonic time
    dirty: bool = False
    mutation_counter: int = 0


class ListingCache:
    """LRU cache for S3 prefix listings with mutation tracking.

    Thread-safe: all access is protected by a lock.
    """

    def __init__(
        self,
        max_entries: int = LISTING_CACHE_MAX_ENTRIES,
        stale_seconds: float = LISTING_CACHE_STALE_SECONDS,
    ) -> None:
        self._max_entries = max_entries
        self._stale_seconds = stale_seconds
        self._cache: OrderedDict[str, CachedListing] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, prefix: str) -> CachedListing | None:
        """Get a cached listing, promoting it to MRU. Returns None on miss."""
        with self._lock:
            entry = self._cache.get(prefix)
            if entry is not None:
                self._cache.move_to_end(prefix)
                return entry
            return None

    def put(self, prefix: str, items: list[S3Item]) -> None:
        """Store a listing, evicting LRU if over capacity."""
        with self._lock:
            if prefix in self._cache:
                existing = self._cache[prefix]
                existing.items = list(items)
                existing.fetched_at = time.monotonic()
                existing.dirty = False
                # Don't reset mutation_counter — revalidation handles that
                self._cache.move_to_end(prefix)
            else:
                self._cache[prefix] = CachedListing(
                    prefix=prefix,
                    items=list(items),
                    fetched_at=time.monotonic(),
                )
            self._evict_if_needed()

    def invalidate(self, prefix: str) -> bool:
        """Remove one entry. Returns True if it existed."""
        with self._lock:
            if prefix in self._cache:
                del self._cache[prefix]
                return True
            return False

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        with self._lock:
            self._cache.clear()

    def is_stale(self, prefix: str) -> bool:
        """True if entry is missing or older than stale threshold."""
        with self._lock:
            entry = self._cache.get(prefix)
            if entry is None:
                return True
            age = time.monotonic() - entry.fetched_at
            return age > self._stale_seconds

    def apply_mutation(self, prefix: str, fn: Callable[[list[S3Item]], None]) -> bool:
        """Apply a mutation function to a cached listing's items.

        Sets dirty=True and increments mutation_counter.
        Returns False if prefix is not cached.
        """
        with self._lock:
            entry = self._cache.get(prefix)
            if entry is None:
                return False
            fn(entry.items)
            entry.dirty = True
            entry.mutation_counter += 1
            return True

    def get_mutation_counter(self, prefix: str) -> int:
        """Returns current mutation counter for a prefix. 0 if not cached."""
        with self._lock:
            entry = self._cache.get(prefix)
            if entry is None:
                return 0
            return entry.mutation_counter

    def safe_revalidate(
        self,
        prefix: str,
        new_items: list[S3Item],
        counter_at_fetch_start: int,
    ) -> bool:
        """Apply background revalidation results safely.

        If mutation_counter matches counter_at_fetch_start, does a standard replace.
        If mutations happened since fetch started, merges: preserves optimistic
        additions while incorporating external changes.

        Returns True if the cache was updated.
        """
        with self._lock:
            entry = self._cache.get(prefix)
            if entry is None:
                # Cache was cleared while we were fetching — just store the result
                self._cache[prefix] = CachedListing(
                    prefix=prefix,
                    items=list(new_items),
                    fetched_at=time.monotonic(),
                )
                self._evict_if_needed()
                return True

            if entry.mutation_counter == counter_at_fetch_start:
                # No mutations since fetch started — safe to replace
                entry.items = list(new_items)
                entry.fetched_at = time.monotonic()
                entry.dirty = False
                return True

            # Mutations happened — merge strategy:
            # Keep items that exist in new_items (server truth)
            # Also keep items that were added by optimistic mutations
            # (items in current cache but NOT in the old server state)
            new_keys = {item.key for item in new_items}

            # Optimistic items: in current cache but not from server
            optimistic = [item for item in entry.items if item.key not in new_keys]

            # Build merged list: server items + optimistic items
            merged = list(new_items) + optimistic

            entry.items = merged
            entry.fetched_at = time.monotonic()
            entry.dirty = bool(optimistic)  # still dirty if we have optimistic items
            # Don't reset mutation_counter — it tracks total mutations
            logger.debug(
                "Merged revalidation for '%s': %d server + %d optimistic items",
                prefix, len(new_items), len(optimistic),
            )
            return True

    def _evict_if_needed(self) -> None:
        """Evict LRU entries if over capacity. Must be called with lock held."""
        while len(self._cache) > self._max_entries:
            evicted_key, _ = self._cache.popitem(last=False)
            logger.debug("Evicted cache entry: '%s'", evicted_key)
