"""User-facing format selection, settings migration and locked batch controls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QApplication
import main
from app_logging import close_logging


class KiCadUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for context in (patch.dict(os.environ, LOCALAPPDATA=str(self.root / 'profile')),
                        patch.object(main, 'SETTINGS_PATH', self.root / 'profile/LCSC3D/settings.json'),
                        patch.object(main, 'LEGACY_SETTINGS_PATH', self.root / 'missing.json')):
            context.start()
            self.addCleanup(context.stop)
        self.addCleanup(close_logging)
        self.windows = []
        self.addCleanup(self.close_windows)
        self.window = self.create_window()

    def create_window(self):
        # Exercise persistence without scheduling the unrelated account-store
        # startup callback on a window that this test may immediately dispose.
        window = main.MainWindow(settings_enabled=False)
        window.settings_enabled = True
        window._restore_settings()
        self.windows.append(window)
        return window

    def close_windows(self):
        for window in reversed(self.windows):
            window.settings_enabled = False
            window.close()
            window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_old_settings_keep_step_and_new_formats_are_opt_in(self):
        self.assertEqual(self.window.selected_formats(), ('STEP',))
        self.assertFalse(self.window.kicad_symbol_box.isChecked())
        self.assertFalse(self.window.kicad_footprint_box.isChecked())

    def test_kicad_only_selection_and_library_name_survive_restart(self):
        w = self.window
        w.step_box.setChecked(False)
        w.kicad_symbol_box.setChecked(True)
        w.kicad_footprint_box.setChecked(True)
        w.kicad_merge_box.setChecked(True)
        w.kicad_name_input.setText('MyParts')
        self.assertTrue(w.save_settings())
        restored = self.create_window()
        options = restored.export_options()
        self.assertEqual(options.formats, ('KICAD_SYMBOL', 'KICAD_FOOTPRINT'))
        self.assertEqual(options.kicad_library_name, 'MyParts')
        self.assertTrue(options.kicad_merge)

    def test_append_mode_temporarily_disables_kicad_without_losing_selection(self):
        w = self.window
        w.kicad_symbol_box.setChecked(True)
        w.lib_append_box.setChecked(True)
        self.assertTrue(w.kicad_symbol_box.isChecked())
        self.assertFalse(w.kicad_symbol_box.isEnabled())
        self.assertNotIn('KICAD_SYMBOL', w.selected_formats())
        w.lib_append_box.setChecked(False)
        self.assertTrue(w.kicad_symbol_box.isEnabled())
        self.assertIn('KICAD_SYMBOL', w.selected_formats())

    def test_batch_locks_all_kicad_controls(self):
        w = self.window
        w.kicad_symbol_box.setChecked(True)
        controls = (w.kicad_symbol_box, w.kicad_footprint_box, w.kicad_merge_box, w.kicad_name_input)
        w.set_running(True)
        self.assertTrue(all(not c.isEnabled() for c in controls))
        w.set_running(False)
        self.assertTrue(all(c.isEnabled() for c in controls))
