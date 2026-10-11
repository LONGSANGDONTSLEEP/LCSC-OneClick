"""User-facing format selection, settings migration and locked batch controls."""
import os
import json
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
        w.export_target_box.setCurrentIndex(w.export_target_box.findData('kicad'))
        w.step_box.setChecked(False)
        w.kicad_symbol_box.setChecked(True)
        w.kicad_footprint_box.setChecked(True)
        w.kicad_merge_symbol_box.setChecked(False)
        w.kicad_merge_footprint_box.setChecked(True)
        w.kicad_electrical_box.setCurrentIndex(w.kicad_electrical_box.findData('bidirectional'))
        w.kicad_name_input.setText('MyParts')
        self.assertTrue(w.save_settings())
        restored = self.create_window()
        options = restored.export_options()
        self.assertEqual(options.formats, ('KICAD_SYMBOL', 'KICAD_FOOTPRINT'))
        self.assertEqual(options.kicad_library_name, 'MyParts')
        self.assertFalse(options.kicad_merge_symbols)
        self.assertTrue(options.kicad_merge_footprints)
        self.assertEqual(options.kicad_electrical_policy, 'bidirectional')
        self.assertEqual(restored.export_target_box.currentData(), 'kicad')

    def test_target_switch_isolates_ad_append_and_retains_both_targets_settings(self):
        w = self.window
        w.step_box.setChecked(False)
        w.schlib_box.setChecked(True)
        w.kicad_symbol_box.setChecked(True)
        w.lib_append_box.setChecked(True)
        self.assertTrue(w.kicad_symbol_box.isChecked())
        self.assertFalse(w.kicad_symbol_box.isEnabled())
        self.assertNotIn('KICAD_SYMBOL', w.selected_formats())
        w.export_target_box.setCurrentIndex(w.export_target_box.findData('kicad'))
        self.assertTrue(w.kicad_symbol_box.isEnabled())
        self.assertEqual(w.selected_formats(), ('KICAD_SYMBOL',))
        self.assertFalse(w.export_options().append_mode)
        self.assertTrue(w.ad_format_options.isHidden())
        self.assertTrue(w.append_options.isHidden())
        self.assertFalse(w.kicad_options.isHidden())
        self.assertTrue(w.path_input.isEnabled())
        w.export_target_box.setCurrentIndex(w.export_target_box.findData('ad'))
        self.assertTrue(w.lib_append_box.isChecked())
        self.assertEqual(w.selected_formats(), ('SCHLIB',))
        self.assertTrue(w.kicad_options.isHidden())
        self.assertFalse(w.append_options.isHidden())

    def test_legacy_combined_switch_migrates_to_both_merge_options(self):
        main.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        main.SETTINGS_PATH.write_text(json.dumps({'step': False, 'kicad_symbol': True,
            'kicad_footprint': True, 'kicad_merge': True}), 'utf-8')
        self.window._restore_settings()
        self.assertEqual(self.window.export_target_box.currentData(), 'kicad')
        options = self.window.export_options()
        self.assertTrue(options.kicad_merge_symbols)
        self.assertTrue(options.kicad_merge_footprints)
        self.assertEqual(options.kicad_electrical_policy, 'source')

    def test_batch_locks_all_kicad_controls(self):
        w = self.window
        w.export_target_box.setCurrentIndex(w.export_target_box.findData('kicad'))
        w.kicad_symbol_box.setChecked(True)
        w.kicad_footprint_box.setChecked(True)
        controls = (w.export_target_box, w.kicad_symbol_box, w.kicad_footprint_box, w.kicad_merge_symbol_box,
                    w.kicad_merge_footprint_box, w.kicad_electrical_box, w.kicad_name_input)
        w.set_running(True)
        self.assertTrue(all(not c.isEnabled() for c in controls))
        w.set_running(False)
        self.assertTrue(all(c.isEnabled() for c in controls))
