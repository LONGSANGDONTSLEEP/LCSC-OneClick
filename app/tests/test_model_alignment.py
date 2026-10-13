import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from model_binding import placement
from model_alignment import align_placement, rotated
from oneclick_export import export_bundle
from test_oneclick import FixtureApi

ROOT = Path(__file__).parent / 'fixtures'

def fixture():
    data = json.loads(gzip.decompress((ROOT / 'C225122-alignment.json.gz').read_bytes()))
    step = gzip.decompress((ROOT / 'C225122-alignment.step.gz').read_bytes())
    return data, step

class ConnectorApi(FixtureApi):
    def get_cad_data_of_component(self, part):
        return fixture()[0]
    def get_step_3d_model(self, uuid):
        return fixture()[1]

class AlignmentTests(unittest.TestCase):
    def test_c225122_real_step_matches_source_projection_after_shift(self):
        data, step = fixture()
        before = placement(data)
        after, diagnostic = align_placement(data, before, step)
        self.assertAlmostEqual(after.x, -3.1250127, places=5)
        self.assertAlmostEqual(after.y, .3144909, places=5)
        self.assertAlmostEqual(after.z, .01, places=5)
        bounds, target = diagnostic['step_rotated_bounds_mm'], diagnostic['source_projection_bounds_mm']
        for axis, shift in enumerate((after.x, after.y)):
            for end in range(2):
                self.assertAlmostEqual(bounds[axis][end] + shift, target[axis][end], delta=.0001)
        self.assertAlmostEqual(bounds[2][0] + after.z, 0, places=5)
        self.assertGreater(abs(before.x - after.x), 3)
    def test_manual_delta_is_added_to_geometry_alignment_and_exported(self):
        with tempfile.TemporaryDirectory() as folder:
            _, root, _, _ = export_bundle(['C225122'], folder, api=ConnectorApi(),
                                           corrections={'C225122': [.1, -.2, .3]})
            row = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['model_bindings'][0]
            self.assertTrue(row['ad']['embedded'])
            self.assertAlmostEqual(row['placement']['x'], -3.0250127, places=5)
            self.assertAlmostEqual(row['placement']['y'], .1144909, places=5)
            self.assertAlmostEqual(row['placement']['z'], .31, places=5)
            self.assertEqual(row['alignment']['manual_delta_mm'], [.1, -.2, .3])
    def test_rotation_and_mismatching_projection_rejected(self):
        x, y, z = rotated((1, 0, 0), (0, 0, 90))
        self.assertAlmostEqual(x, 0, places=6)
        self.assertAlmostEqual(y, 1, places=6)
        data, step = fixture()
        with patch('model_alignment.step_vertices', return_value=[(-100, -100, 0), (100, 100, 1)]):
            with self.assertRaisesRegex(ValueError, '尺寸'):
                align_placement(data, placement(data), step)
