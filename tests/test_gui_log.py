"""Tests for copyable GUI logs, without requiring a desktop session."""
import unittest
from types import SimpleNamespace
from app import Studio


class FakeLog:
    def __init__(self, text="", selected=None):
        self.text = text
        self.selected = selected
        self.seen = False
        self.states = []

    def yview(self):
        return (0.0, 1.0)

    def configure(self, **kwargs):
        self.states.append(kwargs.get("state"))

    def insert(self, where, content):
        self.text += content

    def see(self, where):
        self.seen = True

    def get(self, first, last):
        if first == "sel.first":
            if self.selected is None:
                from tkinter import TclError
                raise TclError("no selection")
            return self.selected
        return self.text


class LogUITests(unittest.TestCase):
    def test_message_is_mirrored_to_both_journals(self):
        main, viewer = FakeLog(), FakeLog()
        stub = SimpleNamespace(log_area=main, log_viewer_text=viewer,
                               _put_log_text=lambda target, content:
                               Studio._put_log_text(None, target, content))
        Studio._append_log(stub, "CUDA: нет библиотеки")
        self.assertEqual(main.text, "• CUDA: нет библиотеки\n")
        self.assertEqual(viewer.text, main.text)
        self.assertTrue(main.seen and viewer.seen)
        self.assertEqual(main.states, ["normal", "disabled"])

    def test_copy_all(self):
        copied = []
        notifications = []
        stub = SimpleNamespace(
            clipboard_clear=lambda: copied.clear(),
            clipboard_append=lambda value: copied.append(value),
            update_idletasks=lambda: None,
            log_copy_status=SimpleNamespace(set=lambda value: notifications.append(value)),
            after=lambda delay, action: None,
        )
        Studio._copy_log_from_widget(stub, FakeLog("Line 1\nLine 2"), selected=False)
        self.assertEqual(copied, ["Line 1\nLine 2"])
        self.assertEqual(notifications, ["Скопировано"])

    def test_copy_selection_and_fallback(self):
        copied = []
        stub = SimpleNamespace(
            clipboard_clear=lambda: copied.clear(),
            clipboard_append=lambda value: copied.append(value),
            update_idletasks=lambda: None,
            log_copy_status=SimpleNamespace(set=lambda value: None),
            after=lambda delay, action: None,
        )
        Studio._copy_log_from_widget(stub, FakeLog("Everything", "Just selected"), selected=True)
        self.assertEqual(copied, ["Just selected"])
        Studio._copy_log_from_widget(stub, FakeLog("Everything"), selected=True)
        self.assertEqual(copied, ["Everything"])


if __name__ == "__main__":
    unittest.main()
