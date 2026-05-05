import struct
from dataclasses import dataclass
from heapq import heappush, heappop
from itertools import count
from typing import Optional

from .geometry import MBR, TID
from .page_store import IOStats, PageStore

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
    def __init__(self, store: PageStore):
        self.store = store
        self.stats = store.stats
        sb = store.get_superblock()
        if sb["root_pid"] == 0:
            root_pid = store.allocate_page()
            root = RTreeNode(root_pid, is_leaf=True, level=0, parent_pid=0)
            store.write_page(root_pid, root.serialize())
            store.set_root(root_pid)
            store.pin_page(root_pid)
        else:
            store.pin_page(sb["root_pid"])

    def _root_pid(self) -> int:
        return self.store.get_root()

    def _read_node(self, pid: int) -> RTreeNode:
        data = self.store.read_page(pid)
        return RTreeNode.deserialize(pid, data)

    def _write_node(self, node: RTreeNode) -> None:
        self.store.write_page(node.page_id, node.serialize())

    def _choose_subtree(self, node: RTreeNode, mbr: MBR) -> RTreeNode:
        if node.is_leaf:
            return node
        best_idx = min(range(len(node.entries)),
                       key=lambda i: (node.entries[i]["mbr"].area_enlargement(mbr),
                                      node.entries[i]["mbr"].area()))
        child = self._read_node(node.entries[best_idx]["child_pid"])
        return self._choose_subtree(child, mbr)

    def _linear_split(self, node: RTreeNode, new_entry: dict) -> tuple[RTreeNode, RTreeNode]:
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
        new_node = RTreeNode(new_pid, node.is_leaf, node.level, node.parent_pid)
        new_node.entries = group2

        if not node.is_leaf:
            for e in new_node.entries:
                child = self._read_node(e["child_pid"])
                child.parent_pid = new_pid
                self._write_node(child)

        return node, new_node

    def _adjust_tree(self, node: RTreeNode, split_node: Optional[RTreeNode] = None) -> None:
        if node.parent_pid == 0 and node.page_id == self._root_pid():
            if split_node:
                new_root_pid = self.store.allocate_page()
                new_root = RTreeNode(new_root_pid, is_leaf=False,
                                     level=node.level + 1, parent_pid=0)
                node.parent_pid = new_root_pid
                split_node.parent_pid = new_root_pid
                self._write_node(node)
                self._write_node(split_node)
                new_root.entries = [
                    {"mbr": node.get_mbr(), "child_pid": node.page_id},
                    {"mbr": split_node.get_mbr(), "child_pid": split_node.page_id},
                ]
                self._write_node(new_root)
                self.store.unpin_page(node.page_id)
                self.store.set_root(new_root_pid)
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
                parent, new_parent = self._linear_split(parent, split_entry)
                self._write_node(parent)
                self._write_node(new_parent)
                self._adjust_tree(parent, new_parent)
            else:
                parent.entries.append(split_entry)
                self._write_node(parent)
                self._adjust_tree(parent)
        else:
            self._write_node(parent)
            self._adjust_tree(parent)

    def insert(self, lon: float, lat: float, tid: TID) -> None:
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

    def range_search(self, lon: float, lat: float, radius: float) -> SearchResult:
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

    def knn(self, lon: float, lat: float, k: int) -> SearchResult:
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

    def delete(self, lon: float, lat: float, tid: TID) -> bool:
        leaf, idx = self._find_leaf(self._root_pid(), lon, lat, tid)
        if leaf is None:
            return False
        leaf.entries.pop(idx)
        self._write_node(leaf)
        self._condense_tree(leaf)
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

    def _condense_tree(self, node: RTreeNode) -> None:
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
            self._write_node(parent)
            current = parent

        root = self._read_node(self._root_pid())
        if not root.is_leaf and len(root.entries) == 1:
            new_root_pid = root.entries[0]["child_pid"]
            self.store.unpin_page(root.page_id)
            self.store.free_page(root.page_id)
            new_root = self._read_node(new_root_pid)
            new_root.parent_pid = 0
            self._write_node(new_root)
            self.store.set_root(new_root_pid)
            self.store.pin_page(new_root_pid)

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

    def bulk_load_from_csv(self, path: str, lon_col: str, lat_col: str,
                            data_page_id: int = 1, delimiter: str = ",",
                            skip_invalid: bool = True) -> int:
        import csv
        n = 0
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            if lon_col not in reader.fieldnames or lat_col not in reader.fieldnames:
                raise ValueError(f"CSV missing columns: {lon_col}, {lat_col}")
            for slot, row in enumerate(reader):
                try:
                    lon = float(row[lon_col])
                    lat = float(row[lat_col])
                except (TypeError, ValueError):
                    if skip_invalid:
                        continue
                    raise
                self.insert(lon, lat, TID(data_page_id, slot))
                n += 1
        return n

    def update_tid(self, lon: float, lat: float, old_tid: TID, new_tid: TID) -> bool:
        result = self._find_leaf(self._root_pid(), lon, lat, old_tid)
        leaf, idx = result
        if leaf is None:
            return False
        leaf.entries[idx]["tid_page"] = new_tid.page_id
        leaf.entries[idx]["tid_slot"] = new_tid.slot_id
        self._write_node(leaf)
        return True
