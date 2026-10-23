import time
import unittest
from parallel_engine import (
    ThroughputETA, run_parallel, target_parallelism, worker_settings,
)


class ParallelTests(unittest.TestCase):
    def test_worker_settings_leave_cpu_headroom(self):
        count, threads = worker_settings('cpu', 'balanced', 0, 20)
        self.assertEqual(count, 2)
        self.assertGreaterEqual(threads, 1)
        count, threads = worker_settings('cpu', 'quiet', 0, 25)
        self.assertEqual(count, 1)
        self.assertGreaterEqual(threads, 1)
        self.assertEqual(worker_settings('cuda', 'balanced', 0, 20), (2, 2))
        self.assertEqual(worker_settings('cpu', 'balanced', 3, 20)[0], 3)
        with self.assertRaises(ValueError):
            worker_settings('cpu', 'balanced', 0, 5)

    def test_adaptive_submission_slows_when_cpu_is_busy(self):
        self.assertEqual(target_parallelism(2, 'balanced', 20, 95, 'cpu'), 1)
        self.assertEqual(target_parallelism(2, 'balanced', 20, 50, 'cpu'), 2)
        self.assertEqual(target_parallelism(2, 'fast', 20, 99, 'cpu'), 2)

    def test_eta_estimates_wall_clock_from_actual_completed_chunks(self):
        eta = ThroughputETA(0, 180, now=0)
        self.assertAlmostEqual(eta.record(60, 60, now=20), 40)
        self.assertAlmostEqual(eta.record(60, 120, now=30), 15)
        self.assertEqual(eta.record(60, 180, now=45), 0)

    def test_eta_does_not_double_count_previous_checkpoint(self):
        eta = ThroughputETA(120, 180, now=0)
        self.assertAlmostEqual(eta.record(30, 150, now=10), 10)

    def test_out_of_order_completion_is_committed_by_finish_not_start(self):
        completion = []
        def run(start, end):
            if start == 0:
                time.sleep(0.12)
            return start
        run_parallel(
            [(0, 60), (60, 120), (120, 180)], 2, run,
            lambda a, b, result: completion.append(result),
            performance='fast', sample_load=lambda: 0.
        )
        self.assertCountEqual(completion, [0, 60, 120])
        self.assertLess(completion.index(60), completion.index(0))

    def test_failed_block_is_not_committed(self):
        committed = []
        def run(start, end):
            if start == 60:
                raise RuntimeError("Simulation: audio decode failed")
            return start
        with self.assertRaisesRegex(RuntimeError, "Simulation"):
            run_parallel([(0, 60), (60, 120)], 1, run,
                         lambda a, b, result: committed.append(result),
                         performance='fast')
        self.assertEqual(committed, [0])


if __name__ == '__main__':
    unittest.main()
