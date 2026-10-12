import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from oneclick_export import extract_parts, read_bom, export_bundle
from backend import NetworkApi

class FixtureApi(NetworkApi):
    def get_cad_data_of_component(self, part):
        return json.loads((Path(__file__).parent / 'fixtures' / (part + '.json')).read_text(encoding='utf-8'))
    def get_step_3d_model(self, uuid):
        return None
    def get_raw_3d_model_obj(self, uuid):
        return None

class OneClickTests(unittest.TestCase):
    def test_bom_extract(self):
        self.assertEqual(extract_parts('编号,型号\nc2040,ABC2040\nhttps://x/C23922.html C2040 C23922xx'), ['C2040', 'C23922'])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bom.csv'
            p.write_bytes('编号,型号\nC2040,电阻'.encode('gb18030'))
            self.assertEqual(read_bom(p), ['C2040'])
    def test_bundle_real_converters_partial_models_and_unique_run(self):
        with tempfile.TemporaryDirectory() as d:
            result, root, archive, failed = export_bundle(['C2040'], d, api=FixtureApi())
            self.assertIn('C2040', failed)
            self.assertTrue((root / 'OneClick.SchLib').stat().st_size > 100)
            self.assertTrue((root / 'OneClick.PcbLib').stat().st_size > 100)
            self.assertTrue((root / 'OneClick.kicad_sym').exists())
            self.assertTrue(list((root / 'OneClick.pretty').glob('*.kicad_mod')))
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertIn('manifest.json', z.namelist())
            self.assertEqual(json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['requested'], ['C2040'])
            _, second, _, _ = export_bundle(['C2040'], d, api=FixtureApi())
            self.assertNotEqual(root, second)
            self.assertTrue(archive.exists())
    def test_empty_input_rejected(self):
        with self.assertRaises(ValueError):
            export_bundle([], '.')
