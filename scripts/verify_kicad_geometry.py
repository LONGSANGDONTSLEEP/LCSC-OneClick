"""Run with KiCad's bundled Python, not the application's runtime."""
import json
import math
from pathlib import Path
import sys
import pcbnew

manifest = json.loads(Path(sys.argv[1]).read_text('utf-8'))
count, drilled, maximum = 0, 0, 0.0
for case in manifest:
    footprint = pcbnew.FootprintLoad(case['library'], case['name'])
    assert footprint is not None, case['name']
    pads = list(footprint.Pads())
    assert len(pads) == len(case['pads']), case['name']
    for actual, expected in zip(pads, case['pads']):
        context = case['name'] + ':' + expected['number']
        assert actual.GetNumber() == expected['number'], context
        box = actual.GetBoundingBox()
        bounds = [box.GetX()/1e6, box.GetY()/1e6, box.GetRight()/1e6, box.GetBottom()/1e6]
        error = max(abs(a-b) for a,b in zip(bounds, expected['bbox']))
        maximum = max(maximum, error)
        # 1.27 um flattening and outline recognition, plus 1 nm serialization.
        assert error < 0.003, (context, bounds, expected['bbox'], error)
        if expected['drill']:
            drilled += 1
            pos = actual.GetPosition()
            assert abs(pos.x/1e6-expected['position'][0]) < 0.0000011, context
            assert abs(pos.y/1e6-expected['position'][1]) < 0.0000011, context
            drill = actual.GetDrillSize()
            assert abs(drill.y/1e6-expected['drill']) < 0.0000011, context
            assert abs(drill.x/1e6-(expected['slot'] or expected['drill'])) < 0.0000011, context
            expected_type = pcbnew.PAD_ATTRIB_PTH if expected['plated'] else pcbnew.PAD_ATTRIB_NPTH
            assert actual.GetAttribute() == expected_type, context
            if expected['slot']:
                error_angle = (actual.GetOrientationDegrees()-expected['drill_rotation']+90) % 180-90
                assert abs(error_angle) < 0.0000011, context
        else:
            assert actual.GetDrillSizeX() == actual.GetDrillSizeY() == 0, context
        if expected['layer'] == 'top_copper':
            assert actual.IsOnLayer(pcbnew.F_Cu) and not actual.IsOnLayer(pcbnew.B_Cu), context
        elif expected['layer'] == 'bottom_copper':
            assert actual.IsOnLayer(pcbnew.B_Cu) and not actual.IsOnLayer(pcbnew.F_Cu), context
        count += 1
report = dict(success=True, footprints=len(manifest), pads=count, drilled_pads=drilled,
              max_copper_bounds_error_mm=maximum)
Path(sys.argv[2]).write_text(json.dumps(report, indent=2), 'utf-8')
print(json.dumps(report))
