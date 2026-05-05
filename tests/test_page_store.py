import os
import struct
import tempfile
import unittest

from rtree.page_store import PageStore, IOStats


class TestPageStoreBasics(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fp = os.path.join(self.td.name, "t.db")
        self.stats = IOStats()
        self.store = PageStore(self.fp, self.stats)

    def tearDown(self):
        self.td.cleanup()

    def test_superblock_initial_state(self):
        sb = self.store.get_superblock()
        self.assertEqual(sb["root_pid"], 0)
        self.assertEqual(sb["next_free"], 1)
        self.assertEqual(sb["free_head"], 0)
        self.assertEqual(sb["node_count"], 0)

    def test_allocate_returns_increasing_pids(self):
        a = self.store.allocate_page()
        b = self.store.allocate_page()
        c = self.store.allocate_page()
        self.assertEqual((a, b, c), (1, 2, 3))

    def test_page_size_is_strict_4096(self):
        pid = self.store.allocate_page()
        with self.assertRaises(AssertionError):
            self.store.write_page(pid, b"x" * 100)
        with self.assertRaises(AssertionError):
            self.store.write_page(pid, b"x" * 4097)

    def test_read_write_roundtrip(self):
        pid = self.store.allocate_page()
        payload = b"\xAB" * PageStore.PAGE_SIZE
        self.store.write_page(pid, payload)
        got = bytes(self.store.read_page(pid))
        self.assertEqual(got, payload)

    def test_io_counter_increments_on_disk_access(self):
        pid = self.store.allocate_page()
        self.store.write_page(pid, b"\x01" * PageStore.PAGE_SIZE)
        before = self.stats.snapshot()
        # second read after eviction must hit disk
        for _ in range(PageStore.BUFFER_SIZE + 5):
            extra = self.store.allocate_page()
            self.store.write_page(extra, b"\x00" * PageStore.PAGE_SIZE)
        # forzar miss leyendo pid original
        _ = self.store.read_page(pid)
        after = self.stats.snapshot()
        self.assertGreater(after.reads, before.reads)


class TestPageStoreFreeListReuse(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fp = os.path.join(self.td.name, "t.db")
        self.store = PageStore(self.fp, IOStats())

    def tearDown(self):
        self.td.cleanup()

    def test_freed_page_is_reused_lifo(self):
        pids = [self.store.allocate_page() for _ in range(5)]
        # libero 2, 4 (en ese orden) -> free_head debería terminar en 4
        self.store.free_page(pids[1])
        self.store.free_page(pids[3])
        # próximas 2 allocations deben venir del free-list (LIFO)
        new1 = self.store.allocate_page()
        new2 = self.store.allocate_page()
        self.assertIn(new1, (pids[1], pids[3]))
        self.assertIn(new2, (pids[1], pids[3]))
        self.assertNotEqual(new1, new2)

    def test_free_then_alloc_then_free_stress(self):
        # forzar reuso múltiple
        live = [self.store.allocate_page() for _ in range(20)]
        for pid in live[::2]:  # libero pares
            self.store.free_page(pid)
        # realloc 10 → todas deben venir del pool liberado
        new = [self.store.allocate_page() for _ in range(10)]
        freed_set = set(live[::2])
        self.assertTrue(set(new).issubset(freed_set))
        # estructura del archivo no debe crecer ilimitadamente
        sb = self.store.get_superblock()
        self.assertLessEqual(sb["next_free"], 21)

    def test_freed_page_is_zeroed_on_realloc(self):
        pid = self.store.allocate_page()
        self.store.write_page(pid, b"\xFF" * PageStore.PAGE_SIZE)
        self.store.free_page(pid)
        new_pid = self.store.allocate_page()
        self.assertEqual(new_pid, pid)
        data = bytes(self.store.read_page(new_pid))
        # debe estar limpio (no residuo del 0xFF anterior)
        self.assertEqual(data, b"\x00" * PageStore.PAGE_SIZE)


class TestPageStorePersistence(unittest.TestCase):
    def test_close_reopen_preserves_data(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "t.db")
            s1 = PageStore(fp, IOStats())
            pid = s1.allocate_page()
            payload = bytes(range(256)) * 16  # 4096 bytes
            s1.write_page(pid, payload)
            s1.set_root(pid)
            del s1
            s2 = PageStore(fp, IOStats())
            self.assertEqual(s2.get_root(), pid)
            self.assertEqual(bytes(s2.read_page(pid)), payload)

    def test_reopen_preserves_freelist(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "t.db")
            s1 = PageStore(fp, IOStats())
            pids = [s1.allocate_page() for _ in range(5)]
            s1.free_page(pids[2])
            s1.free_page(pids[4])
            del s1
            s2 = PageStore(fp, IOStats())
            new = s2.allocate_page()
            self.assertIn(new, (pids[2], pids[4]))


if __name__ == "__main__":
    unittest.main()
