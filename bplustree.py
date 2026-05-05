from dataclasses import dataclass
from typing import Tuple
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

        # INCOMPLETE PAGE FILL WITH 0's
        if len(data) < PAGE_SIZE:
            data = data.ljust(PAGE_SIZE, b"\x00")
        return data
    
    def _write_page_raw(self, page_id: int, data: bytes):
        data = data.ljust(PAGE_SIZE, b"\x00")
        self.file.seek(self._page_offset(page_id))
        self.file.write(data)
        self.file.flush()
        self.write_count += 1
