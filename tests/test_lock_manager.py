import threading
import unittest

from rtree.lock_manager import DeadlockError, LockManager


class Tx:
    """Transacción mínima compatible con LockManager (tid + aborted thread-safe)."""

    def __init__(self, tid: int) -> None:
        self.tid = tid
        self._mu = threading.Lock()
        self._aborted = False

    @property
    def aborted(self) -> bool:
        with self._mu:
            return self._aborted

    @aborted.setter
    def aborted(self, v: bool) -> None:
        with self._mu:
            self._aborted = v


class TestLockManagerBasics(unittest.TestCase):
    def test_x_exclusive_second_blocks(self) -> None:
        lm = LockManager()
        a, b = Tx(1), Tx(2)
        lm.acquire(a, 100, "X")
        done = threading.Event()

        def try_b() -> None:
            lm.acquire(b, 100, "X")
            done.set()

        th = threading.Thread(target=try_b, daemon=True)
        th.start()
        self.assertFalse(done.wait(timeout=0.15))
        lm.release(a, 100)
        self.assertTrue(done.wait(timeout=2.0))
        th.join(timeout=1.0)

    def test_s_shared_two_holders(self) -> None:
        lm = LockManager()
        a, b = Tx(1), Tx(2)
        lm.acquire(a, 5, "S")
        lm.acquire(b, 5, "S")
        lm.release(a, 5)
        lm.release(b, 5)

    def test_x_blocks_s(self) -> None:
        lm = LockManager()
        a, b = Tx(1), Tx(2)
        lm.acquire(a, 7, "X")
        got = threading.Event()

        def try_s() -> None:
            lm.acquire(b, 7, "S")
            got.set()

        th = threading.Thread(target=try_s, daemon=True)
        th.start()
        self.assertFalse(got.wait(timeout=0.1))
        lm.release(a, 7)
        self.assertTrue(got.wait(timeout=2.0))
        th.join(timeout=1.0)

    def test_upgrade_s_to_x(self) -> None:
        lm = LockManager()
        t = Tx(1)
        lm.acquire(t, 3, "S")
        lm.acquire(t, 3, "X")
        lm.release(t, 3)

    def test_release_all(self) -> None:
        lm = LockManager()
        t = Tx(9)
        lm.acquire(t, 1, "S")
        lm.acquire(t, 2, "X")
        lm.release_all(9)
        u = Tx(8)
        lm.acquire(u, 1, "X")
        lm.acquire(u, 2, "X")
        lm.release(u, 1)
        lm.release(u, 2)


class TestLockManagerDeadlock(unittest.TestCase):
    def test_deadlock_victim_is_smallest_tid(self) -> None:
        lm = LockManager()
        t1, t2 = Tx(10), Tx(20)
        lm.acquire(t1, 1, "X")
        lm.acquire(t2, 2, "X")

        barrier = threading.Barrier(2)
        vict_tid: list[int] = []

        def first() -> None:
            barrier.wait()
            try:
                lm.acquire(t1, 2, "X")
            except DeadlockError as e:
                vict_tid.append(e.tid)

        def second() -> None:
            barrier.wait()
            try:
                lm.acquire(t2, 1, "X")
            except DeadlockError as e:
                vict_tid.append(e.tid)

        th1 = threading.Thread(target=first, daemon=True)
        th2 = threading.Thread(target=second, daemon=True)
        th1.start()
        th2.start()
        th1.join(timeout=5.0)
        th2.join(timeout=5.0)
        self.assertEqual(vict_tid, [10])

    def test_acquire_returns_after_abort_flag(self) -> None:
        lm = LockManager()
        t = Tx(1)
        t.aborted = True
        with self.assertRaises(DeadlockError):
            lm.acquire(t, 99, "S")


class TestLockManagerNoHang(unittest.TestCase):
    def test_wait_is_bounded_under_contention(self) -> None:
        lm = LockManager()
        a, b = Tx(1), Tx(2)
        lm.acquire(a, 1, "X")

        def blocker() -> None:
            lm.acquire(b, 1, "S")

        th = threading.Thread(target=blocker, daemon=True)
        th.start()
        th.join(timeout=3.0)
        self.assertTrue(th.is_alive())
        lm.release(a, 1)
        th.join(timeout=2.0)
        self.assertFalse(th.is_alive())


if __name__ == "__main__":
    unittest.main()
