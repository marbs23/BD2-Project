import math
import unittest

from rtree.geometry import MBR


class TestMBR(unittest.TestCase):

    def test_from_point_degenerate(self):
        mbr = MBR.from_point(1.0, 2.0)
        self.assertEqual(mbr.min_lon, 1.0)
        self.assertEqual(mbr.max_lon, 1.0)
        self.assertEqual(mbr.min_lat, 2.0)
        self.assertEqual(mbr.max_lat, 2.0)

    def test_expand(self):
        a = MBR(0, 1, 0, 1)
        b = MBR(0.5, 2, 0.5, 2)
        u = a.expand(b)
        self.assertEqual(u, MBR(0, 2, 0, 2))

    def test_area(self):
        self.assertAlmostEqual(MBR(0, 3, 0, 4).area(), 12.0)
        self.assertAlmostEqual(MBR.from_point(1, 1).area(), 0.0)

    def test_area_enlargement(self):
        base = MBR(0, 1, 0, 1)
        other = MBR(0, 2, 0, 2)
        self.assertAlmostEqual(base.area_enlargement(other), 3.0)

    def test_mindist_sq_inside(self):
        mbr = MBR(0, 10, 0, 10)
        self.assertAlmostEqual(mbr.mindist_sq(5, 5), 0.0)

    def test_mindist_sq_outside_axis(self):
        mbr = MBR(0, 4, 0, 4)
        self.assertAlmostEqual(mbr.mindist_sq(6, 2), 4.0)

    def test_mindist_sq_outside_diagonal(self):
        mbr = MBR(0, 1, 0, 1)
        self.assertAlmostEqual(mbr.mindist_sq(2, 2), 2.0)

    def test_intersects_circle_inside(self):
        mbr = MBR(0, 10, 0, 10)
        self.assertTrue(mbr.intersects_circle(5, 5, 1))

    def test_intersects_circle_outside(self):
        mbr = MBR(0, 1, 0, 1)
        self.assertFalse(mbr.intersects_circle(5, 5, 1))

    def test_intersects_circle_boundary(self):
        mbr = MBR(0, 1, 0, 1)
        self.assertTrue(mbr.intersects_circle(3, 0.5, 2.0))

    def test_intersects_circle_corner(self):
        mbr = MBR(0, 1, 0, 1)
        self.assertTrue(mbr.intersects_circle(2, 2, math.sqrt(2) + 0.001))
        self.assertFalse(mbr.intersects_circle(2, 2, math.sqrt(2) - 0.001))

    def test_contains_point(self):
        mbr = MBR(0, 5, 0, 5)
        self.assertTrue(mbr.contains_point(2, 3))
        self.assertFalse(mbr.contains_point(6, 3))

    def test_pack_unpack_roundtrip(self):
        original = MBR(1.1, 2.2, 3.3, 4.4)
        buf = bytearray(32)
        original.pack_into(buf, 0)
        restored = MBR.unpack_from(buf, 0)
        self.assertEqual(original, restored)


if __name__ == "__main__":
    unittest.main()
