#!/usr/bin/env python3
"""
Simulador de acceso concurrente — BD2 UTEC 26-1.

Lanza 2 threads (T1 y T2) que realizan inserciones y búsquedas
concurrentes sobre el mismo R-tree. El log timestamped muestra:
  - adquisición de locks (S/X)
  - esperas (WAIT) cuando hay conflicto
  - COMMIT / ABORT por deadlock

Uso (desde la raíz del proyecto):
    python examples/concurrent_demo.py
    cat concurrent_demo.log
"""
from __future__ import annotations

import datetime
import os
import random
import tempfile
import threading
import time

from rtree.geometry import TID
from rtree.lock_manager import DeadlockError, LockManager
from rtree.page_store import IOStats, PageStore
from rtree.rtree import RTree
from rtree.wal import WriteAheadLog

# ── logger thread-safe ──────────────────────────────────────────────────────
LOG_PATH = "concurrent_demo.log"
_log_lock = threading.Lock()


def _log(label: str, msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}][{label}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# ── event_cb para el LockManager ────────────────────────────────────────────
def _make_event_cb() -> callable:
    """
    Devuelve un callback que usa threading.current_thread().name como etiqueta.
    Se llama desde dentro de LockManager.acquire() en el hilo que adquiere el lock.
    """
    def cb(event: str, tid: int, pid: int, mode: str) -> None:
        label = threading.current_thread().name
        if event == "LOCK":
            _log(label, f"{mode}-lock pid={pid} (tid={tid})")
        elif event == "WAIT":
            _log(label, f"WAIT pid={pid} (requesting {mode}, tid={tid})")
    return cb


# ── worker ──────────────────────────────────────────────────────────────────
def _run_worker(
    tree: RTree,
    label: str,
    lon_lo: float,
    lon_hi: float,
    page_base: int,
) -> dict:
    """
    Ejecuta 5 lotes de 10 inserciones + 10 búsquedas en rangos espaciales separados.
    Pausa 0.02 s entre inserts para mantener X-locks activos e inducir contención.
    """
    _log(label, "BEGIN worker")
    committed = aborted = searches = 0

    for batch in range(5):
        try:
            with tree.begin_transaction() as tx:
                for k in range(10):
                    lon = random.uniform(lon_lo, lon_hi)
                    lat = random.uniform(-0.8, 0.8)
                    tree.insert(lon, lat, TID(page_base, batch * 10 + k), tx=tx)
                    time.sleep(0.02)  # mantiene X-lock activo → contención con searcher del otro thread
                _log(label, f"COMMIT batch={batch + 1}/5")
            committed += 1
        except DeadlockError:
            aborted += 1
            _log(label, f"ABORTED batch={batch + 1}/5 (deadlock)")

    for j in range(10):
        try:
            with tree.begin_transaction() as tx:
                lon = random.uniform(lon_lo, lon_hi)
                lat = random.uniform(-0.8, 0.8)
                sr = tree.range_search(lon, lat, 0.2, tx=tx)
                searches += 1
        except DeadlockError:
            _log(label, f"search {j + 1} aborted (deadlock)")

    _log(label, f"DONE committed={committed} aborted={aborted} searches={searches}")
    return {"committed": committed, "aborted": aborted, "searches": searches}


# ── main ────────────────────────────────────────────────────────────────────
def main() -> None:
    random.seed(42)

    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)

    td = tempfile.TemporaryDirectory()
    db_path = os.path.join(td.name, "demo.db")
    wal_path = os.path.join(td.name, "demo.wal")

    lm = LockManager(event_cb=_make_event_cb())
    wal = WriteAheadLog(wal_path)
    store = PageStore(db_path, IOStats(), wal=wal)
    tree = RTree(store, lock_mgr=lm)

    _log("main", "Warm-up: insertando 200 puntos...")
    for i in range(200):
        tree.insert(random.uniform(-1.0, 1.0), random.uniform(-1.0, 1.0), TID(999, i))
    _log("main", "Warm-up completo.")

    results: dict[str, dict] = {}

    def t1_fn() -> None:
        results["T1"] = _run_worker(tree, "T1", -1.0, -0.05, 600)

    def t2_fn() -> None:
        results["T2"] = _run_worker(tree, "T2", 0.05, 1.0, 700)

    t1 = threading.Thread(target=t1_fn, name="T1", daemon=True)
    t2 = threading.Thread(target=t2_fn, name="T2", daemon=True)

    _log("main", "Lanzando T1 y T2...")
    t0 = time.monotonic()
    t1.start()
    t2.start()
    t1.join(timeout=30.0)
    t2.join(timeout=30.0)
    elapsed = time.monotonic() - t0

    if t1.is_alive() or t2.is_alive():
        _log("main", "ERROR: algún hilo no terminó en 30 s — posible deadlock no resuelto")
    else:
        _log("main", (
            f"Completado en {elapsed:.2f} s | "
            f"T1={results.get('T1')} | T2={results.get('T2')}"
        ))
        _log("main", f"Log guardado en {os.path.abspath(LOG_PATH)}")

    wal.close()
    td.cleanup()


if __name__ == "__main__":
    main()
