"""Source independence, historical schema compatibility and domain isolation."""
import copy
from dataclasses import replace
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eda_ir import Component, Extension, Pin, Pad, Point, Symbol, SymbolUnit, Footprint, D, loads, dumps, require_domain
from easyeda_import import parse_component
from errors import DownloadError, Cancelled
from kicad import build_library, FORMATS
from altium import export_schlib, export_pcblib
from altium_inspect import schematic, pcb
from app_logging import close_logging

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(part='C2765186'):
    return json.loads((FIXTURES / (part + '.json')).read_text('utf-8'))


class IRTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, LOCALAPPDATA=folder.name)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(close_logging)

    def test_real_samples_survive_wire_roundtrip_and_both_ad_targets(self):
        cases = json.loads(gzip.decompress((FIXTURES / 'official_ad_cases.json.gz').read_bytes()))['cases']
        for case in cases:
            with self.subTest(part=case['part']):
                first = parse_component(case['data'], case['part'])
                second = loads(dumps(first))
                self.assertEqual(first, second)
                self.assertFalse(second.diagnostics)
                old_pins = schematic(export_schlib(case['data'], case['part']))[2]
                new_pins = schematic(export_schlib(second, case['part']))[2]
                self.assertEqual(old_pins, new_pins)
                self.assertEqual(pcb(export_pcblib(case['data'], case['part']))[1],
                                 pcb(export_pcblib(second, case['part']))[1])

    def test_historical_1_0_document_supplies_optional_defaults(self):
        component = loads((FIXTURES / 'ir_v1_0.json').read_text('utf-8'))
        self.assertEqual(component.schema_version, '1.0')
        self.assertFalse(component.has_footprint)
        self.assertEqual(component.symbol.reference, 'U?')
        self.assertEqual(component.symbol.units[0].items[0].number, 'A1')
        self.assertEqual(loads(dumps(component)), component)

    def test_pin_electrical_semantics_and_unknown_source_values_survive_roundtrip(self):
        for source, expected in (('', 'unspecified'), ('0', 'unspecified'), ('1', 'input'),
                ('2', 'output'), ('3', 'bidirectional'), ('4', 'power'), ('future', 'source_type_future')):
            data = fixture('C20197')
            shapes = data['dataStr']['shape']
            index = next(i for i, shape in enumerate(shapes) if shape.startswith('P~'))
            parts = shapes[index].split('~')
            parts[2] = source
            shapes[index] = '~'.join(parts)
            value = loads(dumps(parse_component(data, 'C20197')))
            pin = next(i for u in value.symbol.units for i in u.items if isinstance(i, Pin))
            self.assertEqual(pin.electrical, expected)
            self.assertTrue(any(json.loads(e.payload).get('electrical') == source for e in pin.extensions))
        wire = json.loads(dumps(value))
        item = next(i for i in wire['symbol']['units'][0]['items'] if i['@type'] == 'Pin')
        item.pop('electrical')
        legacy = loads(json.dumps(wire))
        self.assertEqual(next(i for i in legacy.symbol.units[0].items if isinstance(i, Pin)).electrical, 'unspecified')

    def test_future_optional_fields_and_namespaced_extensions_are_retained(self):
        wire = json.loads(dumps(parse_component(fixture(), 'C2765186')))
        wire['schema_version'] = '1.7'
        wire['future_note'] = {'anything': [1, 2, '值']}
        wire['symbol']['future_symbol_attribute'] = {'@type': 'NotYetKnown'}
        value = loads(json.dumps(wire))
        rewritten = json.loads(dumps(value))
        self.assertEqual(rewritten['future_note'], wire['future_note'])
        self.assertEqual(rewritten['symbol']['future_symbol_attribute'], wire['symbol']['future_symbol_attribute'])
        value = replace(value, extensions=(Extension('example.optional', '2', '{"x":1}'),))
        self.assertEqual(loads(dumps(value)).extensions, value.extensions)
        self.assertIsNotNone(require_domain(value, 'symbol'))

    def test_new_major_and_required_features_never_silently_downgrade(self):
        component = parse_component(fixture(), 'C2765186')
        for modified in (replace(component, schema_version='2.0'),
                         replace(component, required_features=('local-mm-y-up', 'blind-vias'))):
            with self.assertRaises(DownloadError):
                require_domain(modified, 'symbol')
        unit = component.symbol.units[0]
        item = replace(unit.items[0], extensions=(Extension('example.critical', '1', required=True),))
        component = replace(component, symbol=replace(component.symbol,
                            units=(replace(unit, items=(item,) + unit.items[1:]),)))
        with self.assertRaisesRegex(DownloadError, '必要扩展'):
            require_domain(component, 'symbol')

    def test_malformed_wire_types_nonfinite_and_unknown_objects_fail(self):
        payloads = ['[]', '{"@type":"Component","part":3}', '{"@type":"NewComponent"}',
                    '{"@type":"Point","x":{"@decimal":"NaN"}}',
                    '{"@type":"Component","schema_version":"1.bad"}']
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(DownloadError):
                loads(payload)

    def test_bad_symbol_does_not_block_footprint_and_vice_versa(self):
        data = fixture()
        data['dataStr']['shape'] = []
        component = parse_component(data, 'C2765186')
        self.assertIsNotNone(require_domain(component, 'footprint'))
        with self.assertRaises(DownloadError):
            require_domain(component, 'symbol')
        artifacts, errors = build_library([component], {component.part: FORMATS}, 'Test')
        self.assertEqual({a.format for a in artifacts}, {'KICAD_FOOTPRINT'})
        self.assertIn((component.part, 'KICAD_SYMBOL'), errors)

    def test_domain_values_keep_pin_identity_copper_and_holes_separate(self):
        component = parse_component(fixture(), 'C2765186')
        pins = [i for u in component.symbol.units for i in u.items if isinstance(i, Pin)]
        pads = [i for i in component.footprint.items if isinstance(i, Pad)]
        self.assertEqual(len(pins), 14)
        self.assertEqual(len(pads), 18)
        self.assertEqual(pins[0].number, 'A1B12')
        self.assertEqual(pins[0].body.x, D('-6.350'))
        self.assertTrue(any(p.copper_center != p.center for p in pads))
        self.assertEqual(sum(p.mechanical_hole for p in pads), 2)
        self.assertEqual(sum(bool(p.slot_length) for p in pads), 4)

    def test_ir_is_not_mutated_by_styles_or_exporter_order(self):
        component = parse_component(fixture('C20197'), 'C20197')
        before = dumps(component)
        first = build_library([component], {component.part: FORMATS}, 'Test')
        export_schlib(component, component.part, symbol_style='ad')
        export_schlib(component, component.part, symbol_style='jlc')
        export_pcblib(component, component.part)
        self.assertEqual(before, dumps(component))
        self.assertEqual(first, build_library([component], {component.part: FORMATS}, 'Test'))

    def test_unknown_source_primitive_is_preserved_and_rejected_by_target(self):
        data = fixture()
        data['dataStr']['shape'].append('FUTURE~unknown_geometry~not_safe_to_drop')
        component = parse_component(data, 'C2765186')
        unknown = component.symbol.units[0].items[-1]
        self.assertIn('unknown_geometry', unknown.extensions[0].payload)
        self.assertEqual(loads(dumps(component)), component)
        with self.assertRaises(DownloadError):
            export_schlib(component, component.part)
        artifacts, errors = build_library([component], {component.part: FORMATS}, 'Test')
        self.assertIn((component.part, 'KICAD_SYMBOL'), errors)
        self.assertTrue(any(a.format == 'KICAD_FOOTPRINT' for a in artifacts))

    def test_parser_propagates_cancellation(self):
        def cancel():
            raise Cancelled()
        with self.assertRaises(Cancelled):
            parse_component(fixture(), 'C2765186', cancel)

    def test_unmodelled_source_fields_are_preserved_without_becoming_a_target_dependency(self):
        component = parse_component(fixture('C20197'), 'C20197')
        first = component.symbol.units[0].items[0]
        source = next(e for e in first.extensions if e.namespace.endswith('.source-record'))
        self.assertIn('record', json.loads(source.payload))
        # The target still works after provenance is stripped from all items.
        stripped = replace(component, symbol=replace(component.symbol, units=tuple(
            replace(u, items=tuple(replace(i, extensions=()) for i in u.items)) for u in component.symbol.units)),
            footprint=replace(component.footprint, items=tuple(replace(i, extensions=()) for i in component.footprint.items)))
        self.assertEqual(build_library([component], {component.part: FORMATS}, 'Test'),
                         build_library([stripped], {component.part: FORMATS}, 'Test'))

    def test_manually_created_ir_needs_no_easyeda_metadata_for_linked_ad_or_kicad(self):
        component = Component(part='C1', title='Generic',
                              symbol=Symbol(name='Generic', units=(SymbolUnit(items=(
                                  Pin(number='A1', connection=Point(D('-2.54'), D(0))),)),)),
                              footprint=Footprint(name='GenericFootprint', items=(
                                  Pad(number='A1', size=Point(D(1), D(2))),)))
        _, records, _ = schematic(export_schlib(component, 'C1'))
        footprint = pcb(export_pcblib(component, 'C1'))[0]
        self.assertEqual(next(r['MODELNAME'] for r in records if r.get('RECORD') == '45'), footprint)
        artifacts, errors = build_library([component], {component.part: FORMATS}, 'GenericLib')
        self.assertFalse(errors)
        self.assertEqual(len(artifacts), 2)

    def test_limited_future_target_cannot_implicitly_accept_complex_core_features(self):
        from eda_targets import KICAD
        limited = replace(KICAD, name='FutureSimpleEDA', features=frozenset({'local-mm-y-up'}))
        with self.assertRaisesRegex(DownloadError, 'symbol-units'):
            limited.require(parse_component(fixture('C23922'), 'C23922'), 'symbol')
        with self.assertRaisesRegex(DownloadError, 'pad-copper-drill'):
            limited.require(parse_component(fixture(), 'C2765186'), 'footprint')
