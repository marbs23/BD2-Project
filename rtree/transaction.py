"""
Transacción ligada a WAL físico y LockManager.

Abort **no** usa write-set en RAM (D3): ``abort()`` recorre la WAL en orden inverso,
filtra ``WRITE`` del ``tid`` y reaplica ``before_image`` con ``PageStore.write_page``
sin pasar por el hook transaccional del futuro (bloque 4).

**TODO durabilidad**: si ``log_commit`` lanza (disco lleno, I/O), la transacción puede
quedar sin estado terminal explícito en log — el caller debe invocar ``abort()`` para
no dejar locks tomados.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from .lock_manager import DeadlockError, LockManager
from .wal import OP_WRITE, WriteAheadLog, iter_records

if TYPE_CHECKING:
    from .page_store import PageStore


class Transaction:
    def __init__(
        self,
        tid: int,
        store: "PageStore",
        lock_mgr: LockManager,
        wal: WriteAheadLog,
    ) -> None:
        self.tid = tid
        self.store = store
        self.lock_mgr = lock_mgr
        self.wal = wal
        self._aborted_lock = threading.RLock()
        self._aborted_flag = False
        self._begun = False
        self._finished = False

    @property
    def aborted(self) -> bool:
        with self._aborted_lock:
            return self._aborted_flag

    @aborted.setter
    def aborted(self, value: bool) -> None:
        with self._aborted_lock:
            self._aborted_flag = bool(value)

    def begin(self) -> None:
        if self._begun:
            raise RuntimeError("begin() ya fue llamado para esta transacción")
        self.wal.log_begin(self.tid)
        self.lock_mgr.register_tx(self)
        self._begun = True

    def commit(self) -> None:
        if self._finished:
            raise RuntimeError("commit sobre transacción ya finalizada")
        if not self._begun:
            raise RuntimeError("commit sin begin()")
        if self.aborted:
            raise DeadlockError(self.tid)
        self.wal.log_commit(self.tid)
        self.lock_mgr.release_all(self.tid)
        self._finished = True

    def abort(self) -> None:
        if self._finished:
            return
        if not self._begun:
            raise RuntimeError("abort sin begin()")
        self.wal.flush()
        for rec in reversed(list(iter_records(self.wal.path))):
            _lsn, rtid, pid, op, before, _after = rec
            if rtid != self.tid or op != OP_WRITE:
                continue
            self.store.write_page(pid, bytes(before))
        self.wal.log_abort(self.tid)
        self.lock_mgr.release_all(self.tid)
        self._finished = True

    def __enter__(self) -> "Transaction":
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._begun or self._finished:
            return None
        if exc_type is not None:
            self.abort()
        else:
            self.commit()
        return None
