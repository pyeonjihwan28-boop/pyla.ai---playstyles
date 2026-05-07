"""Bot thread <-> UI thread contract tests.

These don't spin up Tk; they verify that BotController.update_queue
delivers events in order from a producer (the bot thread) to a
consumer (the UI poll loop) and that signal_completion replaces the
historical sys.exit() path with a queueable status update.
"""
import os
import queue
import sys
import threading
import time
import unittest

# Allow running from repo root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot_controller import BotController
from bot_state import (
    STATUS_COMPLETED,
    STATUS_PAUSED,
    STATUS_RUNNING,
)


def _drain(q, max_seconds=0.3):
    """Drain a queue.Queue until empty or deadline expires."""
    out = []
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            time.sleep(0.005)
    return out


class TestBotControllerContract(unittest.TestCase):

    def test_update_queue_delivers_status_events(self):
        bc = BotController()
        bc.update(status=STATUS_RUNNING, current_brawler="shelly")
        bc.update(status=STATUS_PAUSED)
        events = _drain(bc.update_queue)
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["status"], STATUS_RUNNING)
        self.assertEqual(events[0]["current_brawler"], "shelly")
        self.assertEqual(events[1]["status"], STATUS_PAUSED)

    def test_signal_completion_emits_completed_status(self):
        bc = BotController()
        bc.signal_completion("targets_completed")
        events = _drain(bc.update_queue)
        self.assertTrue(events, "expected at least one queued event")
        self.assertEqual(events[-1]["status"], STATUS_COMPLETED)
        self.assertEqual(events[-1]["message"], "targets_completed")

    def test_signal_completion_idempotent(self):
        bc = BotController()
        bc.signal_completion("first")
        bc.signal_completion("second")  # should be ignored
        events = _drain(bc.update_queue)
        completion_events = [e for e in events if e["status"] == STATUS_COMPLETED]
        self.assertEqual(len(completion_events), 1)
        self.assertEqual(completion_events[0]["message"], "first")

    def test_pause_and_resume_flip_event(self):
        bc = BotController()
        bc.pause()
        self.assertTrue(bc.pause_event.is_set())
        bc.resume()
        self.assertFalse(bc.pause_event.is_set())

    def test_stop_sets_event_and_clears_pause(self):
        bc = BotController()
        bc.pause()
        bc.stop()
        self.assertTrue(bc.stop_event.is_set())
        self.assertFalse(bc.pause_event.is_set(), "stop must wake a paused worker")

    def test_cross_thread_event_delivery_under_200ms(self):
        bc = BotController()
        sent_at = time.time()

        def producer():
            bc.update(status=STATUS_RUNNING, message="hello")

        threading.Thread(target=producer).start()
        ev = bc.update_queue.get(timeout=0.5)
        latency_ms = (time.time() - sent_at) * 1000
        self.assertLess(latency_ms, 200, f"latency {latency_ms:.1f}ms exceeds 200ms")
        self.assertEqual(ev["status"], STATUS_RUNNING)


if __name__ == "__main__":
    unittest.main()
