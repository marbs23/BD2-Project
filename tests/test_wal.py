import os
import struct
import tempfile
import unittest

from rtree.page_store import IOStats, PageStore
from rtree.wal import (
    OP_COMMIT,
    OP_WRITE,
    PAGE_BYTES,
    RECORD_SIZE,
    WriteAheadLog,
    iter_records,
    unpack_record,
)


class TestWalFormat(unittest.TestCase):
    def test_record_size_matches_layout(self) -> None:
        hdr = struct.calcsize("<QII B")
        self.assertEqual(hdr, 17)
        self.assertEqual(RECORD_SIZE, 17 + PAGE_BYTES + PAGE_BYTES)

    def test_unpack_roundtrip(self) -> None:
        before = bytes(range(256)) * (PAGE_BYTES // 256) + bytes(PAGE_BYTES % 256)
        after = bytes((i + 7) % 256 for i in range(PAGE_BYTES))
        raw = struct.pack("<QII B", 42, 9, 100, OP_WRITE) + before + after
        lsn, tid, pid, op, b2, a2 = unpack_record(raw)
        self.assertEqual((lsn, tid, pid, op), (42, 9, 100, OP_WRITE))
        self.assertEqual(b2, before)
        self.assertEqual(a2, after)


class TestWalReplayCommitted(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "t.db")
        self.wal_path = os.path.join(self.td.name, "wal.log")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_redo_applies_after_image_for_committed_tx(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        pid = store.allocate_page()
        before = bytes(store.read_page(pid))
        after = bytearray(before)
        # Exactamente 8 bytes: en Python 3.14+ un RHS más largo que el slice extiende el bytearray.
        after[0:8] = b"COMMITTED"[:8]

        wal = WriteAheadLog(self.wal_path)
        wal.log_begin(1)
        wal.log_write(1, pid, bytes(before), bytes(after))
        wal.log_commit(1)
        wal.close()

        store.write_page(pid, before)

        store2 = PageStore(self.db, IOStats())
        WriteAheadLog.replay(store2, self.wal_path)
        self.assertEqual(bytes(store2.read_page(pid))[:8], b"COMMITTED"[:8])


class TestWalReplayLoser(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "u.db")
        self.wal_path = os.path.join(self.td.name, "wal2.log")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_undo_loser_without_commit(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        pid = store.allocate_page()
        original = bytes(store.read_page(pid))
        dirty = bytearray(original)
        dirty[100] = 0xAB

        wal = WriteAheadLog(self.wal_path)
        wal.log_begin(7)
        wal.log_write(7, pid, original, bytes(dirty))
        wal.close()

        store.write_page(pid, bytes(dirty))

        store2 = PageStore(self.db, IOStats())
        WriteAheadLog.replay(store2, self.wal_path)
        self.assertEqual(bytes(store2.read_page(pid)), original)


class TestWalReplayMixed(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "m.db")
        self.wal_path = os.path.join(self.td.name, "wal3.log")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_redo_committed_and_undo_loser_different_pages(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        p1 = store.allocate_page()
        p2 = store.allocate_page()
        b1 = bytes(store.read_page(p1))
        b2 = bytes(store.read_page(p2))
        c1 = bytearray(b1)
        c1[0] = 0x11
        l1 = bytearray(b2)
        l1[0] = 0xEE

        wal = WriteAheadLog(self.wal_path)
        wal.log_begin(1)
        wal.log_begin(2)
        wal.log_write(1, p1, b1, bytes(c1))
        wal.log_commit(1)
        wal.log_write(2, p2, b2, bytes(l1))
        wal.close()

        store.write_page(p1, b1)
        store.write_page(p2, bytes(l1))

        store2 = PageStore(self.db, IOStats())
        WriteAheadLog.replay(store2, self.wal_path)
        self.assertEqual(store2.read_page(p1)[0], 0x11)
        self.assertEqual(bytes(store2.read_page(p2)), b2)


class TestWalPartialCorruption(unittest.TestCase):
    def test_iter_records_stops_at_truncated_tail(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            path = os.path.join(td.name, "bad.log")
            good = struct.pack("<QII B", 1, 1, 0, OP_COMMIT) + bytes(PAGE_BYTES * 2)
            with open(path, "wb") as f:
                f.write(good)
                f.write(b"\x00\x01\x02")
            recs = list(iter_records(path))
            self.assertEqual(len(recs), 1)
        finally:
            td.cleanup()


class TestWalAbortLogged(unittest.TestCase):
    def test_abort_logged_not_undone_on_replay(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "a.db")
            wal_path = os.path.join(td.name, "wal4.log")
            stats = IOStats()
            store = PageStore(db, stats)
            pid = store.allocate_page()
            orig = bytes(store.read_page(pid))
            dirty = bytearray(orig)
            dirty[50] = 0xCD

            wal = WriteAheadLog(wal_path)
            wal.log_begin(3)
            wal.log_write(3, pid, orig, bytes(dirty))
            wal.log_abort(3)
            wal.close()

            store.write_page(pid, bytes(dirty))

            store2 = PageStore(db, IOStats())
            WriteAheadLog.replay(store2, wal_path)
            self.assertEqual(bytes(store2.read_page(pid)), bytes(dirty))
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
