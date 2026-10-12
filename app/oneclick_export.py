"""OneClick workflow. SPDX-License-Identifier: AGPL-3.0-or-later."""
from __future__ import annotations
import csv
import io
import json
import re
import threading
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from backend import Options, download_batch

PRESETS = {
    'Altium Designer': ('SCHLIB', 'PCBLIB', 'STEP', 'OBJ'),
    'KiCad': ('KICAD_SYMBOL', 'KICAD_FOOTPRINT', 'STEP', 'OBJ'),
    'AD + KiCad': ('SCHLIB', 'PCBLIB', 'KICAD_SYMBOL', 'KICAD_FOOTPRINT', 'STEP', 'OBJ'),
    '仅 3D': ('STEP', 'OBJ'),
}

def extract_parts(text):
    """Extract whole C numbers from pasted BOMs and product URLs."""
    return list(dict.fromkeys(m.upper() for m in re.findall(r'(?<![A-Za-z0-9])C[0-9]+(?![A-Za-z0-9])', text, re.I)))

def read_bom(path):
    raw = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'gb18030', 'utf-16'):
        try:
            return extract_parts(raw.decode(encoding))
        except UnicodeError:
            pass
    raise ValueError('文件编码无法识别，请另存为 UTF-8 CSV')

def export_bundle(parts, destination, preset='AD + KiCad', cancelled=None, progress=None, api=None):
    if not parts:
        raise ValueError('没有有效的 C 编号')
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    # Each run owns a unique directory; previous exports are never replaced.
    from tempfile import mkdtemp
    root = Path(mkdtemp(prefix=datetime.now().strftime('LCSC_%Y%m%d_%H%M%S_'), dir=destination))
    options = Options(root, formats=PRESETS[preset], merge_schlib=True, merge_pcblib=True,
                      schlib_name='OneClick', pcblib_name='OneClick', kicad_merge=True,
                      kicad_library_name='OneClick')
    results = download_batch(parts, options, cancelled or threading.Event(), progress, api=api)
    report = {'preset': preset, 'requested': parts, 'results': [asdict(r) for r in results],
              'cancelled': bool(cancelled and cancelled.is_set()),
              'missing_results': [p for p in parts if p not in {r.part for r in results}],
              'note': '导出元件库，不是整板 PCB 工程。3D 需在 EDA 中手动绑定和校准。'}
    (root / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = io.StringIO(newline='')
    writer = csv.writer(rows)
    writer.writerow(['C编号', '型号', '状态', '成功格式', '错误说明'])
    for r in results:
        writer.writerow([r.part, r.title, r.status, '/'.join(r.completed_formats), r.message])
    (root / 'results.csv').write_text(rows.getvalue(), encoding='utf-8-sig')
    failed = [r.part for r in results if set(options.formats) - set(r.completed_formats)]
    failed += report['missing_results']
    (root / 'retry.txt').write_text('\n'.join(failed), encoding='utf-8')
    (root / '使用说明.txt').write_text(
        'AD：打开 OneClick.SchLib / OneClick.PcbLib。\n'
        'KiCad：在库管理器添加 OneClick.kicad_sym 和 OneClick.pretty，库昵称均为 OneClick。\n'
        'STEP/OBJ 在 OneClick_3D 目录；仅3D模式按器件分目录。模型需要手动绑定并核对旋转、位置和高度。\n'
        'results.csv 和 manifest.json 记录真实结果。retry.txt 可重新导入；没有官方3D的器件重试仍可能缺失。\n'
        '这是元件符号、PCB封装和3D资源包，不含整板工程。生产前按数据手册核对。\n', encoding='utf-8-sig')
    archive = root.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in sorted(root.rglob('*')):
            if file.is_file():
                z.write(file, file.relative_to(root).as_posix())
    return results, root, archive, failed
