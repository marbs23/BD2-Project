
import struct, os, csv, json
from dataclasses import dataclass
from typing import Optional, List

RECORD_FORMAT      = "i100s40sifi"
RECORD_SIZE        = struct.calcsize(RECORD_FORMAT)   # 156 bytes
PAGE_SIZE          = 4096
BUCKET_HEADER_FMT  = "ii"                             # local_depth, n_records
BUCKET_HEADER_SIZE = struct.calcsize(BUCKET_HEADER_FMT)  # 8 bytes
BUCKET_CAPACITY    = (PAGE_SIZE - BUCKET_HEADER_SIZE) // RECORD_SIZE  # 26


@dataclass
class Record:
    id:     int
    title:  str
    author: str
    pages:  int
    rating: float
    year:   int


def _pack(r: Record) -> bytes:
    return struct.pack(
        RECORD_FORMAT,
        r.id,
        r.title.encode("utf-8")[:100].ljust(100, b"\x00"),
        r.author.encode("utf-8")[:40 ].ljust(40,  b"\x00"),
        r.pages, r.rating, r.year,
    )

def _unpack(data: bytes) -> Record:
    rid, title, author, pages, rating, year = struct.unpack(RECORD_FORMAT, data)
    return Record(
        id=rid,
        title =title.decode("utf-8").rstrip("\x00"),
        author=author.decode("utf-8").rstrip("\x00"),
        pages=pages, rating=round(rating, 2), year=year,
    )

#Funcion de direccionamiento indexado
def _bits(key: int, depth: int) -> int:
    # profundidad total de bits a considerar para el hashing (global_depth o local_depth)
    return key % (2** depth)


class ExtendibleHashFile:

    def __init__(self, data_path: str, index_path: str):
        self.data_path  = data_path
        self.index_path = index_path
        self.disk_reads = self.disk_writes = 0

        if os.path.exists(data_path) and os.path.exists(index_path):
            self._f = open(data_path, "r+b")
            self._load_dir()
        else:
            self._f = open(data_path, "w+b")
            self.global_depth = 1
            self.directory    = [0, PAGE_SIZE]   # two initial buckets
            self._alloc_bucket(1)                # bucket 0  @ offset 0
            self._alloc_bucket(1)                # bucket 1  @ offset PAGE_SIZE
            self._save_dir()

    def _save_dir(self):
        with open(self.index_path, "w") as f:
            json.dump({"global_depth": self.global_depth,
                       "directory":    self.directory}, f)

    def _load_dir(self):
        with open(self.index_path) as f:
            d = json.load(f)
        self.global_depth = d["global_depth"]
        self.directory    = d["directory"]

    def _alloc_bucket(self, local_depth: int) -> int:
        """Append a fresh empty bucket page; return its byte offset."""
        self._f.seek(0, os.SEEK_END)
        off = self._f.tell()
        self._f.write(struct.pack(BUCKET_HEADER_FMT, local_depth, 0)
                      + b"\x00" * (PAGE_SIZE - BUCKET_HEADER_SIZE))
        self._f.flush()
        self.disk_writes += 1
        return off

    def _read_bucket(self, off: int):
        """Return (local_depth, [Record, ...])."""
        self._f.seek(off)
        page = self._f.read(PAGE_SIZE)
        self.disk_reads += 1
        ld, n = struct.unpack(BUCKET_HEADER_FMT, page[:BUCKET_HEADER_SIZE])
        recs  = []
        for i in range(n):
            s = BUCKET_HEADER_SIZE + i * RECORD_SIZE
            recs.append(_unpack(page[s: s + RECORD_SIZE]))
        return ld, recs

    def _write_bucket(self, off: int, ld: int, records: List[Record]):
        """Serialise bucket to disk (always exactly PAGE_SIZE bytes)."""
        body = b"".join(_pack(r) for r in records)
        body += b"\x00" * (PAGE_SIZE - BUCKET_HEADER_SIZE - len(body))
        self._f.seek(off)
        self._f.write(struct.pack(BUCKET_HEADER_FMT, ld, len(records)) + body)
        self._f.flush()
        self.disk_writes += 1

    def _split(self, off: int, records: List[Record], old_ld: int):
        """
        Split the bucket at byte-offset `off`.
        `records` is the current (over-full) contents passed in memory.
        `old_ld`  is the bucket's current local depth.
        """
        new_ld = old_ld + 1

        # Double the directory if needed
        if old_ld == self.global_depth:
            self.global_depth += 1
            self.directory = self.directory * 2
            self._save_dir()

        # Allocate sibling bucket
        new_off = self._alloc_bucket(new_ld)

        # Redistribute using bit (new_ld - 1) as discriminant
        bit  = new_ld - 1
        keep = [r for r in records if not ((_bits(r.id, new_ld) >> bit) & 1)]
        move = [r for r in records if      (_bits(r.id, new_ld) >> bit) & 1]

        self._write_bucket(off,     new_ld, keep)
        self._write_bucket(new_off, new_ld, move)

        # Re-point directory entries
        for i in range(len(self.directory)):
            if self.directory[i] == off and ((_bits(i, new_ld) >> bit) & 1):
                self.directory[i] = new_off
        self._save_dir()

        # Recursive guard for pathological keys (all identical hash bits)
        if len(keep) > BUCKET_CAPACITY:
            self._split(off,     keep, new_ld)
        if len(move) > BUCKET_CAPACITY:
            self._split(new_off, move, new_ld)

    # ── public API ─────────────────────────────────────────────────────────────
    def insert(self, rec: Record):
        di  = _bits(rec.id, self.global_depth)
        off = self.directory[di]
        ld, records = self._read_bucket(off)

        if any(r.id == rec.id for r in records):
            return                            # duplicate → skip

        records.append(rec)

        if len(records) <= BUCKET_CAPACITY:
            self._write_bucket(off, ld, records)
        else:
            # Never write an overfull page; split directly from memory
            self._split(off, records, ld)

    def search(self, key: int) -> Optional[Record]:
        """Point lookup — exactly 1 disk read."""
        off = self.directory[_bits(key, self.global_depth)]
        _, records = self._read_bucket(off)
        return next((r for r in records if r.id == key), None)

    def remove(self, key: int) -> bool:
        """Delete by id. Returns True if found and removed."""
        di  = _bits(key, self.global_depth)
        off = self.directory[di]
        ld, records = self._read_bucket(off)
        new_recs = [r for r in records if r.id != key]
        if len(new_recs) == len(records):
            return False
        self._write_bucket(off, ld, new_recs)
        return True

    # rangeSearch NOT supported (project spec forbids it for this structure)

    def reset_counters(self):
        self.disk_reads = self.disk_writes = 0

    def close(self):
        self._f.close()

    # ── CSV bulk load ──────────────────────────────────────────────────────────
    @classmethod
    def from_csv(cls,
                 data_path: str,
                 index_path: str,
                 csv_path: str,
                 delimiter: str = ",") -> "ExtendibleHashFile":
        """
        Build index from CSV. Expected columns: id, title, author, pages, rating, year
        Change the row["..."] keys below if your CSV uses different column names.
        """
        eh = cls(data_path, index_path)
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=delimiter):
                try:
                    eh.insert(Record(
                        id    =int(row["id"]),
                        title =row["title"],
                        author=row["author"],
                        pages =int(row["pages"]),
                        rating=float(row["rating"]),
                        year  =int(row["year"]),
                    ))
                except (ValueError, KeyError):
                    continue
        return eh


# ── demo ───────────────────────────────────────────────────────────────────────
def main():
    DATA  = "books_eh.bin"
    INDEX = "books_eh.json"
    CSV   = "books.csv"      # ← tu CSV real aquí

    for p in (DATA, INDEX):
        if os.path.exists(p): os.remove(p)

    print("Cargando CSV …")
    eh = ExtendibleHashFile.from_csv(DATA, INDEX, CSV)
    print(f"  global_depth   = {eh.global_depth}")
    print(f"  directory size = {len(eh.directory)}")
    print(f"  disk reads  (carga) = {eh.disk_reads}")
    print(f"  disk writes (carga) = {eh.disk_writes}")

    eh.reset_counters()
    r = eh.search(1)
    print(f"\nsearch(1): {r}")
    print(f"  accesos a disco: {eh.disk_reads + eh.disk_writes}")

    eh.reset_counters()
    eh.insert(Record(999999, "Test Book", "Test Author", 300, 4.5, 2024))
    print(f"\ninsert(999999): accesos = {eh.disk_reads + eh.disk_writes}")

    eh.reset_counters()
    print(f"\nremove(999999): {eh.remove(999999)}")
    print(f"  accesos: {eh.disk_reads + eh.disk_writes}")
    print(f"  tras borrar: {eh.search(999999)}")

    eh.close()


if __name__ == "__main__":
    main()