"""Smoke test del ejecutor SQL contra los 4 índices."""
import os
import shutil
import tempfile
import unittest


class TestEjecutorSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, db, sql):
        rs = db.ejecutar(sql)
        for r in rs:
            self.assertTrue(r.ok, f"falló: {r.operacion} → {r.mensaje}")
        return rs

    def test_bptree_flow(self):
        from ejecutor import Ejecutor
        db = Ejecutor(self.tmp)
        self._run(db, 'CREATE TABLE t (id INT INDEX BPTREE, title TEXT, '
                      'author TEXT, pages INT, rating FLOAT, year INT);')
        for i in range(1, 6):
            self._run(db, f'INSERT INTO t VALUES ({i}, "L{i}", "A{i}", {i}, {i}.0, 2020);')
        rs = self._run(db, 'SELECT * FROM t WHERE id = 3;')
        self.assertEqual(len(rs[0].registros), 1)
        rs = self._run(db, 'SELECT * FROM t WHERE id BETWEEN 2 AND 4;')
        self.assertEqual(len(rs[0].registros), 3)
        self._run(db, 'DELETE FROM t WHERE id = 3;')
        db.cerrar_todo()

    def test_hash_flow(self):
        from ejecutor import Ejecutor
        db = Ejecutor(self.tmp)
        self._run(db, 'CREATE TABLE h (id INT INDEX HASH, title TEXT, '
                      'author TEXT, pages INT, rating FLOAT, year INT);')
        for i in range(1, 11):
            self._run(db, f'INSERT INTO h VALUES ({i}, "L{i}", "A{i}", {i}, {i}.0, 2020);')
        rs = self._run(db, 'SELECT * FROM h WHERE id = 7;')
        self.assertEqual(len(rs[0].registros), 1)
        # range no soportado
        rs = db.ejecutar('SELECT * FROM h WHERE id BETWEEN 1 AND 5;')
        self.assertFalse(rs[0].ok)
        db.cerrar_todo()

    def test_rtree_flow(self):
        from ejecutor import Ejecutor
        db = Ejecutor(self.tmp)
        self._run(db, 'CREATE TABLE pts (lon FLOAT INDEX RTREE, lat FLOAT);')
        for i in range(20):
            x = -77.0 + i * 0.01
            y = 12.0 + i * 0.01
            self._run(db, f'INSERT INTO pts VALUES ({x}, {y}, {i}, 0);')
        rs = self._run(db, 'SELECT * FROM pts WHERE coords IN (POINT(-76.95, 12.05), RADIUS 0.10);')
        self.assertGreater(len(rs[0].registros), 0)
        rs = self._run(db, 'SELECT * FROM pts WHERE coords IN (POINT(-76.95, 12.05), K 5);')
        self.assertEqual(len(rs[0].registros), 5)
        db.cerrar_todo()


if __name__ == "__main__":
    unittest.main()
