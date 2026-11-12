"""Geometry regression checks for the original sample-name styles.

Run with: python -m unittest discover -s tests
"""
import unittest
from shapely.geometry import Polygon
from app.geometry import text_shape,connected_bubble,reinforced_tab,generate


class KeychainGeometryTests(unittest.TestCase):
    def test_sample_names_have_solid_backings(self):
        for name in ('LEAH','Joseph','AUDREY','Hugh','SEBASTIAN'):
            with self.subTest(name=name):
                raw=text_shape(name,'DejaVu Sans',17)
                base=connected_bubble(raw,4.8)
                self.assertIsInstance(base,Polygon)
                self.assertEqual(len(base.interiors),0)
                tab=reinforced_tab(base,raw,'top_left',2,2.5)
                self.assertIsInstance(tab,Polygon)
                self.assertEqual(len(tab.interiors),1)
                self.assertTrue(tab.is_valid)

    def test_optional_border_parts(self):
        for enabled,count in ((False,2),(True,3)):
            with self.subTest(border=enabled):
                shapes=generate('LEAH',{'borderEnabled':enabled,'physicalMode':'auto'})
                self.assertEqual(len(shapes),count)
                self.assertEqual(shapes[0][0],'Base')
                self.assertEqual(shapes[-1][0],'Letters')

    def test_bad_values_identify_field(self):
        for settings,key in (({'baseMargin':0.1},'baseMargin'),
                             ({'tabPadding':0.5},'tabPadding'),
                             ({'textBoldness':1.5,'baseMargin':1.5},'textBoldness')):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError,'^'+key+':'):
                    generate('LEAH',settings)


if __name__=='__main__':
    unittest.main()
