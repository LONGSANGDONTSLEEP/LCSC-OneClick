"""Independent S-expression inspection and complete batch/file lifecycle tests."""
import copy
from collections import Counter
from dataclasses import replace
import gzip
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eda_ir import Pin, Pad, Point, Graphic, D
from easyeda_import import parse_component
from errors import DownloadError, Cancelled
from kicad import FORMATS, build_library, symbol_definition, footprint_definition
from library_files import commit_library
import library_files
import backend
from app_logging import close_logging
from test_eda_ir import fixture, FIXTURES


def sexpr(payload):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', payload.decode() if isinstance(payload, bytes) else payload)
    stack, roots = [], []
    for token in tokens:
        if token == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ')':
            if not stack:
                raise AssertionError('Unmatched closing parenthesis')
            stack.pop()
        else:
            if not stack:
                raise AssertionError('Atom outside a node')
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    if stack or len(roots) != 1:
        raise AssertionError('Unbalanced S-expression')
    return roots[0]


def children(node, kind):
    return [item for item in node if isinstance(item, list) and item and item[0] == kind]


def child(node, kind):
    return children(node, kind)[0]


class KiCadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, LOCALAPPDATA=str(self.root / 'profile'))
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(close_logging)
        self.api = backend.NetworkApi(threading.Event())
        self.api.get_cad_data_of_component = lambda part: fixture(part)
        self.api.get_step_3d_model = lambda uuid: b'ISO-10303-21;\n'
        self.api.get_raw_3d_model_obj = lambda uuid: 'v 0 0 0\n'

    def test_all_27_official_samples_export_and_keep_units_pin_numbers_and_pad_counts(self):
        cases = json.loads(gzip.decompress((FIXTURES / 'official_ad_cases.json.gz').read_bytes()))['cases']
        for case in cases:
            with self.subTest(part=case['part']):
                component = parse_component(case['data'], case['part'])
                artifacts, errors = build_library([component], {component.part: FORMATS}, 'Test')
                self.assertEqual(errors, {})
                symbol = child(sexpr(next(a.payload for a in artifacts if a.format == 'KICAD_SYMBOL')), 'symbol')
                units = children(symbol, 'symbol')
                self.assertEqual(len(units), len(component.symbol.units))
                for node, unit in zip(units, component.symbol.units):
                    actual = children(node, 'pin')
                    expected = [i for i in unit.items if isinstance(i, Pin)]
                    self.assertEqual([child(i, 'number')[1] for i in actual], [i.number for i in expected])
                    for pin, source in zip(actual, expected):
                        at, length = child(pin, 'at'), float(child(pin, 'length')[1])
                        self.assertAlmostEqual(float(at[1]), float(source.connection.x), places=6)
                        self.assertAlmostEqual(float(at[2]), float(source.connection.y), places=6)
                        angle = math.radians(float(at[3]))
                        self.assertAlmostEqual(float(at[1]) + math.cos(angle) * length, float(source.body.x), places=5)
                        self.assertAlmostEqual(float(at[2]) + math.sin(angle) * length, float(source.body.y), places=5)
                footprint = sexpr(next(a.payload for a in artifacts if a.format == 'KICAD_FOOTPRINT'))
                pads = children(footprint, 'pad')
                expected = [i for i in component.footprint.items if isinstance(i, Pad)]
                self.assertEqual(Counter(i[1] for i in pads), Counter(i.number for i in expected))

    def test_type_c_slot_centres_angles_plating_and_copper_remain_independent(self):
        component = parse_component(fixture(), 'C2765186')
        pads = children(sexpr(footprint_definition(component, 'TypeC')), 'pad')
        expected = [i for i in component.footprint.items if isinstance(i, Pad)]
        for node, source in zip(pads, expected):
            if not source.drill:
                continue
            at = child(node, 'at')
            self.assertAlmostEqual(float(at[1]), float(source.center.x), places=6)
            self.assertAlmostEqual(float(at[2]), -float(source.center.y), places=6)
            self.assertEqual(node[2], 'thru_hole' if source.plated else 'np_thru_hole')
            drill = child(node, 'drill')
            if source.slot_length:
                self.assertEqual(drill[1], 'oval')
                self.assertAlmostEqual(float(drill[2]), float(source.slot_length), places=6)
                self.assertAlmostEqual(float(drill[3]), float(source.drill), places=6)
                self.assertAlmostEqual(float(at[3]), float(source.drill_rotation), places=6)
            else:
                self.assertAlmostEqual(float(drill[1]), float(source.drill), places=6)

    def test_shared_footprints_and_colliding_names_produce_valid_references(self):
        components = [parse_component(fixture(part), part) for part in ('C23922', 'C8734')]
        artifacts, errors = build_library(components, {c.part: FORMATS for c in components}, 'Shared')
        self.assertFalse(errors)
        self.assertEqual(len(artifacts), 2)
        footprint = next(a for a in artifacts if a.format == 'KICAD_FOOTPRINT')
        self.assertEqual(set(footprint.parts), {'C23922', 'C8734'})
        for symbol in children(sexpr(artifacts[-1].payload), 'symbol'):
            props = {row[1]: row[2] for row in children(symbol, 'property')}
            self.assertEqual(props['Footprint'], 'Shared:' + Path(footprint.path).stem)
        second = components[1]
        pad = next(i for i in second.footprint.items if isinstance(i, Pad))
        changed = replace(pad, size=Point(pad.size.x + D(1), pad.size.y))
        second = replace(second, footprint=replace(second.footprint, items=tuple(
            changed if i is pad else i for i in second.footprint.items)))
        artifacts, errors = build_library([components[0], second], {c.part: FORMATS for c in components}, 'Shared')
        self.assertEqual(len(artifacts), 3)
        self.assertEqual(len({a.path.casefold() for a in artifacts}), 3)

    def test_text_quotes_backslashes_unicode_and_newlines_roundtrip(self):
        value = '设备 "A" (B) \\ C\nnext'
        component = parse_component(fixture('C20197'), 'C20197')
        component = replace(component, title=value)
        node = sexpr(symbol_definition(component, 'Safe'))
        props = {p[1]: p[2] for p in children(node, 'property')}
        self.assertEqual(props['Value'], value)

    def test_symbol_without_exported_footprint_has_no_broken_reference(self):
        component = parse_component(fixture(), 'C2765186')
        artifacts, errors = build_library([component], {component.part: ('KICAD_SYMBOL',)}, 'SymbolOnly')
        symbol = child(sexpr(artifacts[0].payload), 'symbol')
        self.assertEqual({p[1]: p[2] for p in children(symbol, 'property')}['Footprint'], '')

    def test_individual_combined_and_mixed_ad_batch_modes(self):
        for merge, extra in ((False, ()), (True, ()), (True, ('SCHLIB', 'PCBLIB', 'STEP'))):
            with self.subTest(merge=merge, extra=extra):
                root = self.root / str(merge) / str(len(extra))
                options = backend.Options(root, extra + FORMATS, kicad_merge=merge)
                rows = backend.download_batch(['C23922', 'C8734'], options, api=self.api)
                self.assertTrue(all(r.status == '成功' for r in rows), [r.message for r in rows])
                self.assertTrue(all(set(r.completed_formats) == set(options.formats) for r in rows))
                self.assertTrue(all(Path(p).is_file() for r in rows for p in r.files))
                self.assertEqual(len(list(root.rglob('*.kicad_sym'))), 1 if merge else 2)
                self.assertEqual(len(list(root.rglob('*.kicad_mod'))), 1 if merge else 2)

    def test_existing_library_and_existing_directory_remain_untouched(self):
        root = self.root / 'outputs'
        options = backend.Options(root, FORMATS, kicad_merge=True)
        row = backend.download_part('C20197', options, self.api)
        before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
        again = backend.download_part('C20197', options, self.api)
        self.assertEqual(row.status, '成功')
        self.assertEqual(again.status, '失败')
        self.assertIn('已存在', again.message)
        self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_bad_domain_is_partial_success_without_blocking_other_exports(self):
        data = fixture()
        data['dataStr']['shape'] = []
        self.api.get_cad_data_of_component = lambda part: data
        row = backend.download_part('C2765186', backend.Options(self.root / 'out', FORMATS), self.api)
        self.assertEqual(row.status, '部分完成', row.message)
        self.assertEqual(row.completed_formats, ['KICAD_FOOTPRINT'])
        self.assertEqual(len(row.files), 1)

    def test_mid_commit_failure_or_cancellation_rolls_back_new_files(self):
        component = parse_component(fixture(), 'C2765186')
        artifacts, _ = build_library([component], {component.part: FORMATS}, 'Test')
        original = library_files._move_new
        for error in (OSError('simulated disk error'), Cancelled()):
            root = self.root / type(error).__name__
            count = 0
            def move(source, target):
                nonlocal count
                count += 1
                if count == 2:
                    raise error
                original(source, target)
            with patch('library_files._move_new', side_effect=move), self.assertRaises(type(error)):
                commit_library(root, artifacts)
            self.assertEqual(list(root.iterdir()), [])
            self.assertFalse(list((self.root / 'profile/LCSC3D/temp').glob('library-*')))

    def test_ir_failure_and_export_cancellation_do_not_leave_library_files(self):
        def cancel():
            raise Cancelled()
        component = parse_component(fixture(), 'C2765186')
        with self.assertRaises(Cancelled):
            build_library([component], {component.part: FORMATS}, 'Test', cancel)
        with self.assertRaises(DownloadError):
            backend.Options(self.root, FORMATS, append_mode=True)
        for name in ('../escape', 'C:\\bad', 'A:B', 'CON', 'x'*81):
            with self.subTest(name=name), self.assertRaises(DownloadError):
                backend.Options(self.root, FORMATS, kicad_library_name=name)

    def test_unknown_copper_layer_is_not_remapped_to_silkscreen(self):
        component = parse_component(fixture(), 'C2765186')
        items = component.footprint.items
        first = next(i for i in items if isinstance(i, Pad))
        component = replace(component, footprint=replace(component.footprint, items=tuple(
            replace(i, layer='future_copper') if i is first else i for i in items)))
        with self.assertRaises(DownloadError):
            footprint_definition(component, 'Test')

    def test_copper_cutout_and_milling_are_not_exported_as_solid_copper(self):
        component = parse_component(fixture('C20197'), 'C20197')
        path = 'M 0 0 L 1 0 L 1 1 L 0 1 Z'
        items = (Graphic('region', path=path, layer='top_copper', region_type='cutout'),
                 Graphic('region', path=path, layer='all_copper', region_type='npth'))
        component = replace(component, footprint=replace(component.footprint, items=component.footprint.items + items))
        node = sexpr(footprint_definition(component, 'Test'))
        zone = child(node, 'zone')
        self.assertEqual(child(child(zone, 'keepout'), 'copperpour')[1], 'not_allowed')
        self.assertEqual(child(child(zone, 'keepout'), 'tracks')[1], 'allowed')
        edges = [i for i in children(node, 'fp_line') if child(i, 'layer')[1] == 'Edge.Cuts']
        self.assertEqual(len(edges), 4)

    def test_symbol_labels_fit_native_font_and_properties_clear_the_body(self):
        component = parse_component(fixture(), 'C2765186')
        node = sexpr(symbol_definition(component, 'Test'))
        value = next(i for i in children(node, 'property') if i[1] == 'Value')
        body_top = max(i.points[0].y + i.size.y for u in component.symbol.units for i in u.items
                       if isinstance(i, Graphic) and i.kind == 'rect')
        self.assertGreater(float(child(value, 'at')[2]), float(body_top))
        for unit in children(node, 'symbol'):
            for pin in children(unit, 'pin'):
                for label in ('name', 'number'):
                    height = child(child(child(child(pin, label), 'effects'), 'font'), 'size')[1]
                    self.assertLessEqual(float(height), 1.27)

    def test_artifact_path_escape_is_rejected_before_any_write(self):
        from kicad import Artifact
        root = self.root / 'out'
        with self.assertRaises(DownloadError):
            commit_library(root, [Artifact('../escape.kicad_sym', b'bad', 'KICAD_SYMBOL', ('C1',))])
        self.assertFalse((self.root / 'escape.kicad_sym').exists())

    def test_pin_label_visibility_uses_native_controls_without_losing_identity(self):
        from eda_ir import Component, Symbol, SymbolUnit
        first = Pin(number='1', name='HIDDEN', connection=Point(D(-2), D(0)), name_visible=False, number_visible=False)
        second = Pin(number='2', name='VISIBLE', connection=Point(D(-2), D(-2)), body=Point(D(0), D(-2)))
        component = Component(part='C1', symbol=Symbol(name='Probe', units=(SymbolUnit(items=(first, second)),)))
        node = sexpr(symbol_definition(component, 'Probe'))
        pins = children(child(node, 'symbol'), 'pin')
        for kind in ('name', 'number'):
            self.assertEqual(child(child(child(child(pins[0], kind), 'effects'), 'font'), 'size')[1:], ['0', '0'])
            self.assertNotIn('hide', child(child(pins[0], kind), 'effects'))
        self.assertEqual(child(pins[0], 'name')[1], 'HIDDEN')
        hidden = replace(second, name_visible=False, number_visible=False)
        component = replace(component, symbol=replace(component.symbol, units=(SymbolUnit(items=(first, hidden)),)))
        node = sexpr(symbol_definition(component, 'Probe'))
        self.assertIn('hide', child(node, 'pin_names'))
        self.assertIn('hide', child(node, 'pin_numbers'))
