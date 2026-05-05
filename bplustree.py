import struct
from dataclasses import dataclass

RECORD_FORMAT = "i100s40sifi"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)

PAGE_SIZE = 4096

# root_page (int), total_pages (int), height (int), total_records (int)
HEADER_FORMAT = "iiii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# ORDER OF TREE
ORDER = 100

# is_leaf, num_keys, keys..., children...
INTERNAL_NODE_FORMAT = f"ii{ORDER}i{ORDER+1}i"
INTERNAL_NODE_SIZE = struct.calcsize(INTERNAL_NODE_FORMAT)

ORDER_LEAF = 25

# is_leaf, num_keys, next_leaf, keys..., records...
LEAF_NODE_FORMAT = f"iii{ORDER_LEAF}i{ORDER_LEAF*RECORD_SIZE}s"
LEAF_NODE_SIZE = struct.calcsize(LEAF_NODE_FORMAT)
assert LEAF_NODE_SIZE <= PAGE_SIZE, f"Leaf ({LEAF_NODE_SIZE}B) does not fit on page ({PAGE_SIZE}B)"

INTERNAL_ORDER = ORDER_LEAF
INTERNAL_FMT   = f"ii{INTERNAL_ORDER}i{INTERNAL_ORDER+1}i"
INTERNAL_SIZE  = struct.calcsize(INTERNAL_FMT)
assert INTERNAL_SIZE <= PAGE_SIZE, f"Nodo interno ({INTERNAL_SIZE}B) no cabe en página ({PAGE_SIZE}B)"

# Record
@dataclass
class Record:
    id: int
    title: str
    author: str
    pages: int
    rating: float
    year: int