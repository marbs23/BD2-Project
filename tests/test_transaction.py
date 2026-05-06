import os
import tempfile
import unittest

from rtree.lock_manager import DeadlockError, LockManager
from rtree.page_store import IOStats, PageStore
from rtree.transaction import Transaction
from rtree.wal import WriteAheadLog


class TestTransactionAbort(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "db")
        self.wal_path = os.path.join(self.td.name, "wal.log")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_abort_restores_before_images_backward(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        lm = LockManager()
        wal = WriteAheadLog(self.wal_path)
        pid = store.allocate_page()
        orig = bytes(store.read_page(pid))
        dirty = bytearray(orig)
        dirty[10] = 0x77

        tx = Transaction(1, store, lm, wal)
        tx.begin()
        wal.log_write(1, pid, orig, bytes(dirty))
        store.write_page(pid, bytes(dirty))
        tx.abort()
        wal.close()

        self.assertEqual(bytes(store.read_page(pid)), orig)

    def test_abort_two_writes_same_page_newest_first_undo(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        lm = LockManager()
        wal = WriteAheadLog(self.wal_path)
        pid = store.allocate_page()
        a = bytes(store.read_page(pid))
        b = bytearray(a)
        b[0] = 0x01
        c = bytearray(b)
        c[0] = 0x02

        tx = Transaction(2, store, lm, wal)
        tx.begin()
        wal.log_write(2, pid, bytes(a), bytes(b))
        wal.log_write(2, pid, bytes(b), bytes(c))
        store.write_page(pid, bytes(c))
        tx.abort()
        wal.close()

        self.assertEqual(bytes(store.read_page(pid)), a)


class TestTransactionContext(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "db")
        self.wal_path = os.path.join(self.td.name, "wal.log")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_context_commits_on_success(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        lm = LockManager()
        wal = WriteAheadLog(self.wal_path)
        pid = store.allocate_page()
        orig = bytes(store.read_page(pid))
        mark = bytearray(orig)
        mark[99] = 0xEE

        with Transaction(3, store, lm, wal) as tx:
            self.assertEqual(tx.tid, 3)
            wal.log_write(3, pid, orig, bytes(mark))
            store.write_page(pid, bytes(mark))

        store2 = PageStore(self.db, IOStats())
        self.assertEqual(store2.read_page(pid)[99], 0xEE)
        wal.close()

    def test_context_aborts_on_exception_restores_page(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        lm = LockManager()
        wal = WriteAheadLog(self.wal_path)
        pid = store.allocate_page()
        orig = bytes(store.read_page(pid))
        dirty = bytearray(orig)
        dirty[5] = 0xAA

        with self.assertRaises(RuntimeError):
            with Transaction(4, store, lm, wal):
                wal.log_write(4, pid, orig, bytes(dirty))
                store.write_page(pid, bytes(dirty))
                raise RuntimeError("fallo")

        self.assertEqual(bytes(store.read_page(pid)), orig)
        wal.close()


class TestTransactionCommitRules(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.td.name, "db")
        self.wal_path = os.path.join(self.td.name, "wal.log")

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_commit_raises_when_deadlock_aborted(self) -> None:
        stats = IOStats()
        store = PageStore(self.db, stats)
        lm = LockManager()
        wal = WriteAheadLog(self.wal_path)
        tx = Transaction(5, store, lm, wal)
        tx.begin()
        tx.aborted = True
        with self.assertRaises(DeadlockError):
            tx.commit()
        wal.close()


if __name__ == "__main__":
    unittest.main()
