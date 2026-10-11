"""Verify the frozen KiCad demo through its actual window, with an offline API."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--exe', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--cli', type=Path)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(parents=True, exist_ok=True)
fixtures = root / 'fixtures'
fixtures.mkdir(exist_ok=True)
for part in ('C20197', 'C2765186', 'C23922', 'C8734'):
    shutil.copyfile(ROOT / 'app/tests/fixtures' / (part + '.json'), fixtures / (part + '.json'))
env = os.environ.copy()
env.update(LOCALAPPDATA=str(root / 'LocalAppData'), APPDATA=str(root / 'AppData'),
           TEMP=str(root / 'Temp'), TMP=str(root / 'Temp'), KICAD_CONFIG_HOME=str(root / 'kicad-config'), PYTHONUTF8='1')
(root / 'Temp').mkdir(exist_ok=True)
executable = args.exe.resolve()
with (root / 'process.log').open('w', encoding='utf-8') as output:
    process = subprocess.run([str(executable), '--self-test-kicad', str(root)], env=env, stdout=output,
                             stderr=subprocess.STDOUT, timeout=120)
assert process.returncode == 0, process.returncode
report = json.loads((root / 'kicad-verification.json').read_text('utf-8'))
version = re.search(r"VERSION = '([^']+)'", (ROOT / 'app/main.py').read_text('utf-8')).group(1)
assert report['success'] and report['frozen'] and report['version'] == version, report
assert [c['name'] for c in report['cases']] == ['individual', 'combined', 'mixed', 'collision']
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'app/tests')]
from altium_inspect import schematic, pcb
mixed = next(c for c in report['cases'] if c['name'] == 'mixed')
for name in mixed['files']:
    path = Path(name)
    pins = 8 if 'C20197' in str(path) else 14
    if path.suffix == '.SchLib':
        assert len(schematic(path.read_bytes())[2]) == pins
    if path.suffix == '.PcbLib':
        assert len(pcb(path.read_bytes())[1]) == (8 if pins == 8 else 18)
if args.cli:
    for kind, target in (('sym', root / 'combined/LCSC3D.kicad_sym'), ('fp', root / 'combined/LCSC3D.pretty')):
        (root / ('native-' + kind)).mkdir(exist_ok=True)
        command = [str(args.cli.resolve()), kind, 'export', 'svg', '--output', str(root / ('native-' + kind)), str(target)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        (root / ('native-' + kind + '.log')).write_text(result.stdout + result.stderr, 'utf-8')
        assert result.returncode == 0 and 'Error:' not in result.stderr, result.stderr
    expected_units = sum(len(json.loads((fixtures / (p + '.json')).read_text('utf-8')).get('subparts') or [None])
                         for p in ('C23922', 'C8734'))
    assert len(list((root / 'native-sym').glob('*.svg'))) == expected_units
    assert len(list((root / 'native-fp').glob('*.svg'))) == 1
    report['frozen_files_read_by_kicad'] = True
report['ad_mixed_geometry_verified'] = True
report['exe_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
(root / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False))
