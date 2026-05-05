import os
import tempfile
import unittest

from rtree.geometry import TID
from rtree.page_store import IOStats, PageStore
from rtree.rtree import RTree
from rtree.wal import WriteAheadLog


class TestRTreeTransactionalInsert(unittest.TestCase):
    def test_insert_commit_with_wal_and_validate(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "t.db")
            wl = os.path.join(td.name, "t.wal")
            wal = WriteAheadLog(wl)
            stats = IOStats()
            store = PageStore(db, stats, wal=wal)
            tree = RTree(store)
            with tree.begin_transaction() as tx:
                tree.insert(-33.5, -56.2, TID(9, 0), tx=tx)
            info = tree.validate_structure()
            self.assertGreaterEqual(info["n_entries"], 1)
            sr = tree.range_search(-33.5, -56.2, 0.001)
            self.assertEqual(len(sr.tids), 1)
            wal.close()
        finally:
            td.cleanup()

    def test_range_search_with_shared_locks(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "u.db")
            wl = os.path.join(td.name, "u.wal")
            wal = WriteAheadLog(wl)
            store = PageStore(db, stats=IOStats(), wal=wal)
            tree = RTree(store)
            tree.insert(0.0, 0.0, TID(1, 0))
            with tree.begin_transaction() as tx:
                sr = tree.range_search(0.0, 0.0, 1.0, tx=tx)
            self.assertGreaterEqual(len(sr.tids), 1)
            wal.close()
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
