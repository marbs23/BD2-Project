"""
Simulador de acceso concurrente (bloque 5): threads + WAL + LockManager.

Cada test tiene ventana máxima de 10s por `thread.join` para detectar cuelgues.
"""

from __future__ import annotations

import os
import random
import tempfile
import threading
import time
import unittest

from rtree.geometry import TID
from rtree.lock_manager import DeadlockError
from rtree.page_store import IOStats, PageStore
from rtree.rtree import RTree
from rtree.transaction import Transaction
from rtree.wal import WriteAheadLog

JOIN_TIMEOUT_S = 10.0


def _join_all(threads: list[threading.Thread], timeout: float = JOIN_TIMEOUT_S) -> None:
    for t in threads:
        t.join(timeout=timeout)
        assert (
            not t.is_alive()
        ), f"hilo colgado después de {timeout}s ({getattr(t, 'name', t)})"


class TestConcurrentInserts(unittest.TestCase):
    def test_two_threads_disjoint_inserts_complete(self) -> None:
        random.seed(2026)
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "c.db")
            wl = os.path.join(td.name, "c.wal")
            wal = WriteAheadLog(wl)
            stats = IOStats()
            store = PageStore(db, stats, wal=wal)
            tree = RTree(store)

            for i in range(300):
                tree.insert(
                    random.uniform(-1.0, 1.0),
                    random.uniform(-1.0, 1.0),
                    TID(500, i),
                )

            pts_left: list[tuple[float, float, int]] = []
            pts_right: list[tuple[float, float, int]] = []

            def run_left() -> None:
                with tree.begin_transaction() as tx:
                    for k in range(100):
                        lon = -0.95 + k * 0.001
                        lat = -0.5 + (k % 17) * 0.01
                        pts_left.append((lon, lat, k))
                        tree.insert(lon, lat, TID(600, k), tx=tx)

            def run_right() -> None:
                with tree.begin_transaction() as tx:
                    for k in range(100):
                        lon = 0.05 + k * 0.001
                        lat = 0.5 + (k % 19) * 0.01
                        pts_right.append((lon, lat, k))
                        tree.insert(lon, lat, TID(601, k), tx=tx)

            t_a = threading.Thread(target=run_left, name="T_left")
            t_b = threading.Thread(target=run_right, name="T_right")
            t0 = time.monotonic()
            t_a.start()
            t_b.start()
            _join_all([t_a, t_b])
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 10.0, "debe completar sin deadlock prolongado")

            for lon, lat, _ in pts_left + pts_right:
                sr = tree.range_search(lon, lat, 1e-9)
                self.assertGreaterEqual(len(sr.tids), 1, f"punto perdido ({lon},{lat})")

            wal.close()
        finally:
            td.cleanup()


class TestSharedExclusiveBlocking(unittest.TestCase):
    def test_range_waits_until_insert_commits(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "s.db")
            wl = os.path.join(td.name, "s.wal")
            wal = WriteAheadLog(wl)
            store = PageStore(db, IOStats(), wal=wal)
            tree = RTree(store)
            for i in range(50):
                tree.insert(random.uniform(-0.2, 0.2), random.uniform(-0.2, 0.2), TID(800, i))

            inserted = threading.Event()
            got_results: list[int] = []

            def inserter() -> None:
                with tree.begin_transaction() as tx:
                    tree.insert(0.5, 0.5, TID(1, 99), tx=tx)
                    inserted.set()
                    time.sleep(0.6)

            def searcher() -> None:
                inserted.wait(timeout=5.0)
                with tree.begin_transaction() as tx:
                    sr = tree.range_search(0.5, 0.5, 0.05, tx=tx)
                    got_results.append(len(sr.tids))

            t1 = threading.Thread(target=inserter)
            t2 = threading.Thread(target=searcher)
            t1.start()
            t2.start()
            _join_all([t1, t2])
            self.assertEqual(got_results, [1])
            wal.close()
        finally:
            td.cleanup()


class TestDeadlockIntegration(unittest.TestCase):
    def test_deadlock_one_aborts_other_commits(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "d.db")
            wl = os.path.join(td.name, "d.wal")
            wal = WriteAheadLog(wl)
            stats = IOStats()
            store = PageStore(db, stats, wal=wal)
            tree = RTree(store)
            pa = store.allocate_page()
            pb = store.allocate_page()

            barrier = threading.Barrier(2)
            victims: list[int] = []

            def tx_a() -> None:
                tx = Transaction(10, store, tree._lock_mgr, wal)
                try:
                    tx.begin()
                    tree._lock_mgr.acquire(tx, pa, "X")
                    barrier.wait()
                    tree._lock_mgr.acquire(tx, pb, "X")
                    tx.commit()
                except DeadlockError as e:
                    victims.append(e.tid)
                    tx.abort()

            def tx_b() -> None:
                tx = Transaction(20, store, tree._lock_mgr, wal)
                try:
                    tx.begin()
                    tree._lock_mgr.acquire(tx, pb, "X")
                    barrier.wait()
                    tree._lock_mgr.acquire(tx, pa, "X")
                    tx.commit()
                except DeadlockError as e:
                    victims.append(e.tid)
                    tx.abort()

            t1 = threading.Thread(target=tx_a)
            t2 = threading.Thread(target=tx_b)
            t1.start()
            t2.start()
            _join_all([t1, t2])
            self.assertIn(10, victims)
            self.assertEqual(min(victims), 10)
            wal.close()
        finally:
            td.cleanup()


class TestCrashRecoveryConcurrent(unittest.TestCase):
    def test_replay_undo_without_commit(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "r.db")
            wl = os.path.join(td.name, "r.wal")
            wal_path = wl
            wal = WriteAheadLog(wal_path)
            stats = IOStats()
            store = PageStore(db, stats, wal=wal)
            tree = RTree(store)

            coords: list[tuple[float, float]] = []
            tx = tree.begin_transaction()
            tx.begin()
            for i in range(50):
                lon, lat = 0.01 * i, 0.02 * i
                coords.append((lon, lat))
                tree.insert(lon, lat, TID(77, i), tx=tx)
            wal.close()

            store2 = PageStore(db, IOStats())
            WriteAheadLog.replay(store2, wal_path)
            tree2 = RTree(store2)
            for lon, lat in coords:
                sr = tree2.range_search(lon, lat, 1e-12)
                self.assertEqual(len(sr.tids), 0)
        finally:
            td.cleanup()

    def test_replay_keeps_committed_inserts(self) -> None:
        td = tempfile.TemporaryDirectory()
        try:
            db = os.path.join(td.name, "r2.db")
            wl = os.path.join(td.name, "r2.wal")
            wal_path = wl
            wal = WriteAheadLog(wal_path)
            store = PageStore(db, IOStats(), wal=wal)
            tree = RTree(store)

            coords: list[tuple[float, float]] = []
            with tree.begin_transaction() as tx:
                for i in range(50):
                    lon, lat = -0.01 * i, -0.03 * i
                    coords.append((lon, lat))
                    tree.insert(lon, lat, TID(88, i), tx=tx)
            wal.close()

            store2 = PageStore(db, IOStats())
            WriteAheadLog.replay(store2, wal_path)
            tree2 = RTree(store2)
            for lon, lat in coords:
                sr = tree2.range_search(lon, lat, 1e-12)
                self.assertEqual(len(sr.tids), 1)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
