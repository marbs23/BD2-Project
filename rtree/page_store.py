import os
import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass
class IOStats:
    reads: int = 0
    writes: int = 0

    def reset(self) -> None:
        self.reads = 0
        self.writes = 0

    def snapshot(self) -> "IOStats":
        return IOStats(self.reads, self.writes)


class PageStore:
    PAGE_SIZE = 4096
    BUFFER_SIZE = 16

    _MAGIC = b"RTREE_01"
    _SB_FMT = "<8s Q Q Q Q"  # magic(8) + root_pid(8) + next_free(8) + free_head(8) + node_count(8)
    _SB_SIZE = struct.calcsize(_SB_FMT)  # 40 bytes, fits in page 0
    _FREE_NEXT_FMT = "<Q"

    SUPERBLOCK_PID = 0

    def __init__(self, filepath: str, stats: IOStats, lock=None, wal: Any = None):
        self.filepath = filepath
        self.stats = stats
        self._wal = wal
        self._lock = lock or threading.Lock()
        self._buffer: OrderedDict[int, bytearray] = OrderedDict()
        self._pinned: set[int] = set()

        if os.path.exists(filepath):
            self._open_existing()
        else:
            self._create_new()

    def _create_new(self) -> None:
        with open(self.filepath, "wb") as f:
            sb = self._encode_superblock(root_pid=0, next_free=1, free_head=0, node_count=0)
            f.write(sb)

    def _open_existing(self) -> None:
        pass

    def _encode_superblock(self, root_pid: int, next_free: int,
                            free_head: int, node_count: int) -> bytes:
        header = struct.pack(self._SB_FMT, self._MAGIC, root_pid, next_free, free_head, node_count)
        return header + bytes(self.PAGE_SIZE - len(header))

    def _decode_superblock(self, data: bytes) -> dict:
        magic, root_pid, next_free, free_head, node_count = struct.unpack_from(self._SB_FMT, data)
        assert magic == self._MAGIC, f"Bad magic: {magic!r}"
        return {"root_pid": root_pid, "next_free": next_free,
                "free_head": free_head, "node_count": node_count}

    def _read_from_disk(self, pid: int) -> bytearray:
        offset = pid * self.PAGE_SIZE
        with open(self.filepath, "rb") as f:
            f.seek(offset)
            data = f.read(self.PAGE_SIZE)
        if len(data) < self.PAGE_SIZE:
            data = data + bytes(self.PAGE_SIZE - len(data))
        return bytearray(data)

    def _write_to_disk(self, pid: int, data: bytes) -> None:
        assert len(data) == self.PAGE_SIZE
        offset = pid * self.PAGE_SIZE
        with open(self.filepath, "r+b") as f:
            f.seek(offset)
            f.write(data)

    def _buffer_insert(self, pid: int, data: bytearray) -> None:
        if pid in self._buffer:
            self._buffer.move_to_end(pid)
            self._buffer[pid] = data
            return
        if len(self._buffer) >= self.BUFFER_SIZE:
            for evict_pid in self._buffer:
                if evict_pid not in self._pinned:
                    self._buffer.pop(evict_pid)
                    break
        self._buffer[pid] = data
        self._buffer.move_to_end(pid)

    def read_page(self, pid: int) -> memoryview:
        if pid in self._buffer:
            self._buffer.move_to_end(pid)
            return memoryview(self._buffer[pid])
        self.stats.reads += 1
        data = self._read_from_disk(pid)
        self._buffer_insert(pid, data)
        return memoryview(self._buffer[pid])

    def write_page(self, pid: int, data: bytes, tx: Any = None) -> None:
        assert len(data) == self.PAGE_SIZE, f"Page must be {self.PAGE_SIZE} bytes, got {len(data)}"
        if tx is not None and self._wal is not None:
            before = bytes(self.read_page(pid))
            self._wal.log_write(int(getattr(tx, "tid")), pid, before, data)
        self.stats.writes += 1
        self._write_to_disk(pid, data)
        self._buffer_insert(pid, bytearray(data))

    def pin_page(self, pid: int) -> None:
        self._pinned.add(pid)

    def unpin_page(self, pid: int) -> None:
        self._pinned.discard(pid)

    def get_superblock(self) -> dict:
        data = bytes(self.read_page(self.SUPERBLOCK_PID))
        return self._decode_superblock(data)

    def flush_superblock(self, root_pid: int, next_free: int,
                         free_head: int, node_count: int, tx: Any = None) -> None:
        sb = self._encode_superblock(root_pid, next_free, free_head, node_count)
        self.write_page(self.SUPERBLOCK_PID, sb, tx=tx)

    def get_root(self) -> int:
        return self.get_superblock()["root_pid"]

    def set_root(self, pid: int, tx: Any = None) -> None:
        sb = self.get_superblock()
        self.flush_superblock(pid, sb["next_free"], sb["free_head"], sb["node_count"], tx=tx)

    def allocate_page(self) -> int:
        with self._lock:
            sb = self.get_superblock()
            if sb["free_head"] != 0:
                new_pid = sb["free_head"]
                freed_data = bytes(self.read_page(new_pid))
                next_free_head = struct.unpack_from(self._FREE_NEXT_FMT, freed_data, 0)[0]
                blank = bytes(self.PAGE_SIZE)
                self._write_to_disk(new_pid, blank)
                self._buffer_insert(new_pid, bytearray(blank))
                self.flush_superblock(sb["root_pid"], sb["next_free"],
                                      next_free_head, sb["node_count"] + 1)
                return new_pid
            else:
                new_pid = sb["next_free"]
                blank = bytes(self.PAGE_SIZE)
                with open(self.filepath, "r+b") as f:
                    f.seek(new_pid * self.PAGE_SIZE)
                    f.write(blank)
                self._buffer_insert(new_pid, bytearray(blank))
                self.flush_superblock(sb["root_pid"], new_pid + 1,
                                      sb["free_head"], sb["node_count"] + 1)
                return new_pid

    def free_page(self, pid: int) -> None:
        with self._lock:
            sb = self.get_superblock()
            page_data = bytearray(self.PAGE_SIZE)
            struct.pack_into(self._FREE_NEXT_FMT, page_data, 0, sb["free_head"])
            self._write_to_disk(pid, bytes(page_data))
            self._buffer.pop(pid, None)
            self._pinned.discard(pid)
            self.flush_superblock(sb["root_pid"], sb["next_free"],
                                  pid, sb["node_count"] - 1)
