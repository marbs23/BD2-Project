"""
ejecutor.py
===========
Capa que conecta el parser SQL con los 4 índices del proyecto.

Flujo:
  SQL -> Scanner -> Parser -> AST -> Ejecutor -> Índice (BPT/Seq/Hash/RTree)

Diferencia respecto a la versión inicial: los imports del RTree fueron
corregidos para apuntar al módulo real (`rtree.rtree`, `rtree.page_store`,
`rtree.geometry`) que vive en este repositorio. También se ajustó la firma
de `PageStore` para incluir el `IOStats` requerido.
"""

import os
import time
from typing import Any, Dict, List, Optional

from parser_sql import (
    parsear,
    NodoCreateTable,
    NodoSelectPuntual,
    NodoSelectRango,
    NodoSelectRadio,
    NodoSelectKNN,
    NodoSelectTodos,
    NodoInsert,
    NodoDelete,
)

# ── índices ─────────────────────────────────────────────────────────────
try:
    from bplustree import BPlusTree, Record as RecordBPT
    BPTREE_OK = True
except ImportError:
    BPTREE_OK = False

try:
    from sequential_file import SeqFile, Record as RecordSEQ
    SEQFILE_OK = True
except ImportError:
    SEQFILE_OK = False

try:
    from extendible_hash import ExtendibleHashFile, Record as RecordHASH
    HASH_OK = True
except ImportError:
    HASH_OK = False

try:
    from rtree.rtree_index import RTree
    from rtree.page_store import PageStore, IOStats
    from rtree.geometry import TID
    RTREE_OK = True
except ImportError:
    RTREE_OK = False


TECNICAS_VALIDAS = {"BPTREE", "SEQUENTIAL", "HASH", "RTREE"}


class ResultadoEjecucion:
    def __init__(self, operacion: str, tabla: str):
        self.operacion = operacion
        self.tabla = tabla
        self.registros: List[Any] = []
        self.afectados: int = 0
        self.ok: bool = True
        self.mensaje: str = ""
        self.tiempo_ms: float = 0.0
        self.reads: int = 0
        self.writes: int = 0

    @property
    def total_io(self) -> int:
        return self.reads + self.writes

    def __repr__(self):
        if self.registros:
            return (f"[{self.operacion}] tabla={self.tabla} "
                    f"filas={len(self.registros)} io={self.total_io} "
                    f"tiempo={self.tiempo_ms:.2f}ms")
        return (f"[{self.operacion}] tabla={self.tabla} ok={self.ok} "
                f"msg='{self.mensaje}' io={self.total_io} "
                f"tiempo={self.tiempo_ms:.2f}ms")


class Ejecutor:
    def __init__(self, directorio: str = "."):
        self.directorio = directorio
        self._catalogo: Dict[str, dict] = {}

    # ── API pública ─────────────────────────────────────────────────────
    def ejecutar(self, sql: str) -> List[ResultadoEjecucion]:
        try:
            programa = parsear(sql)
        except SyntaxError as e:
            r = ResultadoEjecucion("PARSE_ERROR", "")
            r.ok = False
            r.mensaje = str(e)
            return [r]

        return [self._dispatch(n) for n in programa.sentencias]

    def tablas(self) -> List[str]:
        return list(self._catalogo.keys())

    def info_tabla(self, nombre: str) -> Optional[dict]:
        info = self._catalogo.get(nombre)
        if not info:
            return None
        return {
            "tabla": nombre,
            "tecnica": info["tecnica"],
            "col_clave": info["col_clave"],
            "columnas": [(c.nombre, c.tipo) for c in info["columnas"]],
        }

    def cerrar_todo(self) -> None:
        for info in self._catalogo.values():
            try:
                info["indice"].close()
            except Exception:
                pass

    # ── dispatcher ──────────────────────────────────────────────────────
    def _dispatch(self, nodo) -> ResultadoEjecucion:
        if isinstance(nodo, NodoCreateTable):   return self._crear_tabla(nodo)
        if isinstance(nodo, NodoSelectTodos):   return self._select_todos(nodo)
        if isinstance(nodo, NodoSelectPuntual): return self._select_puntual(nodo)
        if isinstance(nodo, NodoSelectRango):   return self._select_rango(nodo)
        if isinstance(nodo, NodoSelectRadio):   return self._select_radio(nodo)
        if isinstance(nodo, NodoSelectKNN):     return self._select_knn(nodo)
        if isinstance(nodo, NodoInsert):        return self._insertar(nodo)
        if isinstance(nodo, NodoDelete):        return self._eliminar(nodo)
        r = ResultadoEjecucion("DESCONOCIDO", "")
        r.ok = False
        r.mensaje = f"Tipo de nodo no soportado: {type(nodo)}"
        return r

    # ── CREATE ──────────────────────────────────────────────────────────
    def crear_tabla_desde_archivo(self, table_name: str, columns: Dict[str, str], file_path: str, tecnica: str = "BPTREE") -> ResultadoEjecucion:
        """
        Crea una tabla y carga datos desde un archivo CSV.
        Delega en _crear_tabla construyendo un NodoCreateTable sintético.
        """
        from parser_sql import NodoColumna, NodoCreateTable
        r = ResultadoEjecucion("CREATE TABLE FROM FILE", table_name)
        t0 = time.time()
        try:
            cols = []
            for i, (nombre, tipo) in enumerate(columns.items()):
                idx = tecnica.upper() if i == 0 else None
                cols.append(NodoColumna(nombre=nombre, tipo=tipo, indice=idx))
            nodo = NodoCreateTable(tabla=table_name, columnas=cols, archivo=file_path)
            r = self._crear_tabla(nodo)
        except Exception as e:
            r.ok = False
            r.mensaje = f"Error creando tabla desde archivo: {e}"
        r.tiempo_ms = (time.time() - t0) * 1000
        return r
    
    def _crear_tabla(self, nodo: NodoCreateTable) -> ResultadoEjecucion:
        r = ResultadoEjecucion("CREATE TABLE", nodo.tabla)
        t0 = time.time()

        col_clave = None
        tecnica = None
        for col in nodo.columnas:
            if col.indice is not None:
                col_clave = col.nombre
                tecnica = col.indice.upper()
                break
        if tecnica is None:
            tecnica = "BPTREE"
            col_clave = nodo.columnas[0].nombre

        if tecnica not in TECNICAS_VALIDAS:
            r.ok = False
            r.mensaje = f"Técnica '{tecnica}' no reconocida. Usa: {TECNICAS_VALIDAS}"
            return r

        disponibles = {"BPTREE": BPTREE_OK, "SEQUENTIAL": SEQFILE_OK,
                       "HASH": HASH_OK, "RTREE": RTREE_OK}
        if not disponibles[tecnica]:
            r.ok = False
            r.mensaje = f"Módulo {tecnica} no disponible"
            return r

        base = os.path.join(self.directorio, nodo.tabla)
        try:
            if tecnica == "BPTREE":
                indice = (BPlusTree.from_csv(f"{base}_bpt.bin", nodo.archivo)
                          if nodo.archivo else BPlusTree(f"{base}_bpt.bin"))
            elif tecnica == "SEQUENTIAL":
                indice = (SeqFile.from_csv(f"{base}_seq.bin", nodo.archivo)
                          if nodo.archivo else SeqFile(f"{base}_seq.bin"))
            elif tecnica == "HASH":
                indice = (ExtendibleHashFile.from_csv(
                            f"{base}_hash.bin", f"{base}_hash.json", nodo.archivo)
                          if nodo.archivo
                          else ExtendibleHashFile(f"{base}_hash.bin", f"{base}_hash.json"))
            else:  # RTREE
                store = PageStore(f"{base}_rtree.bin", IOStats())
                indice = RTree(store)
                if nodo.archivo:
                    # Detectar columnas de coordenadas en el CSV.
                    # Convención: lon_col y lat_col se infieren del header del CSV.
                    # Soporta: lon/lat, longitude/latitude, x/y, longitud/latitud.
                    import csv as _csv
                    _lon_aliases = {"lon", "longitude", "longitud", "x"}
                    _lat_aliases = {"lat", "latitude", "latitud", "y"}
                    with open(nodo.archivo, newline="", encoding="utf-8") as _f:
                        _header = next(_csv.reader(_f))
                    _header_lower = [c.strip().lower() for c in _header]
                    _lon_col = next(
                        (c for c in _header_lower if c in _lon_aliases), None
                    )
                    _lat_col = next(
                        (c for c in _header_lower if c in _lat_aliases), None
                    )
                    if _lon_col is None or _lat_col is None:
                        r.ok = False
                        r.mensaje = (
                            f"El CSV '{nodo.archivo}' no tiene columnas de coordenadas. "
                            f"Se esperan columnas llamadas lon/longitude/longitud/x y "
                            f"lat/latitude/latitud/y. Columnas encontradas: {_header}"
                        )
                        return r
                    indice.bulk_load_from_csv(
                        nodo.archivo, lon_col=_lon_col, lat_col=_lat_col
                    )
        except Exception as e:
            r.ok = False
            r.mensaje = f"Error creando índice: {e}"
            return r

        self._catalogo[nodo.tabla] = {
            "indice": indice,
            "tecnica": tecnica,
            "columnas": nodo.columnas,
            "col_clave": col_clave,
        }
        r.tiempo_ms = (time.time() - t0) * 1000
        self._stats(r, indice)
        r.mensaje = (f"Tabla '{nodo.tabla}' creada con {tecnica} sobre '{col_clave}'"
                     + (f" cargando '{nodo.archivo}'" if nodo.archivo else ""))
        return r

    # ── SELECT todos (sin WHERE) ─────────────────────────────────────────
    def _select_todos(self, nodo: "NodoSelectTodos") -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT ALL", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r
        indice = info["indice"]
        tecnica = info["tecnica"]
        t0 = time.time()
        self._reset(indice)
        try:
            if tecnica == "RTREE":
                r.ok = False
                r.mensaje = "SELECT * sin WHERE no soportado en RTree; usa POINT+RADIUS"
                return r
            if tecnica == "HASH":
                r.ok = False
                r.mensaje = "SELECT * sin WHERE no soportado en ExtendibleHash"
                return r
            # BPTree y Sequential soportan range_search con rango máximo
            registros = indice.range_search(0, 2**31 - 1)
            if nodo.limite is not None:
                registros = registros[:nodo.limite]
            r.registros = registros
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        self._stats(r, indice)
        return r

    # ── SELECT puntual ──────────────────────────────────────────────────
    def _select_puntual(self, nodo: NodoSelectPuntual) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r

        indice = info["indice"]
        tecnica = info["tecnica"]
        t0 = time.time()
        self._reset(indice)
        try:
            if tecnica == "RTREE":
                r.ok = False
                r.mensaje = "RTree requiere POINT(...) IN ... RADIUS r"
                return r
            res = indice.search(nodo.valor)
            r.registros = [res] if res else []
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        self._stats(r, indice)
        if not r.registros and r.ok:
            r.mensaje = f"No se encontró '{nodo.valor}' en {nodo.tabla}"
        return r

    # ── SELECT BETWEEN ──────────────────────────────────────────────────
    def _select_rango(self, nodo: NodoSelectRango) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT BETWEEN", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r
        tecnica = info["tecnica"]
        if tecnica == "HASH":
            r.ok = False
            r.mensaje = "ExtendibleHash no soporta range search"
            return r
        if tecnica == "RTREE":
            r.ok = False
            r.mensaje = "Para RTree usa POINT+RADIUS"
            return r

        indice = info["indice"]
        t0 = time.time()
        self._reset(indice)
        try:
            r.registros = indice.range_search(nodo.inicio, nodo.fin)
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        self._stats(r, indice)
        return r

    # ── SELECT RADIUS ───────────────────────────────────────────────────
    def _select_radio(self, nodo: NodoSelectRadio) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT RADIUS", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r
        if info["tecnica"] != "RTREE":
            r.ok = False
            r.mensaje = "RADIUS requiere índice RTREE"
            return r
        indice = info["indice"]
        t0 = time.time()
        try:
            res = indice.range_search(nodo.x, nodo.y, nodo.radio)
            r.registros = list(getattr(res, "tids", res))
            r.reads = getattr(res, "io_reads", 0)
            r.writes = getattr(res, "io_writes", 0)
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        return r

    # ── SELECT KNN ──────────────────────────────────────────────────────
    def _select_knn(self, nodo: NodoSelectKNN) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT KNN", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r
        if info["tecnica"] != "RTREE":
            r.ok = False
            r.mensaje = "KNN requiere índice RTREE"
            return r
        indice = info["indice"]
        t0 = time.time()
        try:
            res = indice.knn(nodo.x, nodo.y, nodo.k)
            r.registros = list(getattr(res, "tids", res))
            r.reads = getattr(res, "io_reads", 0)
            r.writes = getattr(res, "io_writes", 0)
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        return r

    # ── INSERT ──────────────────────────────────────────────────────────
    def _insertar(self, nodo: NodoInsert) -> ResultadoEjecucion:
        r = ResultadoEjecucion("INSERT", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r
        indice = info["indice"]
        tecnica = info["tecnica"]
        vals = nodo.valores
        t0 = time.time()
        self._reset(indice)
        try:
            if tecnica == "RTREE":
                lon = float(vals[0])
                lat = float(vals[1])
                pid = int(vals[2]) if len(vals) > 2 else 0
                slot = int(vals[3]) if len(vals) > 3 else 0
                indice.insert(lon, lat, TID(pid, slot))
            else:
                rec = self._build_record(vals, tecnica)
                if rec is None:
                    r.ok = False
                    r.mensaje = f"No se pudo construir el registro {vals}"
                    return r
                if tecnica in ("BPTREE", "SEQUENTIAL"):
                    indice.add(rec)
                else:  # HASH
                    indice.insert(rec)
            r.afectados = 1
            r.mensaje = f"Registro insertado en '{nodo.tabla}'"
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        self._stats(r, indice)
        return r

    # ── DELETE ──────────────────────────────────────────────────────────
    def _eliminar(self, nodo: NodoDelete) -> ResultadoEjecucion:
        r = ResultadoEjecucion("DELETE", nodo.tabla)
        info = self._verif(nodo.tabla, r)
        if info is None:
            return r
        if info["tecnica"] == "RTREE":
            r.ok = False
            r.mensaje = "DELETE en RTree requiere coordenadas; usar API directa"
            return r
        indice = info["indice"]
        t0 = time.time()
        self._reset(indice)
        try:
            ok = indice.remove(nodo.valor)
            r.afectados = 1 if ok else 0
            r.ok = ok
            r.mensaje = (f"Registro {nodo.valor} eliminado" if ok
                         else f"Registro {nodo.valor} no encontrado")
        except Exception as e:
            r.ok = False
            r.mensaje = str(e)
        r.tiempo_ms = (time.time() - t0) * 1000
        self._stats(r, indice)
        return r

    # ── helpers ─────────────────────────────────────────────────────────
    def _verif(self, nombre: str, r: ResultadoEjecucion):
        if nombre not in self._catalogo:
            r.ok = False
            r.mensaje = f"Tabla '{nombre}' no existe."
            return None
        return self._catalogo[nombre]

    def _build_record(self, vals: list, tecnica: str):
        try:
            id_ = int(vals[0])
            title = str(vals[1]) if len(vals) > 1 else ""
            author = str(vals[2]) if len(vals) > 2 else ""
            pages = int(vals[3]) if len(vals) > 3 else 0
            rating = float(vals[4]) if len(vals) > 4 else 0.0
            year = int(vals[5]) if len(vals) > 5 else 0
            if tecnica == "BPTREE":
                return RecordBPT(id=id_, title=title, author=author,
                                 pages=pages, rating=rating, year=year)
            if tecnica == "SEQUENTIAL":
                return RecordSEQ(id=id_, title=title, author=author,
                                 pages=pages, rating=rating, year=year)
            if tecnica == "HASH":
                return RecordHASH(id=id_, title=title, author=author,
                                  pages=pages, rating=rating, year=year)
        except (ValueError, IndexError):
            return None
        return None

    def _reset(self, indice) -> None:
        if hasattr(indice, "reset_stats"):
            indice.reset_stats()
        elif hasattr(indice, "reset_counters"):
            indice.reset_counters()

    def _stats(self, r: ResultadoEjecucion, indice) -> None:
        if hasattr(indice, "get_stats"):
            try:
                s = indice.get_stats()
                r.reads = s.get("reads", 0)
                r.writes = s.get("writes", 0)
                # Para BPlusTree, obtener el número de registros del header
                if hasattr(indice, "_read_header"):
                    root_page, total_pages, height, total_records = indice._read_header()
                    r.afectados = total_records
                return
            except Exception:
                pass
        # Para R-Tree, usar las estadísticas del store
        if hasattr(indice, "stats") and hasattr(indice.stats, "snapshot"):
            try:
                snapshot = indice.stats.snapshot()
                r.reads = snapshot.reads
                r.writes = snapshot.writes
                return
            except Exception:
                pass
        if hasattr(indice, "disk_reads"):
            r.reads = indice.disk_reads
            r.writes = indice.disk_writes


# ── demo CLI ────────────────────────────────────────────────────────────
def _print(rs):
    for r in rs:
        ok = "✓" if r.ok else "✗"
        print(f"  {ok} [{r.operacion}] tabla='{r.tabla}' "
              f"filas={len(r.registros)} io={r.total_io} "
              f"afectados={r.afectados} t={r.tiempo_ms:.2f}ms  msg={r.mensaje}")


if __name__ == "__main__":
    for f in ["demo_books_bpt.bin"]:
        if os.path.exists(f):
            os.remove(f)

    db = Ejecutor(".")
    print("▶ CREATE")
    _print(db.ejecutar(
        'CREATE TABLE demo_books (book_key INT INDEX BPTREE, title TEXT, '
        'author TEXT, pages INT, rating FLOAT, year INT);'))

    print("\n▶ INSERT 10")
    sql = " ".join(
        f'INSERT INTO demo_books VALUES ({i}, "L{i}", "A{i}", {100+i}, {3+i*0.1:.1f}, {2000+i});'
        for i in range(1, 11))
    _print(db.ejecutar(sql))

    print("\n▶ SELECT puntual")
    _print(db.ejecutar('SELECT * FROM demo_books WHERE book_key = 5;'))

    print("\n▶ SELECT BETWEEN")
    _print(db.ejecutar('SELECT * FROM demo_books WHERE book_key BETWEEN 3 AND 7;'))

    print("\n▶ DELETE")
    _print(db.ejecutar('DELETE FROM demo_books WHERE book_key = 5;'))

    print("\n▶ SELECT post-DELETE")
    _print(db.ejecutar('SELECT * FROM demo_books WHERE book_key = 5;'))

    db.cerrar_todo()
    for f in ["demo_books_bpt.bin"]:
        if os.path.exists(f):
            os.remove(f)
