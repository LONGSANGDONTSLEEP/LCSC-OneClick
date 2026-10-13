"""Embed STEP in AD footprints and link KiCad models.

Native framing researched against AltiumSharp PcbLibWriter and KiCad's Altium
reader. Original implementation, AGPL-3.0-or-later; see THIRD-PARTY.md.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import hashlib
import io
import math
from pathlib import Path
import re
import struct
import uuid
import zlib
import olefile
from altium import _block, _common, _parameters, _pcblib_component
from altium_ir import raw
from backend import atomic_write
from compound_storage import compound_file
from library_merge import footprint_signature
from model3d import document, model_reference
from kicad import q
from model_alignment import align_placement

@dataclass(frozen=True)
class Placement:
    x: float
    y: float
    z: float
    rx: float
    ry: float
    rz: float
    width: float
    height: float

def finite(value):
    value = float(value)
    if not math.isfinite(value) or abs(value) > 100000:
        raise ValueError('3D 放置参数超出支持范围')
    return value

def placement(data):
    source = document(document(data.get('packageDetail')).get('dataStr'))
    head = document(source.get('head'))
    reference = model_reference(data)
    nodes = [document(document(s.partition('~')[2]).get('attrs')) for s in source.get('shape', [])
             if isinstance(s, str) and s.startswith('SVGNODE~')]
    nodes = [n for n in nodes if n.get('uuid') and n.get('c_etype') in (None, 'outline3D')]
    if len(nodes) != 1 or not reference:
        raise ValueError('源库缺少唯一 3D 放置记录，未猜测模型位置')
    node = nodes[0]
    origin = str(node.get('c_origin', '')).split(',')
    rotation = str(node.get('c_rotation', '0,0,0')).split(',')
    if len(origin) != 2 or len(rotation) != 3:
        raise ValueError('源库 3D 原点或旋转格式不受支持')
    # EasyEDA canvas units are 10 mil; model geometry is already in mm.
    x = (finite(origin[0]) - finite(head['x'])) * 0.254
    y = -(finite(origin[1]) - finite(head['y'])) * 0.254
    z = finite(node.get('z', 0)) * 0.254
    scale = re.search(r'scale\(([^)]+)\)', str(node.get('transform', '')))
    if scale and any(abs(finite(v) - 1) > 1e-8 for v in re.split(r'[,\s]+', scale[1].strip())):
        raise ValueError('非单位缩放的源模型暂不自动绑定')
    return Placement(x, y, z, *(finite(v) for v in rotation),
                     abs(finite(node.get('c_width', 0))) * 0.254,
                     abs(finite(node.get('c_height', 0))) * 0.254)

def parameter_block(values):
    # ComponentBody and Model metadata use C strings without a leading pipe.
    return _block(_parameters(values)[4:].lstrip(b'|'))

def mil(mm):
    return f'{mm / 0.0254:.8f}mil'

def body_record(model_id, name, p, outline_center=None):
    values = {
        'V7_LAYER': 'MECHANICAL1', 'NAME': '', 'KIND': 0, 'SUBPOLYINDEX': -1,
        'UNIONINDEX': 0, 'ARCRESOLUTION': '0.5mil', 'ISSHAPEBASED': 'FALSE',
        'CAVITYHEIGHT': '0mil', 'STANDOFFHEIGHT': '0mil', 'OVERALLHEIGHT': '0mil',
        'BODYPROJECTION': 0, 'BODYCOLOR3D': 8421504, 'BODYOPACITY3D': '1.000',
        'IDENTIFIER': '', 'TEXTURE': '', 'TEXTURECENTERX': '0mil', 'TEXTURECENTERY': '0mil',
        'TEXTURESIZEX': '0mil', 'TEXTURESIZEY': '0mil', 'TEXTUREROTATION': '0',
        'MODELID': model_id, 'MODEL.CHECKSUM': 0, 'MODEL.EMBED': 'TRUE', 'MODEL.NAME': name,
        'MODEL.2D.X': mil(p.x), 'MODEL.2D.Y': mil(p.y), 'MODEL.2D.ROTATION': '0.000',
        'MODEL.3D.ROTX': f'{p.rx:.3f}', 'MODEL.3D.ROTY': f'{p.ry:.3f}',
        'MODEL.3D.ROTZ': f'{p.rz:.3f}', 'MODEL.3D.DZ': mil(p.z),
        'MODEL.MODELTYPE': 1, 'MODEL.MODELSOURCE': 'Undefined',
    }
    w, h = p.width / 2, p.height / 2
    angle = 0 if outline_center else math.radians(p.rz)
    cx, cy = outline_center or (p.x, p.y)
    vertices = [(cx + x * math.cos(angle) - y * math.sin(angle),
                 cy + x * math.sin(angle) + y * math.cos(angle))
                for x, y in [(-w, -h), (w, -h), (w, h), (-w, h)]]
    body = _common(57) + bytes(5) + parameter_block(values) + struct.pack('<I', 4)
    body += b''.join(struct.pack('<dd', raw(x), raw(y)) for x, y in vertices)
    return b'\x0c' + _block(body)

def read_streams(path):
    with olefile.OleFileIO(str(path)) as ole:
        return {'/'.join(p): ole.openstream(p).read() for p in ole.listdir()}

def embed_ad(path, assignments):
    """Rebuild only a newly generated library; preserve all original streams."""
    streams = read_streams(path)
    if struct.unpack('<I', streams['Library/Models/Header'])[0]:
        raise ValueError('封装库已含模型，本次未重复写入')
    sections = {p.split('/')[0] for p in streams if p.endswith('/Data') and not p.startswith('Library/')}
    signatures = {footprint_signature({k: v for k, v in streams.items() if k.startswith(s + '/')}): s
                  for s in sections}
    grouped = {}
    for item in assignments:
        signature = item['signature']
        section = signatures.get(signature) if signature is not None else None
        if not section:
            item['errors'].append('AD：无法唯一匹配封装几何，未绑定')
            continue
        grouped.setdefault(section, []).append(item)
    metadata, models = [], {}
    for section, items in grouped.items():
        if len({(i['digest'], i['placement']) for i in items}) != 1:
            for item in items:
                item['errors'].append('AD：共享二维封装关联不同 3D，未自动选用其中一个模型')
            continue
        item = items[0]
        key = item['digest']
        if key not in models:
            index = len(models)
            model_id = '{' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'lcsc-step:' + key)).upper() + '}'
            models[key] = model_id
            name = 'LCSC_' + key[:16] + '.step'
            metadata.append(parameter_block({'EMBED': 'TRUE', 'MODELSOURCE': 'Undefined', 'ID': model_id,
                'ROTX': '0.000', 'ROTY': '0.000', 'ROTZ': '0.000', 'DZ': 0, 'CHECKSUM': 0, 'NAME': name}))
            streams[f'Library/Models/{index}'] = zlib.compress(item['step'])
        model_id = models[key]
        streams[section + '/Data'] += body_record(model_id, 'LCSC_' + key[:16] + '.step', item['placement'],
            item.get('alignment', {}).get('target_center_mm'))
        count = struct.unpack('<I', streams[section + '/Header'])[0]
        streams[section + '/Header'] = struct.pack('<I', count + 1)
        for i in items:
            i['ad'] = {'section': section, 'model_id': model_id, 'embedded': True}
    if models:
        streams['Library/Models/Header'] = struct.pack('<I', len(models))
        streams['Library/Models/Data'] = b''.join(metadata)
        payload = compound_file(streams)
        # Read back the CFB and decompress every model before committing it.
        with olefile.OleFileIO(io.BytesIO(payload)) as ole:
            for item in assignments:
                if item.get('ad'):
                    index = list(models).index(item['digest'])
                    if zlib.decompress(ole.openstream(f'Library/Models/{index}').read()) != item['step']:
                        raise ValueError('AD 内嵌 STEP 回读校验失败')
        atomic_write(Path(path), payload)

def link_kicad(root, assignments):
    grouped = {}
    for item in assignments:
        for file in item['result'].files:
            if file.endswith('.kicad_mod'):
                grouped.setdefault(Path(file), []).append(item)
    for path, items in grouped.items():
        if len({(i['digest'], i['placement']) for i in items}) != 1:
            for item in items:
                item['errors'].append('KiCad：共享封装存在不同模型，未自动关联')
            continue
        item, p = items[0], items[0]['placement']
        # KiCad external models resolve against the user's project directory.
        reference = '${KIPRJMOD}/' + item['step_path'].relative_to(root).as_posix()
        text = path.read_text(encoding='utf-8').rstrip()
        if not text.endswith(')') or '(model ' in text:
            raise ValueError('KiCad 封装模型结构不适合追加')
        model = (f'\n  (model {q(reference)}\n    (offset (xyz {p.x:.8f} {p.y:.8f} {p.z:.8f}))'
                 f'\n    (scale (xyz 1 1 1))\n    (rotate (xyz {-p.rx:.8f} {-p.ry:.8f} {-p.rz:.8f}))\n  )\n)\n')
        atomic_write(path, (text[:-1] + model).encode('utf-8'))
        for i in items:
            i['kicad'] = {'footprint': path.relative_to(root).as_posix(), 'path': reference}

def integrate_models(results, root, api, cancelled, corrections=None):
    assignments, report = [], []
    for result in results:
        if not set(result.completed_formats) & {'PCBLIB', 'KICAD_FOOTPRINT'}:
            continue
        row = {'part': result.part, 'ad': None, 'kicad': None, 'errors': []}
        report.append(row)
        steps = [Path(f) for f in result.files if f.lower().endswith('.step')]
        if not steps:
            row['errors'].append('没有可用 STEP，保留二维封装')
            continue
        if cancelled.is_set():
            row['errors'].append('任务已取消，尚未完成 3D 绑定')
            continue
        try:
            data = api.get_cad_data_of_component(result.part)
            p = placement(data)
            step = steps[0].read_bytes()
            if not step.lstrip().startswith(b'ISO-10303-21;') or b'END-ISO-10303-21;' not in step:
                raise ValueError('STEP 数据不完整')
            p, alignment = align_placement(data, p, step)
            delta = (corrections or {}).get(result.part, [0, 0, 0])
            if len(delta) != 3:
                raise ValueError('手动校准必须包含 X/Y/Z 三个毫米偏移')
            dx, dy, dz = (finite(v) for v in delta)
            p = replace(p, x=p.x + dx, y=p.y + dy, z=p.z + dz)
            alignment['manual_delta_mm'] = [dx, dy, dz]
            alignment['target_center_mm'] = [alignment['target_center_mm'][0] + dx,
                                              alignment['target_center_mm'][1] + dy]
            alignment['corrected_translation_mm'] = [p.x, p.y, p.z]
            signature = None
            if 'PCBLIB' in result.completed_formats:
                _, streams = _pcblib_component(data, result.part, lambda: None, merged=True)
                signature = footprint_signature(streams)
            assignments.append(dict(row, result=result, errors=row['errors'], placement=p, alignment=alignment,
                step=step, step_path=steps[0], digest=hashlib.sha256(step).hexdigest(),
                signature=signature))
        except Exception as exc:
            row['errors'].append(str(exc))
    ad_items = [i for i in assignments if 'PCBLIB' in i['result'].completed_formats]
    if ad_items:
        try:
            embed_ad(root / 'OneClick.PcbLib', ad_items)
        except Exception as exc:
            for i in ad_items:
                i['ad'] = None
                i['errors'].append('AD 3D 嵌入失败：' + str(exc))
    ki_items = [i for i in assignments if 'KICAD_FOOTPRINT' in i['result'].completed_formats]
    if ki_items:
        try:
            link_kicad(root, ki_items)
        except Exception as exc:
            for i in ki_items:
                if not i.get('kicad'):
                    i['errors'].append('KiCad 3D 关联失败：' + str(exc))
    for item in assignments:
        row = next(r for r in report if r['part'] == item['part'])
        row.update(ad=item.get('ad'), kicad=item.get('kicad'), placement=item['placement'].__dict__,
                   alignment=item['alignment'])
    for row in report:
        result = next(r for r in results if r.part == row['part'])
        if row['errors']:
            result.status = '部分完成' if result.completed_formats else result.status
            result.export_errors.extend(row['errors'])
            result.message += '；3D：' + '；'.join(row['errors'])
        else:
            message = ' / '.join(v for v, enabled in [('AD 已内嵌 STEP', row['ad']), ('KiCad 已关联 STEP', row['kicad'])] if enabled)
            result.message += '；' + message
    return report
