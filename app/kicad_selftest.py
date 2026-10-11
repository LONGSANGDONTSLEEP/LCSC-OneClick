"""Isolated GUI integration check used by the local frozen-demo verifier."""
import hashlib
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def start(window, folder):
    import main
    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)
    data = {part: json.loads((root / 'fixtures' / (part + '.json')).read_text('utf-8'))
            for part in ('C20197', 'C2765186', 'C23922', 'C8734', 'C2040')}
    class OfflineApi(main.NetworkApi):
        def get_cad_data_of_component(self, part):
            self.check_cancelled()
            return data[part]
    main.NetworkApi = OfflineApi
    window.request_component_info = lambda: None
    window.show_preview = lambda *args, **kwargs: None
    window.resize(1380, 880)
    cases = [('individual', False, False, 'kicad', ['C20197', 'C2765186']),
             ('combined', True, True, 'kicad', ['C23922', 'C8734']),
             ('merge-symbols', True, False, 'kicad', ['C23922', 'C8734']),
             ('merge-footprints', False, True, 'kicad', ['C23922', 'C8734']),
             ('footprint-only', False, True, 'kicad', ['C23922', 'C8734']),
             ('ad', False, False, 'ad', ['C20197', 'C2765186']),
             ('collision', True, True, 'kicad', ['C23922', 'C8734']),
             ('preview-sample', True, True, 'kicad', ['C2040'])]
    state = {'index': -1, 'phase': 'next', 'deadline': time.monotonic() + 90, 'done': False}
    report = {'version': main.VERSION, 'frozen': bool(getattr(sys, 'frozen', False)), 'cases': []}
    timer = QTimer(window)
    timer.setInterval(70)
    def finish(error=''):
        state['done'] = True
        timer.stop()
        report.update(success=not error, error=error)
        (root / 'kicad-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        if window.worker and window.worker.isRunning():
            window.worker.cancelled.set()
        def close():
            if window.worker and window.worker.isRunning():
                QTimer.singleShot(100, close)
            else:
                window.close()
                QApplication.instance().exit(1 if error else 0)
        close()
    def hashes(path):
        return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.rglob('*') if p.is_file()}
    def tick():
        try:
            if time.monotonic() > state['deadline']:
                raise AssertionError('KiCad GUI check timed out')
            if state['phase'] == 'next':
                state['index'] += 1
                if state['index'] == len(cases):
                    from library_preview import build_library_preview
                    preview = build_library_preview(json.loads((root / 'fixtures/C2040_svgs.json').read_text('utf-8')), 'C2040')
                    window.library_cache['C2040'] = preview
                    window.current_preview = 'C2040'
                    window.set_preview_mode('symbol')
                    window.display_library_preview('C2040', preview)
                    state['phase'] = 'preview'
                    return
                name, symbols, footprints, target, parts = cases[state['index']]
                ad = target == 'ad'
                output = root / ('combined' if name == 'collision' else name)
                state.update(name=name, output=output, before=hashes(output), parts=parts)
                window.lib_append_box.setChecked(False)
                window.lib_merge_box.setChecked(False)
                window.step_box.setChecked(False)
                window.obj_box.setChecked(False)
                window.schlib_box.setChecked(ad)
                window.pcblib_box.setChecked(ad)
                window.export_target_box.setCurrentIndex(window.export_target_box.findData(target))
                window.kicad_symbol_box.setChecked(name != 'footprint-only')
                window.kicad_footprint_box.setChecked(True)
                window.kicad_merge_symbol_box.setChecked(symbols)
                window.kicad_merge_footprint_box.setChecked(footprints)
                window.kicad_electrical_box.setCurrentIndex(window.kicad_electrical_box.findData(
                    'input' if name == 'merge-symbols' else 'source'))
                window.kicad_name_input.setText('LCSC3D')
                window.path_input.setText(str(output))
                window.input.setPlainText('\n'.join(parts))
                assert window.load_queue()
                expected = ('SCHLIB', 'PCBLIB') if ad else ('KICAD_FOOTPRINT',) if name == 'footprint-only' else ('KICAD_SYMBOL', 'KICAD_FOOTPRINT')
                assert window.export_options().formats == expected
                window.start_batch()
                state['phase'] = 'wait'
            elif state['phase'] == 'preview':
                assert window.symbol_view.load_state[0] != 'error', window.symbol_view.load_state
                if window.symbol_view.load_state[0] == 'ready':
                    assert window.symbol_view.document.symbol_style == 'kicad'
                    assert not window.symbol_style_row.isVisible() and window.kicad_style_hint.isVisible()
                    state['phase'] = 'inspect-preview'
                    def inspected(fills):
                        try:
                            details = json.loads(fills)
                            report['preview_geometry'] = details['geometry']
                            fills = details['fills']
                        except (ValueError, TypeError):
                            finish('KiCad preview did not return inspectable SVG styles')
                            return
                        if not fills or any(row != ['rgb(255, 255, 194)', '1'] for row in fills):
                            finish('KiCad rectangle preview fill mismatch: ' + str(fills))
                            return
                        report['kicad_preview_verified'] = True
                        report['preview_rectangle_fills'] = fills
                        # Chromium's DOM-ready message can precede the native
                        # compositor's painted frame. Capture on a later tick.
                        state.update(phase='capture-preview', capture_after=time.monotonic() + 0.6)
                    window.symbol_view.page().runJavaScript(
                        "JSON.stringify({geometry:window.getSvgPreviewState(), fills:[...document.querySelectorAll('svg g rect')].map(n=>{const s=getComputedStyle(n);return [s.fill,s.fillOpacity];})})", inspected)
            elif state['phase'] == 'capture-preview' and time.monotonic() >= state['capture_after']:
                snapshot = window.symbol_view.grab().toImage()
                body_pixels = sum(1 for x in range(0, snapshot.width(), 4) for y in range(0, snapshot.height(), 4)
                    if (color := snapshot.pixelColor(x, y)).red() > 230 and color.green() > 230 and color.blue() < 225)
                if body_pixels > 50:
                    window.grab().save(str(root / 'kicad-preview.png'))
                    report['preview_painted_pixels_verified'] = True
                    finish()
                elif time.monotonic() > state['capture_after'] + 3:
                    snapshot.save(str(root / 'preview-missing-pixels.png'))
                    window.grab().save(str(root / 'window-missing-pixels.png'))
                    finish('KiCad preview has no painted symbol pixels')
            elif state['phase'] == 'wait' and not window.batch_running and not (window.worker and window.worker.isRunning()):
                rows = [window.results[p] for p in state['parts']]
                if state['name'] == 'collision':
                    assert all(r.status == '失败' and '已存在' in r.message for r in rows)
                    assert hashes(state['output']) == state['before']
                else:
                    assert all(r.status == '成功' for r in rows), [r.message for r in rows]
                    assert all(Path(p).is_file() for r in rows for p in r.files)
                    if state['name'] == 'combined':
                        assert len(list(state['output'].glob('*.pretty/*.kicad_mod'))) == 1
                if state['name'] == 'merge-footprints':
                    window.grab().save(str(root / 'kicad-controls.png'))
                report['cases'].append({'name': state['name'], 'statuses': [r.status for r in rows],
                                        'files': sorted(set(p for r in rows for p in r.files)), 'success': True})
                state['phase'] = 'next'
        except Exception as exc:
            finish(type(exc).__name__ + ': ' + str(exc))
    timer.timeout.connect(tick)
    timer.start()
