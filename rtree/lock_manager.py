"""
Page-level lock manager (shared / exclusive) with deadlock detection.

**Nota arquitectónica**: Este componente implementa bloqueos de duración según 2PL
para páginas identificadas por `pid`. El descenso con lock coupling (crabbing) en
`RTree` soltará locks S del padre antes del commit; eso no es Strict 2PL literal en
todo el camino — se documenta en `rtree.py` al integrar. Aquí solo gestionamos
adquisición, upgrade S→X y liberación.

Acquire usa espera acotada (polling con ``Condition.wait(timeout=0.05)``) y un
flag ``tx.aborted`` para que otra transacción pueda marcar víctima de deadlock
sin dejar al solicitante colgado en ``wait()`` infinito (D1).
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Literal

LockMode = Literal["S", "X"]


class DeadlockError(Exception):
    """La transacción fue elegida como víctima de deadlock o abortada durante acquire."""

    def __init__(self, tid: int):
        super().__init__(f"deadlock victim or aborted: tid={tid}")
        self.tid = tid


class LockManager:
    """
    Locks a nivel de página: modo S (compartido) o X (exclusivo).

    Contrato del objeto transacción ``tx``:
    - ``tx.tid: int`` identificador estable
    - ``tx.aborted: bool`` lectura/escritura thread-safe (p. ej. bajo un ``threading.Lock``)
    """

    def __init__(self, event_cb=None) -> None:
        self._mu = threading.RLock()
        self._cv = threading.Condition(self._mu)
        self._locks: dict[int, dict] = {}
        self._tx_pages: dict[int, set[int]] = defaultdict(set)
        self._tx_refs: dict[int, object] = {}
        self._waits_for: dict[int, set[int]] = {}
        self._event_cb = event_cb

    def register_tx(self, tx: object) -> None:
        self._tx_refs[int(getattr(tx, "tid"))] = tx

    def acquire(self, tx: object, pid: int, mode: LockMode) -> None:
        tid = int(getattr(tx, "tid"))
        self._tx_refs[tid] = tx
        _notified_wait = False
        while True:
            with self._mu:
                if bool(getattr(tx, "aborted")):
                    raise DeadlockError(tid)
                if self._can_grant(tx, pid, mode):
                    self._grant(tx, pid, mode)
                    self._notify("LOCK", tid, pid, mode)
                    return
                if not _notified_wait:
                    self._notify("WAIT", tid, pid, mode)
                    _notified_wait = True
                self._add_waiter(tx, pid, mode)
                self._rebuild_waits_for()
                cycle = self._find_cycle_on_path(tid)
                if cycle:
                    victim_tid = min(cycle)
                    victim_tx = self._tx_refs.get(victim_tid)
                    if victim_tx is not None:
                        setattr(victim_tx, "aborted", True)
                    # Liberar locks que ya tenía la víctima para desbloquear al otro hilo.
                    self._release_all_locked(victim_tid)
                    self._cv.notify_all()
                    if victim_tid == tid:
                        self._remove_waiter(tid, pid)
                        raise DeadlockError(tid)
                self._cv.wait(timeout=0.05)

    def release(self, tx: object, pid: int) -> None:
        tid = int(getattr(tx, "tid"))
        with self._mu:
            st = self._locks.get(pid)
            if not st or tid not in st["holders"]:
                return
            self._release_holder(pid, st, tid)
            self._cv.notify_all()

    def release_all(self, tid: int) -> None:
        with self._mu:
            self._release_all_locked(tid)
            self._cv.notify_all()

    def _notify(self, event: str, tid: int, pid: int, mode: str) -> None:
        """Llama al event_cb si está configurado. Errores en el callback se silencian."""
        if self._event_cb is not None:
            try:
                self._event_cb(event, tid, pid, mode)
            except Exception:
                pass

    def _release_all_locked(self, tid: int) -> None:
        for pid in list(self._tx_pages.get(tid, ())):
            st = self._locks.get(pid)
            if st and tid in st["holders"]:
                self._release_holder(pid, st, tid)

    @staticmethod
    def _tid(tx: object) -> int:
        return int(getattr(tx, "tid"))

    def _ensure_lock_entry(self, pid: int) -> dict:
        if pid not in self._locks:
            self._locks[pid] = {"mode": "S", "holders": set(), "waiters": deque()}
        return self._locks[pid]

    def _can_grant(self, tx: object, pid: int, mode: LockMode) -> bool:
        tid = self._tid(tx)
        st = self._locks.get(pid)
        if not st or not st["holders"]:
            return True
        # Reentrancia 2PL: la misma transacción ya tiene lock ≥ pedido en esta página.
        if tid in st["holders"]:
            if mode == "S":
                return True
            return st["holders"] <= {tid}
        if mode == "S":
            return st["mode"] == "S"
        # X
        if st["mode"] == "X":
            return st["holders"] == {tid}
        return st["holders"] <= {tid}

    def _grant(self, tx: object, pid: int, mode: LockMode) -> None:
        tid = self._tid(tx)
        st = self._ensure_lock_entry(pid)
        self._remove_waiter(tid, pid)
        if tid in st["holders"]:
            if mode == "S":
                return
            if mode == "X" and st["mode"] == "X" and st["holders"] == {tid}:
                return
            if mode == "X" and st["mode"] == "S" and st["holders"] <= {tid}:
                st["mode"] = "X"
                st["holders"] = {tid}
                return
        if mode == "S":
            if not st["holders"]:
                st["mode"] = "S"
            st["holders"].add(tid)
        else:
            if not st["holders"]:
                st["mode"] = "X"
                st["holders"] = {tid}
            elif st["mode"] == "S" and st["holders"] <= {tid}:
                st["mode"] = "X"
                st["holders"] = {tid}
            else:
                st["holders"] = {tid}
                st["mode"] = "X"
        self._tx_pages[tid].add(pid)

    def _release_holder(self, pid: int, st: dict, tid: int) -> None:
        st["holders"].discard(tid)
        self._tx_pages[tid].discard(pid)
        if not st["holders"]:
            if st["waiters"]:
                pass  # conservar entrada hasta que _grant vacíe waiters
            else:
                del self._locks[pid]

    def _add_waiter(self, tx: object, pid: int, mode: LockMode) -> None:
        tid = self._tid(tx)
        st = self._ensure_lock_entry(pid)
        for wt, wm in st["waiters"]:
            if wt == tid and wm == mode:
                return
        st["waiters"].append((tid, mode))

    def _remove_waiter(self, tid: int, pid: int) -> None:
        st = self._locks.get(pid)
        if not st:
            return
        st["waiters"] = deque((a, b) for (a, b) in st["waiters"] if a != tid)

    def _conflicting_holders(self, pid: int, request_tid: int, request_mode: LockMode) -> set[int]:
        st = self._locks.get(pid)
        if not st or not st["holders"]:
            return set()
        if request_mode == "S":
            if st["mode"] == "X":
                return set(st["holders"])
            return set()
        return {h for h in st["holders"] if h != request_tid}

    def _rebuild_waits_for(self) -> None:
        self._waits_for = {}
        for _pid, st in self._locks.items():
            for wtid, wmode in st["waiters"]:
                blockers = self._conflicting_holders(_pid, wtid, wmode)
                self._waits_for.setdefault(wtid, set()).update(blockers)

    def _find_cycle_on_path(self, start_tid: int) -> list[int] | None:
        """
        Encuentra un ciclo dirigido alcanzable desde ``start_tid`` (arista u→v si u espera a v).
        Devuelve los vértices del ciclo o None.
        """
        waits = self._waits_for
        path: list[int] = []

        def dfs(u: int) -> list[int] | None:
            path.append(u)
            for v in waits.get(u, ()):
                if v in path:
                    return path[path.index(v):]
                r = dfs(v)
                if r:
                    return r
            path.pop()
            return None

        return dfs(start_tid)
