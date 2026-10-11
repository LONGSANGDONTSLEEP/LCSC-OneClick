"""Versioned, target-independent component model.

Geometry is Decimal millimetres in object-local, Y-up coordinates; angles are
counterclockwise degrees. The immutable model is never styled in place.
The wire format preserves unknown optional fields; new required semantics must
declare a feature. Unknown major versions and required features fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
import math
import types
from typing import get_args, get_origin, get_type_hints

from errors import DownloadError

SCHEMA_VERSION = '1.0'
CORE_FEATURES = frozenset({'local-mm-y-up', 'symbol-units', 'pad-copper-drill'})
# Open vocabulary: readers preserve future/source-specific values verbatim.
# `power` records an undirected source declaration; it is not `power_in`.
PIN_ELECTRICAL_TYPES = frozenset({'unspecified', 'input', 'output', 'bidirectional',
    'tri_state', 'passive', 'free', 'power', 'power_in', 'power_out',
    'open_collector', 'open_emitter', 'no_connect'})
D = Decimal
ZERO = D(0)


def decimal(value):
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DownloadError('EDA 坐标或尺寸无效') from exc
    if not result.is_finite() or abs(result) > 10_000_000:
        raise DownloadError('EDA 坐标或尺寸超出支持范围')
    return result


@dataclass(frozen=True)
class Extension:
    namespace: str
    version: str
    payload: str = '{}'
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class Record:
    source_id: str = ''
    extensions: tuple[Extension, ...] = ()
    # Encoded unknown OPTIONAL wire fields; never interpreted as executable data.
    extra_json: str = '{}'


@dataclass(frozen=True)
class Point(Record):
    x: Decimal = ZERO
    y: Decimal = ZERO


@dataclass(frozen=True)
class Stroke(Record):
    width: Decimal = D('0.254')
    color: str = '#000000'
    style: str = 'solid'


@dataclass(frozen=True)
class TextStyle(Record):
    family: str = ''
    height: Decimal = D('2.822222222222222222222222222')  # 8 pt in mm
    color: str = '#000000'
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class Graphic(Record):
    kind: str = ''
    points: tuple[Point, ...] = ()
    size: Point = field(default_factory=Point)
    radius: Point = field(default_factory=Point)
    # SVG path grammar, with all coordinates/lengths normalized to local mm.
    path: str = ''
    stroke: Stroke = field(default_factory=Stroke)
    fill: str = 'none'
    text: str = ''
    text_style: TextStyle = field(default_factory=TextStyle)
    rotation: Decimal = ZERO
    justify: str = 'start'
    layer: str = ''
    visible: bool = True
    mirrored: bool = False
    region_type: str = 'solid'


@dataclass(frozen=True)
class Pin(Record):
    number: str = ''
    name: str = ''
    connection: Point = field(default_factory=Point)
    body: Point = field(default_factory=Point)
    electrical: str = 'unspecified'  # Source semantics, never an export override.
    visible: bool = True
    name_visible: bool = True
    number_visible: bool = True
    inverted: bool = False
    clock: bool = False
    color: str = '#000000'
    name_style: TextStyle = field(default_factory=TextStyle)
    number_style: TextStyle = field(default_factory=TextStyle)
    name_position: Point = field(default_factory=Point)
    number_position: Point = field(default_factory=Point)


@dataclass(frozen=True)
class Pad(Record):
    number: str = ''
    shape: str = 'rect'
    center: Point = field(default_factory=Point)  # hole origin, when drilled
    copper_center: Point = field(default_factory=Point)
    size: Point = field(default_factory=Point)
    vertices: tuple[Point, ...] = ()
    rotation: Decimal = ZERO
    layer: str = 'top_copper'
    drill: Decimal = ZERO
    slot_length: Decimal = ZERO
    drill_rotation: Decimal = ZERO  # absolute orientation, independent of copper
    plated: bool = True
    paste_margin: Decimal = ZERO
    solder_margin: Decimal = ZERO
    mechanical_hole: bool = False


@dataclass(frozen=True)
class Property(Record):
    name: str = ''
    value: str = ''


@dataclass(frozen=True)
class SymbolUnit(Record):
    number: int = 1
    items: tuple[Pin | Graphic, ...] = ()


@dataclass(frozen=True)
class Symbol(Record):
    name: str = ''
    reference: str = 'U?'
    units: tuple[SymbolUnit, ...] = ()
    properties: tuple[Property, ...] = ()
    licence: str = ''


@dataclass(frozen=True)
class Footprint(Record):
    name: str = ''
    items: tuple[Pad | Graphic, ...] = ()
    licence: str = ''


@dataclass(frozen=True)
class Model3D(Record):
    identifier: str = ''
    name: str = ''
    # Source placement is preserved in extensions until verified by an adapter.
    translation: tuple[Decimal, ...] = ()
    rotation: tuple[Decimal, ...] = ()
    scale: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class Diagnostic(Record):
    domain: str = ''
    severity: str = 'error'
    code: str = ''
    message: str = ''


@dataclass(frozen=True)
class Component(Record):
    part: str = ''
    title: str = ''
    description: str = ''
    source: str = 'JLCEDA/EasyEDA Official Library'
    source_url: str = ''
    footprint_name: str = ''
    has_footprint: bool = False
    symbol: Symbol | None = None
    footprint: Footprint | None = None
    models: tuple[Model3D, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    schema_version: str = SCHEMA_VERSION
    required_features: tuple[str, ...] = ('local-mm-y-up',)


RECORD_TYPES = {t.__name__: t for t in (
    Extension, Point, Stroke, TextStyle, Graphic, Pin, Pad, Property, SymbolUnit,
    Symbol, Footprint, Model3D, Diagnostic, Component)}


def _encode(value):
    if type(value) in RECORD_TYPES.values():
        result = json.loads(value.extra_json) if isinstance(value, Record) else {}
        result.update({'@type': type(value).__name__})
        result.update({f.name: _encode(getattr(value, f.name)) for f in fields(value) if f.name != 'extra_json'})
        return result
    if isinstance(value, D):
        return {'@decimal': str(value)}
    if isinstance(value, tuple):
        return [_encode(v) for v in value]
    return value


def _decode(value):
    if isinstance(value, list):
        return tuple(_decode(v) for v in value)
    if not isinstance(value, dict):
        return value
    if '@decimal' in value:
        return decimal(value['@decimal'])
    cls = RECORD_TYPES.get(value.get('@type'))
    if cls is None:
        raise DownloadError('IR 包含未知对象类型：' + str(value.get('@type', '')))
    known = {f.name for f in fields(cls)} - {'extra_json'}
    values = {key: _decode(item) for key, item in value.items() if key in known}
    unknown = {key: item for key, item in value.items() if key not in known and key != '@type'}
    if unknown:
        if not issubclass(cls, Record):
            raise DownloadError('IR 扩展记录包含未知字段')
        values['extra_json'] = json.dumps(unknown, ensure_ascii=False)
    return cls(**values)


@lru_cache(maxsize=None)
def _hints(cls):
    return get_type_hints(cls)


def _validate(value, expected, depth=0):
    if depth > 60:
        raise DownloadError('IR 嵌套层级过深')
    origin, args = get_origin(expected), get_args(expected)
    if origin is types.UnionType:
        if not any(isinstance(value, t) for t in args):
            raise DownloadError('IR 字段类型不匹配')
        expected = next(t for t in args if isinstance(value, t))
    elif origin is tuple:
        if not isinstance(value, tuple) or len(value) > 200000:
            raise DownloadError('IR 集合无效或过大')
        for item in value:
            _validate(item, args[0], depth + 1)
        return
    if type(value) is not expected:
        raise DownloadError('IR 字段类型不匹配：' + str(expected))
    if isinstance(value, D):
        decimal(value)
    elif type(value) in RECORD_TYPES.values():
        for key, hint in _hints(type(value)).items():
            _validate(getattr(value, key), hint, depth + 1)
        if isinstance(value, Record):
            if not isinstance(json.loads(value.extra_json), dict):
                raise DownloadError('IR 可选扩展字段必须为对象')


def validate(component):
    _validate(component, Component)
    try:
        major, minor = (int(v) for v in component.schema_version.split('.'))
    except (ValueError, AttributeError) as exc:
        raise DownloadError('IR 版本号无效') from exc
    if major != 1 or minor < 0:
        raise DownloadError('不支持此 IR 版本：' + component.schema_version)
    missing = set(component.required_features) - CORE_FEATURES
    if missing:
        raise DownloadError('IR 需要尚不支持的能力：' + ', '.join(sorted(missing)))
    return component


def dumps(component):
    validate(component)
    return json.dumps(_encode(component), ensure_ascii=False, indent=2, allow_nan=False)


def loads(payload):
    if not isinstance(payload, str) or len(payload) > 64 * 1024 * 1024:
        raise DownloadError('IR 文档无效或过大')
    try:
        return validate(_decode(json.loads(payload)))
    except (ValueError, TypeError, RecursionError, AttributeError) as exc:
        raise DownloadError('IR 文档结构无效') from exc


def require_domain(component, domain):
    """Validate only requested content; a bad symbol must not block a footprint."""
    validate(component)
    value = getattr(component, domain)
    error = next((d.message for d in component.diagnostics if d.domain == domain and d.severity == 'error'), '')
    if value is None or error:
        raise DownloadError(error or ('官方库中没有可导出的' + ('符号' if domain == 'symbol' else '封装')))
    check_extensions(component)
    def inspect(record):
        if not isinstance(record, Record):
            return
        check_extensions(record)
        for f in fields(record):
            child = getattr(record, f.name)
            if isinstance(child, Record):
                inspect(child)
            elif isinstance(child, tuple):
                for item in child:
                    inspect(item)
    inspect(value)
    return value


def check_extensions(value):
    for extension in value.extensions:
        if extension.required:
            raise DownloadError('导出器不支持必要扩展：' + extension.namespace)


def xy(point):
    return float(point.x), float(point.y)


def rotate(point, degrees):
    angle = math.radians(float(degrees))
    x, y = xy(point)
    return Point(decimal(math.cos(angle) * x - math.sin(angle) * y),
                 decimal(math.sin(angle) * x + math.cos(angle) * y))


def graphic_paths(graphic, check_cancelled=lambda: None):
    from eda_geometry import path_points
    return path_points(graphic.path, tolerance=0.00127, check_cancelled=check_cancelled)
