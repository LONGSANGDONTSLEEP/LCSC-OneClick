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
            for part in ('C20197', 'C2765186', 'C23922', 'C8734')}
    class OfflineApi(main.NetworkApi):
        def get_cad_data_of_component(self, part):
            self.check_cancelled()
            return data[part]
    main.NetworkApi = OfflineApi
    window.request_component_info = lambda: None
    window.show_preview = lambda *args, **kwargs: None
    window.resize(1380, 880)
    cases = [('individual', False, False, ['C20197', 'C2765186']),
             ('combined', True, False, ['C23922', 'C8734']),
             ('mixed', True, True, ['C20197', 'C2765186']),
             ('collision', True, False, ['C23922', 'C8734'])]
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
                    window.grab().save(str(root / 'kicad-controls.png'))
                    finish()
                    return
                name, merge, ad, parts = cases[state['index']]
                output = root / ('combined' if name == 'collision' else name)
                state.update(name=name, output=output, before=hashes(output), parts=parts)
                window.lib_append_box.setChecked(False)
                window.lib_merge_box.setChecked(False)
                window.step_box.setChecked(False)
                window.obj_box.setChecked(False)
                window.schlib_box.setChecked(ad)
                window.pcblib_box.setChecked(ad)
                window.kicad_symbol_box.setChecked(True)
                window.kicad_footprint_box.setChecked(True)
                window.kicad_merge_box.setChecked(merge)
                window.kicad_name_input.setText('LCSC3D')
                window.path_input.setText(str(output))
                window.input.setPlainText('\n'.join(parts))
                assert window.load_queue()
                assert window.export_options().formats[-2:] == ('KICAD_SYMBOL', 'KICAD_FOOTPRINT')
                window.start_batch()
                state['phase'] = 'wait'
            elif not window.batch_running and not (window.worker and window.worker.isRunning()):
                rows = [window.results[p] for p in state['parts']]
                if state['name'] == 'collision':
                    assert all(r.status == '失败' and '已存在' in r.message for r in rows)
                    assert hashes(state['output']) == state['before']
                else:
                    assert all(r.status == '成功' for r in rows), [r.message for r in rows]
                    assert all(Path(p).is_file() for r in rows for p in r.files)
                    if state['name'] == 'combined':
                        assert len(list(state['output'].glob('*.pretty/*.kicad_mod'))) == 1
                if state['name'] == 'mixed':
                    window.grab().save(str(root / 'kicad-mixed-success.png'))
                report['cases'].append({'name': state['name'], 'statuses': [r.status for r in rows],
                                        'files': sorted(set(p for r in rows for p in r.files)), 'success': True})
                state['phase'] = 'next'
        except Exception as exc:
            finish(type(exc).__name__ + ': ' + str(exc))
    timer.timeout.connect(tick)
    timer.start()
