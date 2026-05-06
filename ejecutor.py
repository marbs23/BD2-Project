"""
ejecutor.py
===========
Capa que conecta el parser SQL con los 4 índices del proyecto.

Flujo completo:
  SQL string
      ↓  Scanner
  Lista de tokens
      ↓  Parser
  Nodos AST  (NodoCreateTable, NodoSelectPuntual, etc.)
      ↓  Ejecutor
  Llamada al índice correcto  (BPlusTree / SeqFile / ExtendibleHashFile / RTree)
      ↓
  Resultado + estadísticas de disco

Uso:
    from ejecutor import Ejecutor
    db = Ejecutor()
    db.ejecutar('CREATE TABLE books (book_key INT INDEX BPTREE) FROM FILE "books.csv";')
    db.ejecutar('SELECT * FROM books WHERE book_key = 6;')
    db.ejecutar('SELECT * FROM books WHERE book_key BETWEEN 1 AND 10;')
    db.ejecutar('INSERT INTO books VALUES (500, "mi libro", "autor", 100, 4.5, 2020);')
    db.ejecutar('DELETE FROM books WHERE book_key = 500;')
"""

import time
import os
from typing import Any, Dict, List, Optional

# ── parser (mismo archivo que construimos) ─────────────────────────────────────
from parser_sql import (
    parsear,
    NodoPrograma,
    NodoCreateTable,
    NodoSelectPuntual,
    NodoSelectRango,
    NodoSelectRadio,
    NodoSelectKNN,
    NodoInsert,
    NodoDelete,
)

# ── índices del proyecto ───────────────────────────────────────────────────────
# Importamos con try/except para que el ejecutor funcione aunque falte alguno
try:
    from bplustree import BPlusTree, Record as RecordBPT
    BPTREE_OK = True
except ImportError:
    BPTREE_OK = False
    print("[Ejecutor] BPlusTree no disponible")

try:
    from sequential_file import SeqFile, Record as RecordSEQ
    SEQFILE_OK = True
except ImportError:
    SEQFILE_OK = False
    print("[Ejecutor] SeqFile no disponible")

try:
    from extendible_hash import ExtendibleHashFile, Record as RecordHASH
    HASH_OK = True
except ImportError:
    HASH_OK = False
    print("[Ejecutor] ExtendibleHashFile no disponible")

try:
    from rtree.rtree_index import RTree          # ajusta el import a tu estructura de carpetas
    RTREE_OK = True
except ImportError:
    RTREE_OK = False
    print("[Ejecutor] RTree no disponible")


# ── técnicas soportadas ────────────────────────────────────────────────────────
TECNICAS_VALIDAS = {"BPTREE", "SEQUENTIAL", "HASH", "RTREE"}


# ══════════════════════════════════════════════════════════════════════════════
# RESULTADO: lo que devuelve cada ejecución
# ══════════════════════════════════════════════════════════════════════════════
class ResultadoEjecucion:
    def __init__(self, operacion: str, tabla: str):
        self.operacion   = operacion   # "SELECT", "INSERT", etc.
        self.tabla       = tabla
        self.registros   : List[Any]   = []   # filas devueltas (para SELECT)
        self.afectados   : int         = 0    # filas insertadas/borradas
        self.ok          : bool        = True
        self.mensaje     : str         = ""
        self.tiempo_ms   : float       = 0.0
        self.reads       : int         = 0
        self.writes      : int         = 0

    @property
    def total_io(self): return self.reads + self.writes

    def __repr__(self):
        if self.registros:
            return (f"[{self.operacion}] tabla={self.tabla} "
                    f"filas={len(self.registros)} "
                    f"io={self.total_io} tiempo={self.tiempo_ms:.2f}ms")
        return (f"[{self.operacion}] tabla={self.tabla} "
                f"ok={self.ok} msg='{self.mensaje}' "
                f"io={self.total_io} tiempo={self.tiempo_ms:.2f}ms")


# ══════════════════════════════════════════════════════════════════════════════
# EJECUTOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
class Ejecutor:
    """
    Mantiene un catálogo de tablas en memoria.
    Cada tabla tiene:
      - índice    : instancia de BPlusTree / SeqFile / ExtendibleHashFile / RTree
      - tecnica   : "BPTREE" | "SEQUENTIAL" | "HASH" | "RTREE"
      - columnas  : lista de NodoColumna (para saber el esquema)
      - col_clave : nombre de la columna con INDEX (la clave del índice)
    """

    def __init__(self, directorio: str = "."):
        """
        directorio: carpeta donde se guardarán los archivos .bin de cada tabla.
        """
        self.directorio = directorio
        # catálogo: { nombre_tabla → info_tabla }
        self._catalogo: Dict[str, dict] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # PUNTO DE ENTRADA PÚBLICO
    # ──────────────────────────────────────────────────────────────────────────
    def ejecutar(self, sql: str) -> List[ResultadoEjecucion]:
        """
        Parsea y ejecuta una o más sentencias SQL separadas por ;
        Devuelve una lista de ResultadoEjecucion (uno por sentencia).
        """
        try:
            programa = parsear(sql)
        except SyntaxError as e:
            r = ResultadoEjecucion("PARSE_ERROR", "")
            r.ok      = False
            r.mensaje = str(e)
            return [r]

        resultados = []
        for nodo in programa.sentencias:
            r = self._ejecutar_nodo(nodo)
            resultados.append(r)
        return resultados

    # ──────────────────────────────────────────────────────────────────────────
    # DISPATCHER — decide qué método llamar según el tipo de nodo
    # ──────────────────────────────────────────────────────────────────────────
    def _ejecutar_nodo(self, nodo) -> ResultadoEjecucion:
        if   isinstance(nodo, NodoCreateTable):   return self._crear_tabla(nodo)
        elif isinstance(nodo, NodoSelectPuntual): return self._select_puntual(nodo)
        elif isinstance(nodo, NodoSelectRango):   return self._select_rango(nodo)
        elif isinstance(nodo, NodoSelectRadio):   return self._select_radio(nodo)
        elif isinstance(nodo, NodoSelectKNN):     return self._select_knn(nodo)
        elif isinstance(nodo, NodoInsert):        return self._insertar(nodo)
        elif isinstance(nodo, NodoDelete):        return self._eliminar(nodo)
        else:
            r = ResultadoEjecucion("DESCONOCIDO", "")
            r.ok = False; r.mensaje = f"Tipo de nodo no soportado: {type(nodo)}"
            return r

    # ══════════════════════════════════════════════════════════════════════════
    # CREATE TABLE
    # ══════════════════════════════════════════════════════════════════════════
    def _crear_tabla(self, nodo: NodoCreateTable) -> ResultadoEjecucion:
        r = ResultadoEjecucion("CREATE TABLE", nodo.tabla)
        t0 = time.time()

        # 1. detectar qué columna tiene INDEX y qué técnica usa
        col_clave = None
        tecnica   = None
        for col in nodo.columnas:
            if col.indice is not None:
                col_clave = col.nombre
                tecnica   = col.indice.upper()
                break

        if tecnica is None:
            # sin INDEX explícito usamos B+Tree por defecto
            tecnica   = "BPTREE"
            col_clave = nodo.columnas[0].nombre

        if tecnica not in TECNICAS_VALIDAS:
            r.ok = False
            r.mensaje = f"Técnica '{tecnica}' no reconocida. Usa: {TECNICAS_VALIDAS}"
            return r

        # 2. verificar disponibilidad del módulo
        if tecnica == "BPTREE"     and not BPTREE_OK:
            r.ok=False; r.mensaje="BPlusTree no importado"; return r
        if tecnica == "SEQUENTIAL" and not SEQFILE_OK:
            r.ok=False; r.mensaje="SeqFile no importado"; return r
        if tecnica == "HASH"       and not HASH_OK:
            r.ok=False; r.mensaje="ExtendibleHashFile no importado"; return r
        if tecnica == "RTREE"      and not RTREE_OK:
            r.ok=False; r.mensaje="RTree no importado"; return r

        # 3. construir rutas de archivo
        base = os.path.join(self.directorio, nodo.tabla)

        # 4. crear el índice
        try:
            if tecnica == "BPTREE":
                if nodo.archivo:
                    indice = BPlusTree.from_csv(f"{base}_bpt.bin", nodo.archivo)
                else:
                    indice = BPlusTree(f"{base}_bpt.bin")

            elif tecnica == "SEQUENTIAL":
                if nodo.archivo:
                    indice = SeqFile.from_csv(f"{base}_seq.bin", nodo.archivo)
                else:
                    indice = SeqFile(f"{base}_seq.bin")

            elif tecnica == "HASH":
                if nodo.archivo:
                    indice = ExtendibleHashFile.from_csv(
                        f"{base}_hash.bin", f"{base}_hash.json", nodo.archivo)
                else:
                    indice = ExtendibleHashFile(
                        f"{base}_hash.bin", f"{base}_hash.json")

            elif tecnica == "RTREE":
                # El RTree de tu proyecto usa PageStore — lo inicializamos vacío
                # Si tienes bulk_load_from_csv puedes llamarlo aquí
                from rtree.rtree_index import RTree
                from rtree.geometry import PageStore
                store  = PageStore(f"{base}_rtree.bin")
                indice = RTree(store)
                if nodo.archivo:
                    indice.bulk_load_from_csv(
                        nodo.archivo, lon_col="longitude", lat_col="latitude")

        except Exception as e:
            r.ok=False; r.mensaje=f"Error creando índice: {e}"; return r

        # 5. registrar en el catálogo
        self._catalogo[nodo.tabla] = {
            "indice"   : indice,
            "tecnica"  : tecnica,
            "columnas" : nodo.columnas,
            "col_clave": col_clave,
        }

        # 6. métricas
        r.tiempo_ms = (time.time() - t0) * 1000
        r = self._agregar_stats(r, indice)
        r.mensaje = (f"Tabla '{nodo.tabla}' creada con técnica {tecnica} "
                     f"sobre columna '{col_clave}'"
                     + (f" cargando '{nodo.archivo}'" if nodo.archivo else ""))
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # SELECT puntual → search()
    # ══════════════════════════════════════════════════════════════════════════
    def _select_puntual(self, nodo: NodoSelectPuntual) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT", nodo.tabla)
        info = self._verificar_tabla(nodo.tabla, r)
        if info is None: return r

        indice  = info["indice"]
        tecnica = info["tecnica"]
        t0 = time.time()
        self._reset_stats(indice)

        try:
            if tecnica == "RTREE":
                # RTree no tiene search puntual directo; usamos range con radio 0
                res = indice.range_search(float(nodo.valor), float(nodo.valor), 0)
                r.registros = res.tids if res else []
            else:
                resultado = indice.search(nodo.valor)
                r.registros = [resultado] if resultado else []
        except Exception as e:
            r.ok=False; r.mensaje=str(e)

        r.tiempo_ms = (time.time() - t0) * 1000
        r = self._agregar_stats(r, indice)
        if not r.registros:
            r.mensaje = f"No se encontró '{nodo.valor}' en {nodo.tabla}"
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # SELECT rango → range_search()
    # ══════════════════════════════════════════════════════════════════════════
    def _select_rango(self, nodo: NodoSelectRango) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT BETWEEN", nodo.tabla)
        info = self._verificar_tabla(nodo.tabla, r)
        if info is None: return r

        indice  = info["indice"]
        tecnica = info["tecnica"]

        if tecnica == "HASH":
            r.ok=False
            r.mensaje="ExtendibleHash no soporta rangeSearch"
            return r
        if tecnica == "RTREE":
            r.ok=False
            r.mensaje="Para RTree usa POINT+RADIUS en lugar de BETWEEN"
            return r

        t0 = time.time()
        self._reset_stats(indice)

        try:
            r.registros = indice.range_search(nodo.inicio, nodo.fin)
        except Exception as e:
            r.ok=False; r.mensaje=str(e)

        r.tiempo_ms = (time.time() - t0) * 1000
        r = self._agregar_stats(r, indice)
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # SELECT espacial radio → RTree.range_search()
    # ══════════════════════════════════════════════════════════════════════════
    def _select_radio(self, nodo: NodoSelectRadio) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT RADIUS", nodo.tabla)
        info = self._verificar_tabla(nodo.tabla, r)
        if info is None: return r

        if info["tecnica"] != "RTREE":
            r.ok=False
            r.mensaje=f"SELECT con RADIUS solo funciona con índice RTREE (tabla usa {info['tecnica']})"
            return r

        indice = info["indice"]
        t0 = time.time()

        try:
            resultado = indice.range_search(nodo.x, nodo.y, nodo.radio)
            r.registros = resultado.tids
            r.reads  = resultado.io_reads
            r.writes = resultado.io_writes
        except Exception as e:
            r.ok=False; r.mensaje=str(e)

        r.tiempo_ms = (time.time() - t0) * 1000
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # SELECT KNN → RTree.knn()
    # ══════════════════════════════════════════════════════════════════════════
    def _select_knn(self, nodo: NodoSelectKNN) -> ResultadoEjecucion:
        r = ResultadoEjecucion("SELECT KNN", nodo.tabla)
        info = self._verificar_tabla(nodo.tabla, r)
        if info is None: return r

        if info["tecnica"] != "RTREE":
            r.ok=False
            r.mensaje=f"SELECT con K solo funciona con índice RTREE (tabla usa {info['tecnica']})"
            return r

        indice = info["indice"]
        t0 = time.time()

        try:
            resultado = indice.knn(nodo.x, nodo.y, nodo.k)
            r.registros = resultado.tids
            r.reads  = resultado.io_reads
            r.writes = resultado.io_writes
        except Exception as e:
            r.ok=False; r.mensaje=str(e)

        r.tiempo_ms = (time.time() - t0) * 1000
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # INSERT → add() / insert()
    # ══════════════════════════════════════════════════════════════════════════
    def _insertar(self, nodo: NodoInsert) -> ResultadoEjecucion:
        r = ResultadoEjecucion("INSERT", nodo.tabla)
        info = self._verificar_tabla(nodo.tabla, r)
        if info is None: return r

        indice  = info["indice"]
        tecnica = info["tecnica"]
        cols    = info["columnas"]
        t0 = time.time()
        self._reset_stats(indice)

        try:
            # construimos el Record según la técnica
            # esperamos los valores en el mismo orden que las columnas definidas
            vals = nodo.valores
            rec  = self._construir_record(vals, tecnica)
            if rec is None:
                r.ok=False
                r.mensaje=f"No se pudo construir el registro con valores {vals}"
                return r

            if tecnica in ("BPTREE", "SEQUENTIAL"):
                indice.add(rec)
            elif tecnica == "HASH":
                indice.insert(rec)
            elif tecnica == "RTREE":
                # RTree necesita lon, lat y un TID
                from rtree.rtree_index import TID
                lon = float(vals[0]); lat = float(vals[1])
                tid = TID(page_id=0, slot_id=0)   # TID simbólico
                indice.insert(lon, lat, tid)

            r.afectados = 1
            r.mensaje   = f"Registro insertado en '{nodo.tabla}'"

        except Exception as e:
            r.ok=False; r.mensaje=str(e)

        r.tiempo_ms = (time.time() - t0) * 1000
        r = self._agregar_stats(r, indice)
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # DELETE → remove()
    # ══════════════════════════════════════════════════════════════════════════
    def _eliminar(self, nodo: NodoDelete) -> ResultadoEjecucion:
        r = ResultadoEjecucion("DELETE", nodo.tabla)
        info = self._verificar_tabla(nodo.tabla, r)
        if info is None: return r

        indice  = info["indice"]
        tecnica = info["tecnica"]
        t0 = time.time()
        self._reset_stats(indice)

        try:
            if tecnica == "RTREE":
                r.ok=False
                r.mensaje="DELETE en RTree requiere coordenadas. Usa delete(lon, lat, tid) directamente."
                return r

            clave = nodo.valor
            ok    = indice.remove(clave)
            r.afectados = 1 if ok else 0
            r.ok        = ok
            r.mensaje   = (f"Registro {clave} eliminado de '{nodo.tabla}'"
                           if ok else
                           f"Registro {clave} no encontrado en '{nodo.tabla}'")

        except Exception as e:
            r.ok=False; r.mensaje=str(e)

        r.tiempo_ms = (time.time() - t0) * 1000
        r = self._agregar_stats(r, indice)
        return r

    # ══════════════════════════════════════════════════════════════════════════
    # HELPERS INTERNOS
    # ══════════════════════════════════════════════════════════════════════════
    def _verificar_tabla(self, nombre: str, r: ResultadoEjecucion):
        """Devuelve info de la tabla o None si no existe (y rellena r con el error)."""
        if nombre not in self._catalogo:
            r.ok = False
            r.mensaje = (f"Tabla '{nombre}' no existe. "
                         f"Ejecuta primero CREATE TABLE {nombre} ...")
            return None
        return self._catalogo[nombre]

    def _construir_record(self, vals: list, tecnica: str):
        """
        Construye el Record correcto según la técnica.
        Se asume el orden:  id, title, author, pages, rating, year
        compatible con el CSV del proyecto.
        """
        try:
            id_    = int(vals[0])
            title  = str(vals[1]) if len(vals) > 1 else ""
            author = str(vals[2]) if len(vals) > 2 else ""
            pages  = int(vals[3]) if len(vals) > 3 else 0
            rating = float(vals[4]) if len(vals) > 4 else 0.0
            year   = int(vals[5]) if len(vals) > 5 else 0

            if tecnica == "BPTREE" and BPTREE_OK:
                return RecordBPT(id=id_, title=title, author=author,
                                 pages=pages, rating=rating, year=year)
            elif tecnica == "SEQUENTIAL" and SEQFILE_OK:
                return RecordSEQ(id=id_, title=title, author=author,
                                 pages=pages, rating=rating, year=year)
            elif tecnica == "HASH" and HASH_OK:
                return RecordHASH(id=id_, title=title, author=author,
                                  pages=pages, rating=rating, year=year)
        except (ValueError, IndexError):
            return None
        return None

    def _reset_stats(self, indice):
        """Resetea los contadores de I/O si el índice lo soporta."""
        if hasattr(indice, "reset_stats"):
            indice.reset_stats()
        elif hasattr(indice, "reset_counters"):
            indice.reset_counters()

    def _agregar_stats(self, r: ResultadoEjecucion, indice) -> ResultadoEjecucion:
        """Copia reads/writes del índice al resultado."""
        if hasattr(indice, "get_stats"):
            stats    = indice.get_stats()
            r.reads  = stats.get("reads",  0)
            r.writes = stats.get("writes", 0)
        elif hasattr(indice, "disk_reads"):
            r.reads  = indice.disk_reads
            r.writes = indice.disk_writes
        return r

    # ──────────────────────────────────────────────────────────────────────────
    # ACCESO AL CATÁLOGO (útil para el frontend)
    # ──────────────────────────────────────────────────────────────────────────
    def tablas(self) -> List[str]:
        return list(self._catalogo.keys())

    def info_tabla(self, nombre: str) -> Optional[dict]:
        info = self._catalogo.get(nombre)
        if not info: return None
        return {
            "tabla"    : nombre,
            "tecnica"  : info["tecnica"],
            "col_clave": info["col_clave"],
            "columnas" : [(c.nombre, c.tipo) for c in info["columnas"]],
        }

    def cerrar_todo(self):
        """Cierra todos los archivos abiertos."""
        for nombre, info in self._catalogo.items():
            try:
                info["indice"].close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# DEMO — prueba el ejecutor con BPlusTree (el único que tenemos disponible aquí)
# ══════════════════════════════════════════════════════════════════════════════
def _imprimir_resultado(resultados):
    for r in resultados:
        estado = "✓" if r.ok else "✗"
        print(f"\n  {estado} [{r.operacion}] tabla='{r.tabla}'")
        if r.registros:
            print(f"    filas devueltas: {len(r.registros)}")
            for reg in r.registros[:3]:   # mostramos máximo 3
                print(f"      → {reg}")
            if len(r.registros) > 3:
                print(f"      ... y {len(r.registros)-3} más")
        if r.mensaje:
            print(f"    msg: {r.mensaje}")
        print(f"    io: {r.reads} reads + {r.writes} writes = {r.total_io} total")
        print(f"    tiempo: {r.tiempo_ms:.3f} ms")


if __name__ == "__main__":
    import os
    # limpiar archivos de prueba anteriores
    for f in ["demo_books_bpt.bin"]:
        if os.path.exists(f): os.remove(f)

    print("=" * 65)
    print("  DEMO EJECUTOR SQL — B+ Tree")
    print("=" * 65)

    db = Ejecutor(directorio=".")

    # ── 1. CREATE TABLE (sin CSV, solo estructura) ──────────────────────────
    print("\n▶ CREATE TABLE")
    r = db.ejecutar(
        'CREATE TABLE demo_books '
        '(book_key INT INDEX BPTREE, title TEXT, author TEXT, '
        ' pages INT, average_rating FLOAT, published_date INT);'
    )
    _imprimir_resultado(r)

    # ── 2. INSERT varios registros ───────────────────────────────────────────
    print("\n▶ INSERT (10 registros)")
    sentencias_insert = " ".join([
        f'INSERT INTO demo_books VALUES ({i}, "Libro {i}", "Autor {i}", {100+i}, {3.0 + i*0.1:.1f}, {2000+i});'
        for i in range(1, 11)
    ])
    r = db.ejecutar(sentencias_insert)
    _imprimir_resultado(r)

    # ── 3. SELECT puntual ────────────────────────────────────────────────────
    print("\n▶ SELECT puntual (book_key = 5)")
    r = db.ejecutar("SELECT * FROM demo_books WHERE book_key = 5;")
    _imprimir_resultado(r)

    # ── 4. SELECT rango ──────────────────────────────────────────────────────
    print("\n▶ SELECT BETWEEN (book_key BETWEEN 3 AND 7)")
    r = db.ejecutar("SELECT * FROM demo_books WHERE book_key BETWEEN 3 AND 7;")
    _imprimir_resultado(r)

    # ── 5. DELETE ────────────────────────────────────────────────────────────
    print("\n▶ DELETE (book_key = 5)")
    r = db.ejecutar("DELETE FROM demo_books WHERE book_key = 5;")
    _imprimir_resultado(r)

    # ── 6. SELECT después de borrar ──────────────────────────────────────────
    print("\n▶ SELECT puntual después de DELETE (book_key = 5, debe ser vacío)")
    r = db.ejecutar("SELECT * FROM demo_books WHERE book_key = 5;")
    _imprimir_resultado(r)

    # ── 7. Error: tabla no existe ────────────────────────────────────────────
    print("\n▶ SELECT en tabla inexistente (debe dar error)")
    r = db.ejecutar("SELECT * FROM tabla_fantasma WHERE book_key = 1;")
    _imprimir_resultado(r)

    # ── 8. Error: sintaxis incorrecta ────────────────────────────────────────
    print("\n▶ SQL con error de sintaxis")
    r = db.ejecutar("SELECT FROM demo_books WHERE book_key = 1;")
    _imprimir_resultado(r)

    # ── 9. Múltiples sentencias en un solo string ────────────────────────────
    print("\n▶ Múltiples sentencias en un call")
    r = db.ejecutar("""
        INSERT INTO demo_books VALUES (99, "Libro 99", "Autor 99", 300, 4.9, 2024);
        SELECT * FROM demo_books WHERE book_key = 99;
        DELETE FROM demo_books WHERE book_key = 99;
        SELECT * FROM demo_books WHERE book_key = 99;
    """)
    _imprimir_resultado(r)

    print("\n▶ Info de tabla")
    print(f"  tablas activas: {db.tablas()}")
    print(f"  info: {db.info_tabla('demo_books')}")

    db.cerrar_todo()
    # limpiar
    for f in ["demo_books_bpt.bin"]:
        if os.path.exists(f): os.remove(f)

    print("\n" + "=" * 65)
    print("  Demo finalizada")
    print("=" * 65)