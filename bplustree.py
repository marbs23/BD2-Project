from dataclasses import dataclass
from typing import Tuple, List, Optional
import struct
import os

RECORD_FORMAT = "i100s40sifi"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)

PAGE_SIZE = 4096

# TREE HEADER: root_page (int), total_pages (int), height (int), total_records (int)
HEADER_FORMAT = "iiii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# ORDER OF TREE
ORDER = 100
# INTERNAL_NODE HEADER: is_leaf, num_keys, keys..., children...
INTERNAL_NODE_FORMAT = f"ii{ORDER}i{ORDER+1}i"
INTERNAL_NODE_SIZE = struct.calcsize(INTERNAL_NODE_FORMAT)
assert INTERNAL_NODE_SIZE <= PAGE_SIZE, f"Nodo interno ({INTERNAL_NODE_SIZE}B) no cabe en página ({PAGE_SIZE}B)"

# ORDER OF LEAF
ORDER_LEAF = 25
# LEAF_NODE HEADER: is_leaf, num_keys, next_leaf, keys..., records...
LEAF_NODE_FORMAT = f"iii{ORDER_LEAF}i{ORDER_LEAF*RECORD_SIZE}s"
LEAF_NODE_SIZE = struct.calcsize(LEAF_NODE_FORMAT)
assert LEAF_NODE_SIZE <= PAGE_SIZE, f"Leaf ({LEAF_NODE_SIZE}B) does not fit on page ({PAGE_SIZE}B)"

# Record
@dataclass
class Record:
    id: int
    title: str
    author: str
    pages: int
    rating: float
    year: int


class BPlusTree:

    def __init__(self, filename: str):
        self.filename = filename
        self.read_count  = 0
        self.write_count = 0

        if not os.path.exists(self.filename):
            self.file = open(self.filename, "w+b")
            self._write_header(-1, 0, 0, 0)
        else:
            self.file = open(self.filename, "r+b")

    def _read_header(self) -> Tuple[int, int, int, int]:
        self.file.seek(0)
        data = self.file.read(HEADER_SIZE)
        self.read_count += 1
        return struct.unpack(HEADER_FORMAT, data)

    def _write_header(self, root_page: int, total_pages: int, height: int, total_records: int):
        self.file.seek(0)
        self.file.write(struct.pack(HEADER_FORMAT, root_page, total_pages, height, total_records))
        self.file.flush()
        self.write_count += 1

    @staticmethod
    def _page_offset(page_id: int) -> int:
        return HEADER_SIZE + page_id * PAGE_SIZE
    
    def _read_page_raw(self, page_id: int) -> bytes:
        self.file.seek(self._page_offset(page_id))
        data = self.file.read(PAGE_SIZE)
        self.read_count += 1

        # Incomplete page fill with 0's
        if len(data) < PAGE_SIZE:
            data = data.ljust(PAGE_SIZE, b"\x00")
        return data
    
    def _write_page_raw(self, page_id: int, data: bytes):
        data = data.ljust(PAGE_SIZE, b"\x00")
        self.file.seek(self._page_offset(page_id))
        self.file.write(data)
        self.file.flush()
        self.write_count += 1

    def _read_internal_node(self, page_id: int) -> Tuple[int, int, List[int], List[int]]:
        raw = self._read_page_raw(page_id)
        unpacked = struct.unpack_from(INTERNAL_NODE_FORMAT, raw)
        is_leaf  = unpacked[0]
        num_keys = unpacked[1]
        keys     = list(unpacked[2 : 2 + ORDER])[:num_keys]
        children = list(unpacked[2 + ORDER : 2 + ORDER + ORDER + 1])[:num_keys + 1]
        return is_leaf, num_keys, keys, children
    
    def _write_internal_node(self, page_id: int, num_keys: int, keys: List[int], children: List[int]):
        keys_padded     = (keys     + [0] * ORDER)[:ORDER]
        children_padded = (children + [0] * (ORDER + 1))[:ORDER + 1]
        data = struct.pack(INTERNAL_NODE_FORMAT, 0, num_keys, *keys_padded, *children_padded)
        self._write_page_raw(page_id, data)

    def _read_leaf(self, page_id: int) -> Tuple[int, int, List[int], List[Record]]:
        raw = self._read_page_raw(page_id)
        is_leaf, num_keys, next_leaf = struct.unpack_from("iii", raw, 0)
        offset = 12

        keys = []
        for i in range(ORDER_LEAF):
            k = struct.unpack_from("i", raw, offset)[0]
            offset += 4
            keys.append(k)
        keys = keys[:num_keys]

        records = []
        for i in range(ORDER_LEAF):
            rec_bytes = raw[offset: offset + RECORD_SIZE]
            offset += RECORD_SIZE
            if i < num_keys:
                records.append(self._unpack_record(rec_bytes))

        return num_keys, next_leaf, keys, records
    
    def _write_leaf(self, page_id: int, num_keys: int, next_leaf: int,
                    keys: List[int], records: List[Record]):
        data = struct.pack("iii", 1, num_keys, next_leaf)

        keys_padded = (keys + [0] * ORDER_LEAF)[:ORDER_LEAF]
        for k in keys_padded:
            data += struct.pack("i", k)

        for i in range(ORDER_LEAF):
            if i < len(records):
                data += self._pack_record(records[i])
            else:
                data += b"\x00" * RECORD_SIZE

        self._write_page_raw(page_id, data)

    @staticmethod
    def _pack_record(rec: Record) -> bytes:
        return struct.pack(
            RECORD_FORMAT,
            rec.id,
            rec.title.encode("utf-8")[:100].ljust(100, b"\x00"),
            rec.author.encode("utf-8")[:40].ljust(40, b"\x00"),
            rec.pages,
            rec.rating,
            rec.year,
        )
    
    @staticmethod
    def _unpack_record(data: bytes) -> Record:
        values = struct.unpack(RECORD_FORMAT, data)
        return Record(
            id        = values[0],
            title     = values[1].decode("utf-8", errors="ignore").rstrip("\x00").strip(),
            author    = values[2].decode("utf-8", errors="ignore").rstrip("\x00").strip(),
            pages     = values[3],
            rating    = values[4],
            year      = values[5],
        )
    #
    
    # NEW PAGE ON DISK
    def _alloc_page(self) -> int:
        root_page, total_pages, height, total_records = self._read_header()
        new_page_id = total_pages
        self._write_header(root_page, total_pages + 1, height, total_records)
        return new_page_id

    def _is_leaf_page(self, page_id: int) -> bool:
        raw = self._read_page_raw(page_id)
        is_leaf = struct.unpack_from("i", raw, 0)[0]
        return is_leaf == 1

    # SEARCH
    def search(self, id_key: int) -> Optional[Record]:
        root_page, total_pages, height, total_records = self._read_header()
        if root_page == -1:
            return None  # Empty tree

        page_id = root_page
        while not self._is_leaf_page(page_id):
            _, num_keys, keys, children = self._read_internal_node(page_id)

            page_id = children[0]
            for i in range(num_keys):
                if id_key >= keys[i]:
                    page_id = children[i + 1]
                else:
                    break

        num_keys, next_leaf, keys, records = self._read_leaf(page_id)
        for i in range(num_keys):
            if keys[i] == id_key:
                if records[i].id == -1:  # mark as deleted
                    return None
                return records[i]
        return None