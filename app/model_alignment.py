"""Measure STEP geometry in its real coordinate system before placing it."""
import math
from pathlib import Path
import tempfile
from dataclasses import replace
from app_paths import temporary_directory
from model3d import document, read_obj

def rotated(vertex, angles):
    x, y, z = vertex
    for axis, degrees in enumerate(angles):
        c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
        if axis == 0:
            y, z = c * y - s * z, s * y + c * z
        elif axis == 1:
            x, z = c * x + s * z, -s * x + c * z
        else:
            x, y = c * x - s * y, s * x + c * y
    return x, y, z

def step_vertices(step):
    import cascadio
    with tempfile.TemporaryDirectory(prefix='step-align-', dir=temporary_directory()) as folder:
        source, mesh = Path(folder) / 'model.step', Path(folder) / 'mesh.obj'
        source.write_bytes(step)
        code = cascadio.step_to_obj(str(source), str(mesh), tol_linear=0.001,
                                    tol_angular=0.1, use_parallel=False, use_colors=False)
        if code != 0 or not mesh.exists():
            raise ValueError('STEP 几何解析失败，未猜测原点')
        return read_obj(mesh.read_text(encoding='utf-8')).vertices

def projection_bounds(data):
    source = document(document(data.get('packageDetail')).get('dataStr'))
    head = document(source.get('head'))
    points = []
    def visit(node):
        attrs = document(node.get('attrs'))
        values = str(attrs.get('points', '')).split()
        if values:
            if len(values) % 2:
                raise ValueError('源 3D 投影坐标不完整')
            for index in range(0, len(values), 2):
                x, y = float(values[index]), float(values[index + 1])
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError('源 3D 投影坐标无效')
                points.append(((x - float(head['x'])) * .254, -(y - float(head['y'])) * .254))
        for child in node.get('childNodes', []):
            visit(child)
    for shape in source.get('shape', []):
        if isinstance(shape, str) and shape.startswith('SVGNODE~'):
            visit(document(shape.partition('~')[2]))
    if not points:
        return None
    return [(min(p[i] for p in points), max(p[i] for p in points)) for i in range(2)]

def align_placement(data, p, step):
    points = [rotated(v, (p.rx, p.ry, p.rz)) for v in step_vertices(step)]
    bounds = [(min(v[i] for v in points), max(v[i] for v in points)) for i in range(3)]
    center = [(a + b) / 2 for a, b in bounds[:2]]
    target = projection_bounds(data)
    target_center = [(a + b) / 2 for a, b in target] if target else [p.x, p.y]
    if target:
        for axis in range(2):
            expected, measured = target[axis][1] - target[axis][0], bounds[axis][1] - bounds[axis][0]
            if abs(measured - expected) > max(.1, .03 * expected):
                raise ValueError('STEP 尺寸与源 3D 投影不一致，未自动居中；请核对模型或使用手动偏移')
    aligned = replace(p, x=target_center[0] - center[0], y=target_center[1] - center[1],
                      z=p.z - bounds[2][0],
                      width=target[0][1] - target[0][0] if target else p.width,
                      height=target[1][1] - target[1][0] if target else p.height)
    diagnostic = {'method': 'STEP mesh to source projection', 'step_rotated_bounds_mm': bounds,
                  'source_projection_bounds_mm': target, 'target_center_mm': target_center,
                  'geometry_shift_mm': [-center[0], -center[1], -bounds[2][0]],
                  'corrected_translation_mm': [aligned.x, aligned.y, aligned.z]}
    return aligned, diagnostic
