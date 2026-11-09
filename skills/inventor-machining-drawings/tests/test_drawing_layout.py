import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from drawing_layout import plan_views, scale_label, plain_formatted_text, readable_dimension


class LayoutTests(unittest.TestCase):
    def test_plate_keeps_one_to_one_with_iso(self):
        scale, p = plan_views({'front':(10,10), 'top':(10,.5), 'iso':(14.2,8.6)}, 1, (2,8,40,23.7))
        self.assertEqual(scale, 1)
        self.assertEqual(p['front'][0], p['top'][0])
        self.assertGreater(p['top'][1], p['front'][1])
        self.assertGreater(p['iso'][0], p['front'][0])

    def test_large_part_uses_reduction_and_keeps_all_axes(self):
        scale, p = plan_views({n:s for n,s in [('front',(100,70)),('top',(100,30)),('bottom',(100,30)),('left',(30,70)),('right',(30,70))]}, 1, (2,8,40,23.7))
        self.assertEqual(scale, .05)
        self.assertEqual(p['top'][0],p['front'][0])
        self.assertEqual(p['bottom'][0],p['front'][0])
        self.assertEqual(p['left'][1],p['front'][1])
        self.assertGreater(p['right'][0],p['front'][0])
        self.assertLess(p['bottom'][1],p['front'][1])

    def test_label_and_nonstandard_limit(self):
        self.assertEqual(scale_label(.5),'1:2')
        self.assertEqual(scale_label(2),'2:1')
        self.assertEqual(plan_views({'front':(1,1)},.6195,(0,0,10,10))[0],.5)
        with self.assertRaises(ValueError):plan_views({'front':(1,1)},0,(0,0,10,10))

    def test_readable_text_preserves_breaks_and_symbol_sources(self):
        self.assertEqual(plain_formatted_text('一行<Br/>二行<StyleOverride Font="MS Gothic">・三行</StyleOverride>'),'一行\n二行・三行')
        self.assertEqual(readable_dimension('n30 貫通','<StyleOverride Font="AIGDT">n</StyleOverride>'),'φ30 貫通')
        self.assertEqual(readable_dimension('n30','<StyleOverride Font="MS Gothic">n30</StyleOverride>'),'n30')
        self.assertEqual(readable_dimension('n30 貫通','<DimensionValue/> 貫通',True),'φ30 貫通')
        self.assertEqual(readable_dimension('2×n30.00 ±0.1 貫通','2×<DimensionValue/> 貫通',True),'2×φ30.00 ±0.1 貫通')


if __name__=='__main__': unittest.main()
