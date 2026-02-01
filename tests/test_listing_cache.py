"""Tests for ListingCache."""

import time

from s3ui.core.listing_cache import ListingCache
from s3ui.models.s3_objects import S3Item


def _item(name: str, key: str = "") -> S3Item:
    return S3Item(name=name, key=key or name, is_prefix=False, size=0)


class TestPutAndGet:
    def test_round_trip(self):
        cache = ListingCache()
        items = [_item("a.txt"), _item("b.txt")]
        cache.put("prefix/", items)
        result = cache.get("prefix/")
        assert result is not None
        assert len(result.items) == 2
        assert result.prefix == "prefix/"

    def test_miss_returns_none(self):
        cache = ListingCache()
        assert cache.get("nonexistent/") is None


class TestLRUEviction:
    def test_evicts_oldest(self):
        cache = ListingCache(max_entries=3)
        cache.put("a/", [_item("1")])
        cache.put("b/", [_item("2")])
        cache.put("c/", [_item("3")])
        cache.put("d/", [_item("4")])
        # "a/" should have been evicted
        assert cache.get("a/") is None
        assert cache.get("d/") is not None

    def test_get_promotes_to_mru(self):
        cache = ListingCache(max_entries=3)
        cache.put("a/", [_item("1")])
        cache.put("b/", [_item("2")])
        cache.put("c/", [_item("3")])
        # Access "a/" to promote it
        cache.get("a/")
        # Now add "d/" — "b/" should be evicted (it's now LRU)
        cache.put("d/", [_item("4")])
        assert cache.get("a/") is not None
        assert cache.get("b/") is None


class TestInvalidate:
    def test_invalidate_single(self):
        cache = ListingCache()
        cache.put("a/", [_item("1")])
        result = cache.invalidate("a/")
        assert result is True
        assert cache.get("a/") is None

    def test_invalidate_missing(self):
        cache = ListingCache()
        result = cache.invalidate("nonexistent/")
        assert result is False

    def test_invalidate_all(self):
        cache = ListingCache()
        cache.put("a/", [_item("1")])
        cache.put("b/", [_item("2")])
        cache.invalidate_all()
        assert cache.get("a/") is None
        assert cache.get("b/") is None


class TestStaleness:
    def test_fresh_entry_not_stale(self):
        cache = ListingCache(stale_seconds=10.0)
        cache.put("a/", [_item("1")])
        assert cache.is_stale("a/") is False

    def test_missing_entry_is_stale(self):
        cache = ListingCache()
        assert cache.is_stale("nonexistent/") is True

    def test_old_entry_is_stale(self):
        cache = ListingCache(stale_seconds=0.01)
        cache.put("a/", [_item("1")])
        time.sleep(0.02)
        assert cache.is_stale("a/") is True


class TestMutations:
    def test_apply_mutation(self):
        cache = ListingCache()
        cache.put("a/", [_item("1"), _item("2")])
        new_item = _item("3")
        cache.apply_mutation("a/", lambda items: items.append(new_item))
        entry = cache.get("a/")
        assert len(entry.items) == 3
        assert entry.dirty is True
        assert entry.mutation_counter == 1

    def test_apply_mutation_increments_counter(self):
        cache = ListingCache()
        cache.put("a/", [])
        cache.apply_mutation("a/", lambda items: items.append(_item("x")))
        cache.apply_mutation("a/", lambda items: items.append(_item("y")))
        assert cache.get_mutation_counter("a/") == 2

    def test_apply_mutation_missing_prefix(self):
        cache = ListingCache()
        result = cache.apply_mutation("missing/", lambda items: items.clear())
        assert result is False

    def test_get_mutation_counter_missing(self):
        cache = ListingCache()
        assert cache.get_mutation_counter("missing/") == 0


class TestSafeRevalidate:
    def test_no_mutations_replaces(self):
        cache = ListingCache()
        cache.put("a/", [_item("1"), _item("2")])
        counter = cache.get_mutation_counter("a/")
        new_items = [_item("3"), _item("4")]
        cache.safe_revalidate("a/", new_items, counter)
        entry = cache.get("a/")
        assert len(entry.items) == 2
        names = {i.name for i in entry.items}
        assert names == {"3", "4"}
        assert entry.dirty is False

    def test_with_mutations_merges(self):
        cache = ListingCache()
        cache.put("a/", [_item("1"), _item("2")])
        counter = cache.get_mutation_counter("a/")

        # Simulate optimistic mutation after fetch started
        optimistic = _item("optimistic")
        cache.apply_mutation("a/", lambda items: items.append(optimistic))

        # Server returns different data (doesn't include optimistic item)
        new_items = [_item("1"), _item("3")]
        cache.safe_revalidate("a/", new_items, counter)

        entry = cache.get("a/")
        names = {i.name for i in entry.items}
        # Should have server items + optimistic item
        assert "1" in names
        assert "3" in names
        assert "optimistic" in names
        assert entry.dirty is True  # still dirty because optimistic items exist

    def test_cache_cleared_during_fetch(self):
        cache = ListingCache()
        cache.put("a/", [_item("1")])
        counter = cache.get_mutation_counter("a/")
        cache.invalidate_all()
        # Revalidation arrives after cache was cleared
        new_items = [_item("x"), _item("y")]
        result = cache.safe_revalidate("a/", new_items, counter)
        assert result is True
        entry = cache.get("a/")
        assert len(entry.items) == 2
