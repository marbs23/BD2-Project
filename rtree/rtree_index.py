import struct
import threading
from dataclasses import dataclass
from heapq import heappush, heappop
from itertools import count
from typing import TYPE_CHECKING, Literal, Optional

from .geometry import MBR, TID
from .lock_manager import LockManager
from .page_store import IOStats, PageStore

if TYPE_CHECKING:
    from .transaction import Transaction

PAGE_SIZE = PageStore.PAGE_SIZE

HEADER_FMT = "<BB H Q Q I 8x"
ENTRY_FMT = "<Q H 6x 4d"

assert struct.calcsize(HEADER_FMT) == 32, f"Header size mismatch: {struct.calcsize(HEADER_FMT)}"
assert struct.calcsize(ENTRY_FMT) == 48, f"Entry size mismatch: {struct.calcsize(ENTRY_FMT)}"

HEADER_SIZE = 32
ENTRY_SIZE = 48
M = (PAGE_SIZE - HEADER_SIZE) // ENTRY_SIZE   # 84
m = -(-M * 2 // 5)                             # 34


@dataclass
class SearchResult:
    tids: list
    visited_mbrs: list
    query_point: tuple
    query_radius: Optional[float]
    io_reads: int
    io_writes: int


class RTreeNode:
    def __init__(self, page_id: int, is_leaf: bool, level: int, parent_pid: int = 0):
        self.page_id = page_id
        self.is_leaf = is_leaf
        self.level = level
        self.parent_pid = parent_pid
        self.entries: list[dict] = []

    def get_mbr(self) -> Optional[MBR]:
        if not self.entries:
            return None
        result = self.entries[0]["mbr"]
        for e in self.entries[1:]:
            result = result.expand(e["mbr"])
        return result

    def is_full(self) -> bool:
        return len(self.entries) >= M

    def is_underfull(self) -> bool:
        return len(self.entries) < m

    def serialize(self) -> bytes:
        buf = bytearray(PAGE_SIZE)
        struct.pack_into(HEADER_FMT, buf, 0,
                         int(self.is_leaf), self.level,
                         len(self.entries), self.page_id,
                         self.parent_pid, 0)
        for i, e in enumerate(self.entries):
            offset = HEADER_SIZE + i * ENTRY_SIZE
            mbr: MBR = e["mbr"]
            if self.is_leaf:
                child_q = e.get("tid_page", 0)
                slot_h = e.get("tid_slot", 0)
            else:
                child_q = e.get("child_pid", 0)
                slot_h = 0
            struct.pack_into(ENTRY_FMT, buf, offset,
                             child_q, slot_h,
                             mbr.min_lon, mbr.max_lon, mbr.min_lat, mbr.max_lat)
        return bytes(buf)

    @classmethod
    def deserialize(cls, page_id: int, data) -> "RTreeNode":
        raw = bytes(data)
        is_leaf_b, level, n_entries, pid, parent_pid, _ = struct.unpack_from(HEADER_FMT, raw, 0)
        node = cls(page_id, bool(is_leaf_b), level, parent_pid)
        for i in range(n_entries):
            offset = HEADER_SIZE + i * ENTRY_SIZE
            child_q, slot_h, min_lon, max_lon, min_lat, max_lat = struct.unpack_from(ENTRY_FMT, raw, offset)
            mbr = MBR(min_lon, max_lon, min_lat, max_lat)
            if node.is_leaf:
                node.entries.append({"mbr": mbr, "tid_page": child_q, "tid_slot": slot_h})
            else:
                node.entries.append({"mbr": mbr, "child_pid": child_q})
        return node


class RTree:
    """
    **Nota arquitectónica (D2)**: El descenso con lock coupling usa candados S en rama
    de lectura (cangrejo) y cadena X root→hoja para mutaciones con transacción, para
    que ``_adjust_tree`` pueda escribir ancestros ya cubiertos por la cadena pesimista.
    Strict 2PL puro en todo el descenso es opcional vía ``strict_2pl`` (reservado).
    """

    def __init__(
        self,
        store: PageStore,
        lock_mgr: Optional[LockManager] = None,
        strict_2pl: bool = False,
    ):
        self.store = store
        self.stats = store.stats
        self._lock_mgr = lock_mgr if lock_mgr is not None else LockManager()
        self._strict_2pl = strict_2pl
        self._tid_gen = count(1)
        self._tid_mu = threading.Lock()
        sb = store.get_superblock()
        if sb["root_pid"] == 0:
            root_pid = store.allocate_page()
            root = RTreeNode(root_pid, is_leaf=True, level=0, parent_pid=0)
            store.write_page(root_pid, root.serialize())
            store.set_root(root_pid)
            store.pin_page(root_pid)
        else:
            store.pin_page(sb["root_pid"])

    def begin_transaction(self) -> "Transaction":
        from .transaction import Transaction

        wal = getattr(self.store, "_wal", None)
        if wal is None:
            raise RuntimeError(
                "Se requiere PageStore(..., wal=WriteAheadLog(...)) para begin_transaction()"
            )
        with self._tid_mu:
            tid = next(self._tid_gen)
        return Transaction(tid, self.store, self._lock_mgr, wal)

    def _root_pid(self) -> int:
        return self.store.get_root()

    def _read_node(self, pid: int) -> RTreeNode:
        data = self.store.read_page(pid)
        return RTreeNode.deserialize(pid, data)

    def _write_node(self, node: RTreeNode, tx: Optional["Transaction"] = None) -> None:
        self.store.write_page(node.page_id, node.serialize(), tx=tx)

    def _child_pid_for_point(self, node: RTreeNode, lon: float, lat: float) -> int:
        point_mbr = MBR.from_point(lon, lat)
        best_idx = min(
            range(len(node.entries)),
            key=lambda i: (
                node.entries[i]["mbr"].area_enlargement(point_mbr),
                node.entries[i]["mbr"].area(),
            ),
        )
        return node.entries[best_idx]["child_pid"]

    def _leaf_pid_for_point_readonly(self, lon: float, lat: float) -> int:
        """
        Desciende desde la raíz sin candados (solo lectura) con la misma heurística
        que el crabbing. Sirve para comprobar, tras un descenso optimista, que el
        árbol no cambió bajo nuestros pies (otra transacción pudo partir un ancestro).
        """
        pid = self._root_pid()
        while True:
            node = self._read_node(pid)
            if node.is_leaf:
                return pid
            pid = self._child_pid_for_point(node, lon, lat)

    def _descend_optimistic(
        self,
        lon: float,
        lat: float,
        leaf_lock: Literal["S", "X"],
        tx: "Transaction",
    ) -> tuple[list[int], int, bool]:
        """
        Cangrejo S hasta la hoja; en hoja upgrade a X si ``leaf_lock == 'X'``.

        Tras fijar la hoja, compara con un descenso read-only: si difiere, otro hilo
        pudo reestructurar mientras se soltaban S en padres → ``need_retry=True``;
        el caller debe ``release_all`` y tomar el camino pesimista (D2).

        TODO opcional: detectar cambio de MBR del padre sin split (estructura igual,
        distinta geometría de entrada) — mismo gancho ``need_retry``.
        """
        pid = self._root_pid()
        held: list[int] = []
        while True:
            self._lock_mgr.acquire(tx, pid, "S")
            held.append(pid)
            node = self._read_node(pid)
            if node.is_leaf:
                if leaf_lock == "X":
                    self._lock_mgr.acquire(tx, pid, "X")
                stable = self._leaf_pid_for_point_readonly(lon, lat)
                need_retry = stable != pid
                return held, pid, need_retry
            child_pid = self._child_pid_for_point(node, lon, lat)
            self._lock_mgr.acquire(tx, child_pid, "S")
            self._lock_mgr.release(tx, pid)
            held.pop()
            pid = child_pid

    def _descend_pessimistic(self, lon: float, lat: float, tx: "Transaction") -> tuple[list[int], int]:
        """
        Cadena X sobre root→…→hoja (sin soltar ancestros) para mutaciones que propagan
        al árbol (insert/delete + ``_adjust_tree``).
        """
        path: list[int] = []
        pid = self._root_pid()
        while True:
            self._lock_mgr.acquire(tx, pid, "X")
            path.append(pid)
            node = self._read_node(pid)
            if node.is_leaf:
                return path, pid
            pid = self._child_pid_for_point(node, lon, lat)

    def _choose_subtree(self, node: RTreeNode, mbr: MBR) -> RTreeNode:
        if node.is_leaf:
            return node
        best_idx = min(range(len(node.entries)),
                       key=lambda i: (node.entries[i]["mbr"].area_enlargement(mbr),
                                      node.entries[i]["mbr"].area()))
        child = self._read_node(node.entries[best_idx]["child_pid"])
        return self._choose_subtree(child, mbr)

    def _linear_split(
        self,
        node: RTreeNode,
        new_entry: dict,
        tx: Optional["Transaction"] = None,
    ) -> tuple[RTreeNode, RTreeNode]:
        all_entries = node.entries + [new_entry]

        def pick_seeds_linear():
            best_sep, seeds = -1.0, (0, 1)
            dims = [
                (lambda e: e["mbr"].min_lon, lambda e: e["mbr"].max_lon),
                (lambda e: e["mbr"].min_lat, lambda e: e["mbr"].max_lat),
            ]
            for get_min, get_max in dims:
                vals_min = [(get_min(e), i) for i, e in enumerate(all_entries)]
                vals_max = [(get_max(e), i) for i, e in enumerate(all_entries)]
                vals_min.sort(); vals_max.sort()
                highest_low = vals_min[-1]
                lowest_high = vals_max[0]
                span = (max(get_max(e) for e in all_entries) -
                        min(get_min(e) for e in all_entries))
                sep = abs(highest_low[0] - lowest_high[0]) / span if span else 0
                if sep > best_sep:
                    best_sep = sep
                    seeds = (highest_low[1], lowest_high[1])
            return seeds

        i1, i2 = pick_seeds_linear()
        if i1 == i2:
            i2 = (i2 + 1) % len(all_entries)

        group1 = [all_entries[i1]]
        group2 = [all_entries[i2]]
        remaining = [e for i, e in enumerate(all_entries) if i not in (i1, i2)]

        def get_group_mbr(g):
            result = g[0]["mbr"]
            for e in g[1:]:
                result = result.expand(e["mbr"])
            return result

        for entry in remaining:
            if len(group1) + len(remaining) - remaining.index(entry) - 1 + len(group2) < m + 1:
                pass
            if len(group1) >= M + 1 - m:
                group2.append(entry)
            elif len(group2) >= M + 1 - m:
                group1.append(entry)
            else:
                mbr1 = get_group_mbr(group1)
                mbr2 = get_group_mbr(group2)
                e1 = mbr1.area_enlargement(entry["mbr"])
                e2 = mbr2.area_enlargement(entry["mbr"])
                if e1 < e2 or (e1 == e2 and mbr1.area() <= mbr2.area()):
                    group1.append(entry)
                else:
                    group2.append(entry)

        node.entries = group1
        new_pid = self.store.allocate_page()
        if tx is not None:
            self._lock_mgr.acquire(tx, new_pid, "X")
        new_node = RTreeNode(new_pid, node.is_leaf, node.level, node.parent_pid)
        new_node.entries = group2

        if not node.is_leaf:
            for e in new_node.entries:
                child = self._read_node(e["child_pid"])
                child.parent_pid = new_pid
                self._write_node(child, tx)

        return node, new_node

    def _adjust_tree(
        self,
        node: RTreeNode,
        split_node: Optional[RTreeNode] = None,
        tx: Optional["Transaction"] = None,
    ) -> None:
        if node.parent_pid == 0 and node.page_id == self._root_pid():
            if split_node:
                new_root_pid = self.store.allocate_page()
                if tx is not None:
                    self._lock_mgr.acquire(tx, new_root_pid, "X")
                new_root = RTreeNode(new_root_pid, is_leaf=False,
                                     level=node.level + 1, parent_pid=0)
                node.parent_pid = new_root_pid
                split_node.parent_pid = new_root_pid
                self._write_node(node, tx)
                self._write_node(split_node, tx)
                new_root.entries = [
                    {"mbr": node.get_mbr(), "child_pid": node.page_id},
                    {"mbr": split_node.get_mbr(), "child_pid": split_node.page_id},
                ]
                self._write_node(new_root, tx)
                self.store.unpin_page(node.page_id)
                self.store.set_root(new_root_pid, tx=tx)
                self.store.pin_page(new_root_pid)
            return

        parent = self._read_node(node.parent_pid)
        for e in parent.entries:
            if e["child_pid"] == node.page_id:
                e["mbr"] = node.get_mbr()
                break

        if split_node:
            split_entry = {"mbr": split_node.get_mbr(), "child_pid": split_node.page_id}
            if parent.is_full():
                parent, new_parent = self._linear_split(parent, split_entry, tx)
                self._write_node(parent, tx)
                self._write_node(new_parent, tx)
                self._adjust_tree(parent, new_parent, tx)
            else:
                parent.entries.append(split_entry)
                self._write_node(parent, tx)
                self._adjust_tree(parent, None, tx)
        else:
            self._write_node(parent, tx)
            self._adjust_tree(parent, None, tx)

    def _insert_autocommit(self, lon: float, lat: float, tid: TID) -> None:
        point_mbr = MBR.from_point(lon, lat)
        root = self._read_node(self._root_pid())
        leaf = self._choose_subtree(root, point_mbr)
        entry = {"mbr": point_mbr, "tid_page": tid.page_id, "tid_slot": tid.slot_id}
        if leaf.is_full():
            leaf, new_leaf = self._linear_split(leaf, entry)
            self._write_node(leaf)
            self._write_node(new_leaf)
            self._adjust_tree(leaf, new_leaf)
        else:
            leaf.entries.append(entry)
            self._write_node(leaf)
            self._adjust_tree(leaf)

    def insert(self, lon: float, lat: float, tid: TID, tx: Optional["Transaction"] = None) -> None:
        if tx is None:
            self._insert_autocommit(lon, lat, tid)
            return

        # Descenso optimista (need_retry interno si la hoja read-only ≠ hoja bloqueada).
        self._descend_optimistic(lon, lat, "X", tx)
        self._lock_mgr.release_all(tx.tid)

        _, leaf_pid = self._descend_pessimistic(lon, lat, tx)
        point_mbr = MBR.from_point(lon, lat)
        leaf = self._read_node(leaf_pid)
        entry = {"mbr": point_mbr, "tid_page": tid.page_id, "tid_slot": tid.slot_id}
        if leaf.is_full():
            leaf, new_leaf = self._linear_split(leaf, entry, tx)
            self._write_node(leaf, tx)
            self._write_node(new_leaf, tx)
            self._adjust_tree(leaf, new_leaf, tx)
        else:
            leaf.entries.append(entry)
            self._write_node(leaf, tx)
            self._adjust_tree(leaf, None, tx)

    def range_search(
        self,
        lon: float,
        lat: float,
        radius: float,
        tx: Optional["Transaction"] = None,
    ) -> SearchResult:
        if tx is None:
            before = self.stats.snapshot()
            results: list[TID] = []
            visited_mbrs: list[tuple] = []
            stack = [self._root_pid()]

            while stack:
                pid = stack.pop()
                node = self._read_node(pid)
                node_mbr = node.get_mbr()
                if node_mbr:
                    visited_mbrs.append(node_mbr.to_tuple())
                for e in node.entries:
                    if not e["mbr"].intersects_circle(lon, lat, radius):
                        continue
                    if node.is_leaf:
                        results.append(TID(e["tid_page"], e["tid_slot"]))
                    else:
                        stack.append(e["child_pid"])

            after = self.stats.snapshot()
            return SearchResult(
                tids=results,
                visited_mbrs=visited_mbrs,
                query_point=(lon, lat),
                query_radius=radius,
                io_reads=after.reads - before.reads,
                io_writes=after.writes - before.writes,
            )

        before = self.stats.snapshot()
        results: list[TID] = []
        visited_mbrs: list[tuple] = []

        def walk(pid: int) -> None:
            self._lock_mgr.acquire(tx, pid, "S")
            try:
                node = self._read_node(pid)
                node_mbr = node.get_mbr()
                if node_mbr:
                    visited_mbrs.append(node_mbr.to_tuple())
                for e in node.entries:
                    if not e["mbr"].intersects_circle(lon, lat, radius):
                        continue
                    if node.is_leaf:
                        results.append(TID(e["tid_page"], e["tid_slot"]))
                    else:
                        walk(e["child_pid"])
            finally:
                self._lock_mgr.release(tx, pid)

        walk(self._root_pid())
        after = self.stats.snapshot()
        return SearchResult(
            tids=results,
            visited_mbrs=visited_mbrs,
            query_point=(lon, lat),
            query_radius=radius,
            io_reads=after.reads - before.reads,
            io_writes=after.writes - before.writes,
        )

    def knn(self, lon: float, lat: float, k: int, tx: Optional["Transaction"] = None) -> SearchResult:
        before = self.stats.snapshot()
        heap: list = []
        results: list[tuple] = []
        visited_mbrs: list[tuple] = []
        tie_break = count()

        root_node = self._read_node(self._root_pid())
        root_mbr = root_node.get_mbr()
        root_d = root_mbr.mindist_sq(lon, lat) if root_mbr else 0.0
        heappush(heap, (root_d, next(tie_break), False, self._root_pid()))

        while heap:
            d, _, is_entry, item = heappop(heap)
            if is_entry:
                results.append((d, item))
                if len(results) == k:
                    break
                continue
            # Node to expand: prune only when we already have k results
            if len(results) == k and d > results[-1][0]:
                break
            node = self._read_node(item)
            mbr = node.get_mbr()
            if mbr:
                visited_mbrs.append(mbr.to_tuple())
            for e in node.entries:
                md = e["mbr"].mindist_sq(lon, lat)
                if len(results) == k and md > results[-1][0]:
                    continue
                if node.is_leaf:
                    heappush(heap, (md, next(tie_break), True,
                                    TID(e["tid_page"], e["tid_slot"])))
                else:
                    heappush(heap, (md, next(tie_break), False, e["child_pid"]))

        after = self.stats.snapshot()
        return SearchResult(
            tids=[tid for _, tid in results[:k]],
            visited_mbrs=visited_mbrs,
            query_point=(lon, lat),
            query_radius=None,
            io_reads=after.reads - before.reads,
            io_writes=after.writes - before.writes,
        )

    def delete(self, lon: float, lat: float, tid: TID, tx: Optional["Transaction"] = None) -> bool:
        if tx is None:
            leaf, idx = self._find_leaf(self._root_pid(), lon, lat, tid)
            if leaf is None:
                return False
            leaf.entries.pop(idx)
            self._write_node(leaf)
            self._condense_tree(leaf)
            return True

        _, leaf_pid = self._descend_pessimistic(lon, lat, tx)
        leaf = self._read_node(leaf_pid)
        idx = -1
        for i, e in enumerate(leaf.entries):
            if e["tid_page"] == tid.page_id and e["tid_slot"] == tid.slot_id:
                idx = i
                break
        if idx < 0:
            return False
        leaf.entries.pop(idx)
        self._write_node(leaf, tx)
        self._condense_tree(leaf, tx)
        return True

    def _find_leaf(self, pid: int, lon: float, lat: float,
                   tid: TID) -> tuple[Optional[RTreeNode], int]:
        node = self._read_node(pid)
        if node.is_leaf:
            for i, e in enumerate(node.entries):
                if e["tid_page"] == tid.page_id and e["tid_slot"] == tid.slot_id:
                    return node, i
            return None, -1
        for e in node.entries:
            if e["mbr"].contains_point(lon, lat):
                result, idx = self._find_leaf(e["child_pid"], lon, lat, tid)
                if result is not None:
                    return result, idx
        return None, -1

    def _condense_tree(self, node: RTreeNode, tx: Optional["Transaction"] = None) -> None:
        orphans: list[dict] = []
        current = node

        while current.parent_pid != 0 or current.page_id != self._root_pid():
            if current.parent_pid == 0:
                break
            parent = self._read_node(current.parent_pid)
            if current.is_underfull():
                for i, e in enumerate(parent.entries):
                    if e["child_pid"] == current.page_id:
                        parent.entries.pop(i)
                        break
                orphans.extend(current.entries)
                self.store.free_page(current.page_id)
            else:
                for e in parent.entries:
                    if e["child_pid"] == current.page_id:
                        e["mbr"] = current.get_mbr()
                        break
            self._write_node(parent, tx)
            current = parent

        root = self._read_node(self._root_pid())
        if not root.is_leaf and len(root.entries) == 1:
            new_root_pid = root.entries[0]["child_pid"]
            self.store.unpin_page(root.page_id)
            self.store.free_page(root.page_id)
            new_root = self._read_node(new_root_pid)
            new_root.parent_pid = 0
            self._write_node(new_root, tx)
            self.store.set_root(new_root_pid, tx=tx)
            self.store.pin_page(new_root_pid)

        # TODO (durabilidad / opcional +2 pts): si ``tx`` está activo, estas ``insert``
        # deben ejecutarse con el mismo ``tx`` para que huérfanos pasen por WAL y locks;
        # hoy son autocommit y un crash entre ``free_page`` y reinsert deja entradas
        # inconsistentes respecto al log.
        for entry in orphans:
            if "tid_page" in entry:
                self.insert(entry["mbr"].min_lon, entry["mbr"].min_lat,
                            TID(entry["tid_page"], entry["tid_slot"]))
            else:
                self._reinsert_subtree(entry["child_pid"])

    def _reinsert_subtree(self, pid: int) -> None:
        node = self._read_node(pid)
        if node.is_leaf:
            for e in node.entries:
                self.insert(e["mbr"].min_lon, e["mbr"].min_lat,
                            TID(e["tid_page"], e["tid_slot"]))
        else:
            for e in node.entries:
                self._reinsert_subtree(e["child_pid"])
        self.store.free_page(pid)

    def get_stats(self):
        snapshot = self.stats.snapshot()
        return {
            "reads": snapshot.reads,
            "writes": snapshot.writes,
            "total_io": snapshot.reads + snapshot.writes
        }

    def bulk_load_from_csv(self, path: str, lon_col: str, lat_col: str,
                            data_page_id: int = 1, delimiter: str = ",",
                            skip_invalid: bool = True) -> int:
        """
        Carga masiva desde CSV usando STR (Sort-Tile-Recursive).
        Construye el árbol de abajo hacia arriba en memoria y escribe todo de una vez.
        O(n log n) con I/O mínimo — mucho más rápido que insertar punto a punto.
        """
        import csv as _csv
        import math

        # 1. Leer todos los puntos en RAM
        points = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f, delimiter=delimiter)
            if lon_col not in reader.fieldnames or lat_col not in reader.fieldnames:
                raise ValueError(f"CSV missing columns: {lon_col}, {lat_col}")
            for slot, row in enumerate(reader):
                try:
                    lon = float(row[lon_col])
                    lat = float(row[lat_col])
                    points.append((lon, lat, data_page_id + slot // 65535, slot % 65535))
                except (TypeError, ValueError):
                    if skip_invalid:
                        continue
                    raise

        n = len(points)
        if n == 0:
            return 0

        # 2. STR sort: franjas por lon, dentro de cada franja ordenar por lat
        num_leaves = math.ceil(n / M)
        num_slices = max(1, math.ceil(math.sqrt(num_leaves)))
        points.sort(key=lambda p: p[0])
        slice_size = num_slices * M
        sorted_pts = []
        for i in range(0, n, slice_size):
            chunk = points[i:i + slice_size]
            chunk.sort(key=lambda p: p[1])
            sorted_pts.extend(chunk)

        # 3. Construir árbol bottom-up
        # Cada nivel es una lista de (RTreeNode, page_id)
        # Empezamos con las hojas
        def make_leaves():
            nodes = []
            for i in range(0, len(sorted_pts), M):
                batch = sorted_pts[i:i + M]
                pid = self.store.allocate_page()
                node = RTreeNode(pid, is_leaf=True, level=0, parent_pid=0)
                for lon, lat, dp, slot in batch:
                    node.entries.append({
                        "mbr": MBR.from_point(lon, lat),
                        "tid_page": dp,
                        "tid_slot": slot,
                    })
                nodes.append(node)
            return nodes

        def make_internal_level(children: list, level: int) -> list:
            nodes = []
            for i in range(0, len(children), M):
                batch = children[i:i + M]
                pid = self.store.allocate_page()
                node = RTreeNode(pid, is_leaf=False, level=level, parent_pid=0)
                for child in batch:
                    child.parent_pid = pid
                    node.entries.append({
                        "mbr": child.get_mbr(),
                        "child_pid": child.page_id,
                    })
                nodes.append(node)
            return nodes

        current = make_leaves()
        level = 1
        all_nodes = list(current)
        while len(current) > 1:
            current = make_internal_level(current, level)
            all_nodes.extend(current)
            level += 1

        root = current[0]
        root.parent_pid = 0

        # 4. Escribir todos los nodos a disco
        for node in all_nodes:
            self.store.write_page(node.page_id, node.serialize())

        # 5. Actualizar la raíz en el superbloque
        old_root = self.store.get_root()
        self.store.unpin_page(old_root)
        self.store.set_root(root.page_id)
        self.store.pin_page(root.page_id)

        return n

    def validate_structure(self) -> dict:
        """Auditoría estructural. Lanza AssertionError si la estructura es inválida.

        Verifica:
          - todas las hojas están al mismo nivel
          - MBR de cada padre contiene la unión de MBRs de sus hijos
          - parent_pid es consistente
          - fill factor: nodos no-root tienen >= m y <= M entries (root excepto)
          - no hay ciclos
        Devuelve métricas: depth, n_nodes, n_leaves, n_entries, total_overlap_area, total_dead_area.
        """
        root_pid = self._root_pid()
        leaf_levels: set = set()
        n_nodes = 0
        n_leaves = 0
        n_entries = 0
        total_overlap = 0.0
        total_dead = 0.0
        seen: set = set()

        def visit(pid: int, expected_parent: int, expected_level: Optional[int] = None):
            nonlocal n_nodes, n_leaves, n_entries, total_overlap, total_dead
            assert pid not in seen, f"cycle detected at pid={pid}"
            seen.add(pid)
            node = self._read_node(pid)
            n_nodes += 1
            assert node.parent_pid == expected_parent, (
                f"node {pid} has parent_pid={node.parent_pid}, expected {expected_parent}"
            )
            if pid != root_pid:
                assert m <= len(node.entries) <= M, (
                    f"node {pid} fill violation: {len(node.entries)} entries (m={m}, M={M})"
                )
            else:
                assert len(node.entries) <= M
            if node.is_leaf:
                n_leaves += 1
                leaf_levels.add(node.level)
            n_entries += len(node.entries)

            if not node.is_leaf:
                # children MBRs deben caber dentro del MBR del padre (que es la unión)
                parent_union = node.get_mbr()
                child_areas = []
                for e in node.entries:
                    child = self._read_node(e["child_pid"])
                    child_mbr = child.get_mbr()
                    assert child_mbr is None or (
                        child_mbr.min_lon >= e["mbr"].min_lon - 1e-9
                        and child_mbr.max_lon <= e["mbr"].max_lon + 1e-9
                        and child_mbr.min_lat >= e["mbr"].min_lat - 1e-9
                        and child_mbr.max_lat <= e["mbr"].max_lat + 1e-9
                    ), f"child {e['child_pid']} MBR escapes parent entry MBR"
                    child_areas.append(e["mbr"].area())
                    visit(e["child_pid"], pid, node.level - 1)
                # overlap aproximado entre pares
                for i in range(len(node.entries)):
                    for j in range(i + 1, len(node.entries)):
                        a, b = node.entries[i]["mbr"], node.entries[j]["mbr"]
                        if a.intersects_mbr(b):
                            ov_lon = min(a.max_lon, b.max_lon) - max(a.min_lon, b.min_lon)
                            ov_lat = min(a.max_lat, b.max_lat) - max(a.min_lat, b.min_lat)
                            total_overlap += max(ov_lon, 0) * max(ov_lat, 0)
                if parent_union is not None:
                    total_dead += max(0.0, parent_union.area() - sum(child_areas))

        visit(root_pid, 0)
        assert len(leaf_levels) <= 1, f"leaves at multiple levels: {leaf_levels}"
        return {
            "depth": (max(leaf_levels) if leaf_levels else 0),
            "n_nodes": n_nodes,
            "n_leaves": n_leaves,
            "n_entries": n_entries,
            "total_overlap_area": total_overlap,
            "total_dead_area": total_dead,
        }

    def update_tid(self, lon: float, lat: float, old_tid: TID, new_tid: TID) -> bool:
        result = self._find_leaf(self._root_pid(), lon, lat, old_tid)
        leaf, idx = result
        if leaf is None:
            return False
        leaf.entries[idx]["tid_page"] = new_tid.page_id
        leaf.entries[idx]["tid_slot"] = new_tid.slot_id
        self._write_node(leaf)
        return True
