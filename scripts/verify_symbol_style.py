"""Exercise both symbol styles in the frozen app with an isolated profile.

Preview checks use anonymous public resources. Export checks use the existing
offline fixtures and independently read the resulting native SchLib records.
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import olefile


def verify(output, sandbox):
    root = Path(__file__).resolve().parents[1]
    sandbox.mkdir(parents=True, exist_ok=True)
    source = output / 'LCSC3D.exe'
    executable = sandbox / 'LCSC3D.exe'
    assert source.resolve() != executable.resolve()
    shutil.copy2(source, executable)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(('PYTHON', 'QT_', 'QML_', 'QSG_', 'LCSC3D_')):
            environment.pop(key)
    windows = Path(os.environ['SystemRoot'])
    environment['PATH'] = os.pathsep.join(map(str, (windows / 'System32', windows)))
    environment['LOCALAPPDATA'] = str(sandbox / 'LocalAppData')
    temporary = sandbox / 'Temp'
    temporary.mkdir(exist_ok=True)
    environment['TEMP'] = environment['TMP'] = str(temporary)

    def run(name, flag, style, extra=()):
        folder = sandbox / name
        folder.mkdir(exist_ok=True)
        with (folder / 'stdout.log').open('wb') as stdout, (folder / 'stderr.log').open('wb') as stderr:
            result = subprocess.run([str(executable), flag, str(folder), '--self-test-symbol-style', style, *extra],
                cwd=sandbox, env=environment, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=180)
        assert result.returncode == 0, (name, (folder / 'stderr.log').read_text('utf-8', errors='replace'))
        return folder

    reports = {}
    for style in ('jlc', 'ad'):
        folder = run('preview-' + style, '--self-test', style)
        report = json.loads((folder / 'verification.json').read_text('utf-8'))
        assert report['frozen'] and report['symbol_style'] == style
        assert report['preview'] == 'ready' and report['second_3d_ready']
        assert report['preview_window'] == {'hwnd_preserved': True, 'events': []}
        assert report['library_previews']['C2040']['symbol_pins'] == [57]
        assert report['library_previews']['C20197']['symbol_pins'] == [8]
        reports[style] = dict(preview=True, symbol_pins=[57, 8])
        print('PREVIEW', style, 'PASS', flush=True)

    sys.path.insert(0, str(root / 'app/tests'))
    from altium_inspect import schematic
    for part in ('C163691', 'C2765186'):
        folder = run('regression-' + part, '--self-test-ad', 'ad', ('--self-test-ad-parts', part))
        report = json.loads((folder / 'verification.json').read_text('utf-8'))
        assert report['success'] and report['preview'] == 'ready' and report['symbol_style'] == 'ad'
        file = next(Path(f) for f in report['results'][0]['files'] if f.endswith('.SchLib'))
        header, records, pins = schematic(file.read_bytes())
        assert header['AREACOLOR'] == '16777215'
        assert not any(row.get('RECORD') == '10' for row in records)
        if part == 'C163691':
            assert len(pins) == 2 and not any('AREACOLOR' in row for row in records)
        else:
            assert any(row.get('RECORD') == '14' for row in records)
        print('REGRESSION', part, 'PASS', flush=True)

    folder = sandbox / 'export-ad'
    fixtures = folder / 'fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    for part in ('C2040', 'C20197', 'C2765186', 'C23922', 'C8734'):
        shutil.copy2(root / 'app/tests/fixtures' / (part + '.json'), fixtures)
    run('export-ad', '--self-test-export', 'ad')
    report = json.loads((folder / 'export-verification.json').read_text('utf-8'))
    assert report['frozen'] and report['success'] and len(report['cases']) == 17, report
    checked = 0
    for case in report['cases']:
        files = {Path(f) for row in case.get('results', []) for f in row['files'] if f.endswith('.SchLib')}
        for path in files:
            payload = path.read_bytes()
            with olefile.OleFileIO(io.BytesIO(payload)) as library:
                sections = [p[0] for p in library.listdir() if len(p) == 2 and p[1] == 'Data']
            for section in sections:
                header, records, pins = schematic(payload, section)
                part = next(r['TEXT'] for r in records if r.get('NAME', '').upper() == 'SUPPLIERPART')
                if part == 'C2765186' or part == 'C23922' and case['name'].startswith('shared-append'):
                    continue  # Pre-existing entries retain their original appearance.
                assert pins
                for row in records:
                    assert row.get('RECORD') != '10'
                    for key in ('FONTID', 'NAME_CUSTOMFONTID', 'DESIGNATOR_CUSTOMFONTID'):
                        if key in row:
                            assert header['FONTNAME' + row[key]] == 'Times New Roman'
                            assert header['SIZE' + row[key]] == '8'
                    if row.get('RECORD') == '2':
                        assert row['COLOR'] == '0'
                        assert row['NAME_CUSTOMCOLOR'] == row['DESIGNATOR_CUSTOMCOLOR'] == '8388608'
                    if row.get('RECORD') in ('6', '7', '8', '10', '14'):
                        assert row['COLOR'] == '128' and row['LINEWIDTH'] == '1'
                        if 'AREACOLOR' in row:
                            assert row['AREACOLOR'] in ('128', '11599871')
                checked += 1
    assert checked > 20
    assert source.read_bytes() == executable.read_bytes()
    result = dict(success=True, styles=reports, regression_parts=['C163691', 'C2765186'],
                  export_cases=17, independently_read_symbols=checked)
    (sandbox / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=root / 'outputs')
    parser.add_argument('--verify-dir', type=Path, default=root / 'work/symbol-style/frozen')
    args = parser.parse_args()
    print(json.dumps(verify(args.output_dir.resolve(), args.verify_dir.resolve()), ensure_ascii=False))
