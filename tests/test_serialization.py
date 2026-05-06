import struct
import unittest

from rtree.geometry import MBR, TID
from rtree.rtree_index import RTreeNode, M, PAGE_SIZE


class TestNodeSerialization(unittest.TestCase):
    def test_leaf_full_roundtrip(self):
        node = RTreeNode(page_id=42, is_leaf=True, level=0, parent_pid=7)
        for i in range(M):
            node.entries.append({
                "mbr": MBR(-180.0 + i, -179.0 + i, -90.0 + i * 0.5, -89.5 + i * 0.5),
                "tid_page": 100 + i,
                "tid_slot": i,
            })
        blob = node.serialize()
        self.assertEqual(len(blob), PAGE_SIZE)
        rec = RTreeNode.deserialize(42, blob)
        self.assertTrue(rec.is_leaf)
        self.assertEqual(rec.level, 0)
        self.assertEqual(rec.parent_pid, 7)
        self.assertEqual(len(rec.entries), M)
        for orig, got in zip(node.entries, rec.entries):
            self.assertEqual(orig["mbr"], got["mbr"])
            self.assertEqual(orig["tid_page"], got["tid_page"])
            self.assertEqual(orig["tid_slot"], got["tid_slot"])

    def test_internal_with_child_pids(self):
        node = RTreeNode(page_id=99, is_leaf=False, level=3, parent_pid=0)
        node.entries.append({"mbr": MBR(0, 10, 0, 10), "child_pid": 5})
        node.entries.append({"mbr": MBR(20, 30, 20, 30), "child_pid": 6})
        blob = node.serialize()
        rec = RTreeNode.deserialize(99, blob)
        self.assertFalse(rec.is_leaf)
        self.assertEqual(rec.level, 3)
        self.assertEqual(rec.entries[0]["child_pid"], 5)
        self.assertEqual(rec.entries[1]["child_pid"], 6)

    def test_float_precision_extreme_values(self):
        # floats con muchos decimales: precision IEEE-754 double debe sobrevivir
        cases = [
            (-77.04267384529384, -77.04267384529383, -12.04500000000001, -12.045),
            (1e-300, 1e-300 + 1e-310, -1e-300, 1e-300),
            (1.7976931348623157e+200, 1.7976931348623158e+200, 0.0, 1.0),
        ]
        for tup in cases:
            mbr = MBR(*tup)
            buf = bytearray(32)
            mbr.pack_into(buf, 0)
            got = MBR.unpack_from(buf, 0)
            self.assertEqual(mbr, got)

    def test_empty_node_roundtrip(self):
        node = RTreeNode(page_id=1, is_leaf=True, level=0, parent_pid=0)
        blob = node.serialize()
        rec = RTreeNode.deserialize(1, blob)
        self.assertEqual(rec.entries, [])
        self.assertEqual(rec.parent_pid, 0)

    def test_serialized_page_is_exactly_page_size(self):
        # crítico: si no es exacto rompe paginación
        for n_entries in [0, 1, M // 2, M]:
            node = RTreeNode(0, True, 0, 0)
            for i in range(n_entries):
                node.entries.append({
                    "mbr": MBR(0, 1, 0, 1), "tid_page": i, "tid_slot": 0
                })
            self.assertEqual(len(node.serialize()), PAGE_SIZE)


if __name__ == "__main__":
    unittest.main()
