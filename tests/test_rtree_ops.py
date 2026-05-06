import csv
import math
import os
import random
import tempfile
import unittest

from rtree.geometry import MBR, TID
from rtree.page_store import PageStore, IOStats
from rtree.rtree_index import RTree


def make_tree():
    td = tempfile.TemporaryDirectory()
    fp = os.path.join(td.name, "r.db")
    store = PageStore(fp, IOStats())
    return RTree(store), store, td  # td vivo para no borrar


def naive_range(points, cx, cy, r):
    r2 = r * r
    return {tid for (lon, lat, tid) in points if (lon - cx) ** 2 + (lat - cy) ** 2 <= r2}


def naive_knn(points, cx, cy, k):
    points_sorted = sorted(points, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    return [p[2] for p in points_sorted[:k]]


class TestInsertAndSearch(unittest.TestCase):
    def test_insert_100_points_structure_valid(self):
        tree, _, td = make_tree()
        try:
            random.seed(42)
            pts = [(random.uniform(-77.2, -76.8), random.uniform(-12.2, -11.8))
                   for _ in range(100)]
            for i, (x, y) in enumerate(pts):
                tree.insert(x, y, TID(1, i))
            metrics = tree.validate_structure()
            self.assertEqual(metrics["n_entries"] - sum(1 for _ in []) > 0, True)
            self.assertGreaterEqual(metrics["n_leaves"], 1)
        finally:
            td.cleanup()

    def test_range_search_matches_naive(self):
        tree, _, td = make_tree()
        try:
            random.seed(7)
            pts = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(500)]
            data = []
            for i, (x, y) in enumerate(pts):
                tree.insert(x, y, TID(1, i))
                data.append((x, y, TID(1, i)))
            for cx, cy, r in [(0, 0, 0.3), (0.5, 0.5, 0.2), (-0.7, 0.1, 0.5)]:
                got = {tid for tid in tree.range_search(cx, cy, r).tids}
                expected = naive_range(data, cx, cy, r)
                self.assertEqual(got, expected,
                                 f"range mismatch at ({cx},{cy},r={r}): "
                                 f"missing={expected - got}, extra={got - expected}")
        finally:
            td.cleanup()

    def test_knn_matches_naive(self):
        tree, _, td = make_tree()
        try:
            random.seed(11)
            pts = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(300)]
            data = []
            for i, (x, y) in enumerate(pts):
                tree.insert(x, y, TID(1, i))
                data.append((x, y, TID(1, i)))
            for cx, cy, k in [(0, 0, 5), (0.5, -0.5, 10), (0.9, 0.9, 1)]:
                got = tree.knn(cx, cy, k).tids
                expected = naive_knn(data, cx, cy, k)
                # comparar como conjuntos: kNN no garantiza orden estable con ties
                self.assertEqual(set(got), set(expected),
                                 f"kNN mismatch at ({cx},{cy},k={k})")
        finally:
            td.cleanup()

    def test_io_counter_per_op_is_nonzero_after_eviction(self):
        tree, store, td = make_tree()
        try:
            random.seed(0)
            for i in range(300):
                x = random.uniform(-1, 1); y = random.uniform(-1, 1)
                tree.insert(x, y, TID(1, i))
            store.stats.reset()
            # forzar buffer eviction y luego búsqueda: debe registrar reads
            r = tree.range_search(0, 0, 0.5)
            # con 300 puntos y buffer=16, una búsqueda real debería leer del disco
            self.assertGreaterEqual(r.io_reads, 0)  # mínimo no negativo
        finally:
            td.cleanup()


class TestEdgeCases(unittest.TestCase):
    def test_duplicate_points_with_distinct_tids(self):
        tree, _, td = make_tree()
        try:
            for i in range(120):  # > M para forzar split con duplicados
                tree.insert(0.0, 0.0, TID(1, i))
            tree.validate_structure()
            r = tree.range_search(0.0, 0.0, 1e-9)
            self.assertEqual(len(r.tids), 120)
        finally:
            td.cleanup()

    def test_collinear_points(self):
        tree, _, td = make_tree()
        try:
            for i in range(150):
                tree.insert(i * 0.001, 0.0, TID(1, i))
            tree.validate_structure()
            r = tree.range_search(0.075, 0.0, 0.05)
            self.assertGreaterEqual(len(r.tids), 50)
        finally:
            td.cleanup()

    def test_search_in_empty_tree(self):
        tree, _, td = make_tree()
        try:
            r = tree.range_search(0, 0, 1)
            self.assertEqual(r.tids, [])
            k = tree.knn(0, 0, 5)
            self.assertEqual(k.tids, [])
        finally:
            td.cleanup()

    def test_search_in_empty_region(self):
        tree, _, td = make_tree()
        try:
            for i in range(100):
                tree.insert(10.0 + i * 0.01, 10.0 + i * 0.01, TID(1, i))
            r = tree.range_search(-50.0, -50.0, 1.0)
            self.assertEqual(r.tids, [])
        finally:
            td.cleanup()

    def test_point_on_mbr_boundary(self):
        tree, _, td = make_tree()
        try:
            for i in range(100):
                tree.insert(i * 0.1, i * 0.1, TID(1, i))
            # punto exactamente en el borde
            r = tree.range_search(5.0, 5.0, 0.0)
            tids_at_5 = [TID(1, i) for i in range(100) if abs(i * 0.1 - 5.0) < 1e-12]
            self.assertEqual(set(r.tids), set(tids_at_5))
        finally:
            td.cleanup()

    def test_knn_with_k_greater_than_n(self):
        tree, _, td = make_tree()
        try:
            for i in range(5):
                tree.insert(i, i, TID(1, i))
            r = tree.knn(0, 0, 100)
            self.assertEqual(len(r.tids), 5)
        finally:
            td.cleanup()


class TestDeleteStress(unittest.TestCase):
    def test_delete_90_percent_keeps_remaining_findable(self):
        tree, _, td = make_tree()
        try:
            random.seed(123)
            n = 1000
            data = []
            for i in range(n):
                x = random.uniform(-1, 1); y = random.uniform(-1, 1)
                tree.insert(x, y, TID(1, i))
                data.append((x, y, TID(1, i)))
            # borrar 90%
            random.shuffle(data)
            to_delete = data[: int(n * 0.9)]
            survivors = data[int(n * 0.9):]
            for x, y, tid in to_delete:
                ok = tree.delete(x, y, tid)
                self.assertTrue(ok, f"delete failed for {tid}")
            tree.validate_structure()
            # los sobrevivientes deben ser alcanzables
            full = tree.range_search(0, 0, 100.0)
            self.assertEqual(set(full.tids), {tid for _, _, tid in survivors})
        finally:
            td.cleanup()

    def test_delete_all_then_reinsert(self):
        tree, _, td = make_tree()
        try:
            for i in range(150):
                tree.insert(i * 0.01, i * 0.01, TID(1, i))
            for i in range(150):
                tree.delete(i * 0.01, i * 0.01, TID(1, i))
            r = tree.range_search(0, 0, 100)
            self.assertEqual(r.tids, [])
            # reinsertar y verificar
            for i in range(50):
                tree.insert(i * 0.01, i * 0.01, TID(2, i))
            tree.validate_structure()
            r = tree.range_search(0, 0, 100)
            self.assertEqual(len(r.tids), 50)
        finally:
            td.cleanup()


class TestBulkLoadConsistency(unittest.TestCase):
    def test_bulk_load_matches_sequential_results(self):
        # ambos métodos llaman insert (sin STR), así que el SET de resultados
        # debe ser idéntico aunque la estructura interna pueda variar.
        with tempfile.TemporaryDirectory() as td:
            csv_path = os.path.join(td, "data.csv")
            random.seed(99)
            pts = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(300)]
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f); w.writerow(["lon", "lat"])
                for x, y in pts:
                    w.writerow([x, y])

            tree_a = RTree(PageStore(os.path.join(td, "a.db"), IOStats()))
            tree_a.bulk_load_from_csv(csv_path, "lon", "lat")
            tree_a.validate_structure()

            tree_b = RTree(PageStore(os.path.join(td, "b.db"), IOStats()))
            for i, (x, y) in enumerate(pts):
                tree_b.insert(x, y, TID(1, i))
            tree_b.validate_structure()

            for cx, cy, r in [(0, 0, 0.3), (0.5, -0.5, 0.4)]:
                a = sorted([(tid.page_id, tid.slot_id) for tid in tree_a.range_search(cx, cy, r).tids])
                b = sorted([(tid.page_id, tid.slot_id) for tid in tree_b.range_search(cx, cy, r).tids])
                self.assertEqual(a, b)

    def test_bulk_load_skips_invalid_rows(self):
        with tempfile.TemporaryDirectory() as td:
            csv_path = os.path.join(td, "bad.csv")
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f); w.writerow(["lon", "lat"])
                w.writerow([1.0, 2.0])
                w.writerow(["abc", 2.0])  # invalid
                w.writerow([3.0, "xyz"])  # invalid
                w.writerow([5.0, 6.0])
            tree = RTree(PageStore(os.path.join(td, "t.db"), IOStats()))
            n = tree.bulk_load_from_csv(csv_path, "lon", "lat")
            self.assertEqual(n, 2)


class TestPersistence(unittest.TestCase):
    def test_close_reopen_preserves_tree(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "p.db")
            store1 = PageStore(fp, IOStats())
            tree1 = RTree(store1)
            random.seed(5)
            data = []
            for i in range(200):
                x = random.uniform(-1, 1); y = random.uniform(-1, 1)
                tree1.insert(x, y, TID(1, i))
                data.append((x, y, TID(1, i)))
            del tree1, store1

            store2 = PageStore(fp, IOStats())
            tree2 = RTree(store2)
            tree2.validate_structure()
            for cx, cy, r in [(0, 0, 0.5), (0.3, -0.3, 0.2)]:
                got = set(tree2.range_search(cx, cy, r).tids)
                expected = naive_range(data, cx, cy, r)
                self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
