"""
Append-only Write-Ahead Log (physical logging, páginas completas de 4096 bytes).

**Trade-off**: cada WRITE ocupa ~8 KiB en disco (before + after) + cabecera;
``os.fsync`` en COMMIT/ABORT. Logical logging reduciría tamaño pero exige operaciones
inversas del R-tree; fuera de scope para este simulador.

Recovery implementa ARIES simplificado (D4): análisis → REDO (tx commiteadas) →
UNDO (tx incompletas). ``replay`` ignora bytes residuales menores que un record
(registro truncado por crash).

Los appends concurrentes al mismo archivo se serializan con ``_append_mu`` para
mantener LSN y bytes contiguos coherentes entre hilos.
"""

from __future__ import annotations

import os
import struct
import threading
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from rtree.page_store import PageStore


# --- Formato binario (little-endian, sin padding implícito en cabecera) ---
# <lsn:Q><tid:I><pid:I><op:B><before:4096s><after:4096s>
_HEADER_STRUCT = struct.Struct("<QII B")
HEADER_SIZE = _HEADER_STRUCT.size  # 17
PAGE_BYTES = 4096
RECORD_SIZE = HEADER_SIZE + PAGE_BYTES + PAGE_BYTES

OP_BEGIN = 0
OP_WRITE = 1
OP_COMMIT = 2
OP_ABORT = 3


class WriteAheadLog:
    """WAL append-only sobre archivo ``path``."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fp = open(path, "a+b")
        self._next_lsn = self._compute_next_lsn()
        self._append_mu = threading.Lock()

    def close(self) -> None:
        self._fp.close()

    def flush(self) -> None:
        """Vuelca buffers del proceso para que lecturas externas (p.ej. UNDO vía ``iter_records``) vean datos."""
        with self._append_mu:
            self._fp.flush()

    def _compute_next_lsn(self) -> int:
        try:
            sz = os.path.getsize(self.path)
        except OSError:
            return 1
        if sz == 0:
            return 1
        n_full = sz // RECORD_SIZE
        if n_full == 0:
            return 1
        last_chunk = self._read_record_bytes_at((n_full - 1) * RECORD_SIZE)
        if last_chunk is None:
            return 1
        lsn, _, _, _ = _unpack_header(last_chunk)
        return int(lsn) + 1

    def _read_record_bytes_at(self, offset: int) -> bytes | None:
        self._fp.seek(offset)
        raw = self._fp.read(RECORD_SIZE)
        if len(raw) != RECORD_SIZE:
            return None
        return raw

    def _append_locked_body(
        self,
        tid: int,
        pid: int,
        op: int,
        before: bytes,
        after: bytes,
    ) -> int:
        assert len(before) == PAGE_BYTES and len(after) == PAGE_BYTES
        lsn = self._next_lsn
        self._next_lsn += 1
        hdr = _HEADER_STRUCT.pack(lsn, tid, pid, op)
        self._fp.seek(0, os.SEEK_END)
        self._fp.write(hdr)
        self._fp.write(before)
        self._fp.write(after)
        return lsn

    def _append_record(
        self,
        tid: int,
        pid: int,
        op: int,
        before: bytes,
        after: bytes,
    ) -> int:
        with self._append_mu:
            return self._append_locked_body(tid, pid, op, before, after)

    def log_begin(self, tid: int) -> None:
        z = bytes(PAGE_BYTES)
        self._append_record(tid, 0, OP_BEGIN, z, z)

    def log_write(self, tid: int, pid: int, before: bytes, after: bytes) -> None:
        self._append_record(tid, pid, OP_WRITE, before, after)

    def log_commit(self, tid: int) -> None:
        z = bytes(PAGE_BYTES)
        with self._append_mu:
            self._append_locked_body(tid, 0, OP_COMMIT, z, z)
            self._fp.flush()
            os.fsync(self._fp.fileno())

    def log_abort(self, tid: int) -> None:
        z = bytes(PAGE_BYTES)
        with self._append_mu:
            self._append_locked_body(tid, 0, OP_ABORT, z, z)
            self._fp.flush()
            os.fsync(self._fp.fileno())

    @staticmethod
    def replay(store: "PageStore", wal_path: str) -> None:
        """
        Análisis → REDO → UNDO sobre ``store``.

        - ``committed_tids``: tienen registro COMMIT.
        - ``aborted_logged``: tienen registro ABORT (abort ya reconciliado en línea).
        - ``losers``: BEGIN sin COMMIT ni ABORT → UNDO de sus WRITE.
        """
        records = list(iter_records(wal_path))
        begun: set[int] = set()
        committed: set[int] = set()
        aborted_logged: set[int] = set()

        for rec in records:
            _lsn, tid, _pid, op, _b, _a = rec
            if op == OP_BEGIN:
                begun.add(tid)
            elif op == OP_COMMIT:
                committed.add(tid)
            elif op == OP_ABORT:
                aborted_logged.add(tid)

        losers = begun - committed - aborted_logged

        for rec in records:
            _lsn, tid, pid, op, _before, after = rec
            if op != OP_WRITE:
                continue
            if tid in committed:
                store.write_page(pid, after)

        for rec in reversed(records):
            _lsn, tid, pid, op, before, _after = rec
            if op != OP_WRITE:
                continue
            if tid in losers:
                store.write_page(pid, before)


def _unpack_header(chunk: bytes) -> tuple[int, int, int, int]:
    if len(chunk) < HEADER_SIZE:
        raise ValueError("truncated WAL header")
    return _HEADER_STRUCT.unpack_from(chunk, 0)


def unpack_record(raw: bytes) -> tuple[int, int, int, int, bytes, bytes]:
    if len(raw) != RECORD_SIZE:
        raise ValueError(f"bad WAL record size {len(raw)} expected {RECORD_SIZE}")
    lsn, tid, pid, op = _unpack_header(raw)
    off = HEADER_SIZE
    before = raw[off : off + PAGE_BYTES]
    off += PAGE_BYTES
    after = raw[off : off + PAGE_BYTES]
    return lsn, tid, pid, op, before, after


def iter_records(path: str) -> Iterable[tuple[int, int, int, int, bytes, bytes]]:
    """Itera registros válidos; ignora un sufijo incompleto (< RECORD_SIZE bytes)."""
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        data = f.read()
    offset = 0
    while offset + RECORD_SIZE <= len(data):
        chunk = data[offset : offset + RECORD_SIZE]
        try:
            yield unpack_record(chunk)
        except (struct.error, ValueError):
            # TODO: truncar archivo al último offset válido si se desea reparar WAL
            break
        offset += RECORD_SIZE
