import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
import olefile
from oneclick_export import export_bundle
from model_binding import placement, finite, read_streams, embed_ad, Placement
from test_oneclick import FixtureApi

STEP = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n'

class ModelApi(FixtureApi):
    def get_step_3d_model(self, uuid):
        return STEP

def block(data, offset=0):
    size = struct.unpack_from('<I', data, offset)[0]
    return data[offset+4:offset+4+(size & 0xffffff)], offset+4+(size & 0xffffff)

def params(data):
    return dict(field.split('=', 1) for field in data.rstrip(b'\0').decode('ascii').split('|') if '=' in field)

def body_parameters(data):
    _, pos = block(data)  # footprint name
    found = []
    while pos < len(data):
        kind = data[pos]
        pos += 1
        payload, pos = block(data, pos)
        if kind == 12:
            values, _ = block(payload, 18)
            found.append(params(values))
        elif kind == 2:
            for _ in range(5):
                _, pos = block(data, pos)
        elif kind == 5:
            _, pos = block(data, pos)
        elif kind not in (1, 3, 4, 11):
            raise AssertionError(f'Unknown native primitive {kind}')
    return found

class ModelBindingTests(unittest.TestCase):
    def test_embedded_step_guid_placement_and_kicad_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            results, root, archive, failed = export_bundle(['C2040', 'C20197'], directory, api=ModelApi())
            streams = read_streams(root / 'OneClick.PcbLib')
            self.assertEqual(struct.unpack('<I', streams['Library/Models/Header'])[0], 1)
            self.assertEqual(zlib.decompress(streams['Library/Models/0']), STEP)
            metadata, _ = block(streams['Library/Models/Data'])
            model = params(metadata)
            self.assertEqual(model['EMBED'], 'TRUE')
            self.assertTrue(model['ID'].startswith('{'))
            sections = [key for key in streams if key.endswith('/Data') and not key.startswith('Library/')]
            self.assertEqual(len(sections), 2)
            for key in sections:
                bodies = body_parameters(streams[key])
                self.assertEqual(len(bodies), 1)
                self.assertEqual(bodies[0]['MODELID'], model['ID'])
                self.assertEqual(bodies[0]['MODEL.EMBED'], 'TRUE')
                self.assertEqual(bodies[0]['MODEL.MODELTYPE'], '1')
                self.assertEqual(bodies[0]['MODEL.2D.X'], '0.00000000mil')
            for footprint in (root / 'OneClick.pretty').glob('*.kicad_mod'):
                text = footprint.read_text(encoding='utf-8')
                self.assertIn('(model "${KIPRJMOD}/OneClick_3D/', text)
                self.assertIn('(scale (xyz 1 1 1))', text)
            report = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(all(r['ad']['embedded'] for r in report['model_bindings']))
    def test_source_units_origin_y_flip_rotation_and_height(self):
        data = ModelApi().get_cad_data_of_component('C2040')
        package = data['packageDetail']
        source = package['dataStr']
        if isinstance(source, str):
            source = json.loads(source)
            package['dataStr'] = source
        for index, value in enumerate(source['shape']):
            if value.startswith('SVGNODE~'):
                node = json.loads(value.partition('~')[2])
                node['attrs']['c_origin'] = f"{float(source['head']['x'])+10},{float(source['head']['y'])+20}"
                node['attrs']['z'] = '5'
                node['attrs']['c_rotation'] = '90,180,270'
                source['shape'][index] = 'SVGNODE~' + json.dumps(node)
        p = placement(data)
        self.assertAlmostEqual(p.x, 2.54)
        self.assertAlmostEqual(p.y, -5.08)
        self.assertAlmostEqual(p.z, 1.27)
        self.assertEqual((p.rx, p.ry, p.rz), (90, 180, 270))
        for invalid in (math.nan, math.inf, 100001):
            with self.assertRaises(ValueError):
                finite(invalid)
    def test_missing_model_report_and_no_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            _, root, _, failed = export_bundle(['C2040'], directory, api=FixtureApi())
            streams = read_streams(root / 'OneClick.PcbLib')
            self.assertEqual(struct.unpack('<I', streams['Library/Models/Header'])[0], 0)
            report = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
            self.assertIsNone(report['model_bindings'][0]['ad'])
            self.assertTrue(report['model_bindings'][0]['errors'])
            self.assertEqual(failed, ['C2040'])
