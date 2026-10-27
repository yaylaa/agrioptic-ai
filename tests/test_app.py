import unittest
from unittest.mock import patch
from pathlib import Path
import json
import pandas as pd
from streamlit.testing.v1 import AppTest
from water_model import balance, schedule

class PilotTests(unittest.TestCase):
    def test_event_constraints(self):
        fields=[{'name':'A','area':100},{'name':'B','area':100}]
        p=schedule(fields,3,1,1000,8,60,12000,False,800)
        self.assertEqual(p[0][1],3)
        self.assertIsNone(p[1][1])
        self.assertTrue(all(x[3]<=6000 for x in p))
        self.assertIsNone(schedule(fields,0,3,100,8,60,12000,False,800)[0][1])
    def test_delay_and_balance(self):
        weather=pd.DataFrame({'Tarih':pd.date_range('2026-07-15',periods=14),'ET₀ (mm)':[6.]*14,'Yağış (mm)':[0.]*14})
        a=balance(weather,'2026-07-15',10,100,.6,60,[0])
        b=balance(weather,'2026-07-15',10,100,.6,60,[8])
        self.assertGreaterEqual(b['Karşılanmayan ETc (mm)'].sum(),a['Karşılanmayan ETc (mm)'].sum())
        self.assertTrue(a['Kök bölgesi tamponu (mm)'].between(0,100).all())
        with self.assertRaises(ValueError): balance(weather.head(3),'2026-07-15',10,100,.6,60,[])
    def test_all_pages(self):
        at=AppTest.from_file('app.py').run(timeout=30)
        self.assertFalse(at.exception)
        for page in at.sidebar.radio[0].options:
            at.sidebar.radio[0].set_value(page).run(timeout=30)
            self.assertFalse(at.exception,page)
        at.sidebar.radio[0].set_value('Su Karar Laboratuvarı').run()
        at.slider[0].set_value(8).run()
        self.assertFalse(at.exception)
    def test_baseline_runs_offline(self):
        with patch('requests.get',side_effect=ConnectionError('offline')):
            # RuntimeError avoids retry sleep and tests baseline independent sections.
            at=AppTest.from_file('baseline_app.py').run(timeout=30)
            self.assertFalse(at.exception)
    def test_illustrative_offline_mode(self):
        at=AppTest.from_file('app.py').run(timeout=30)
        at.sidebar.toggle[0].set_value(True).run()
        self.assertFalse(at.exception)
        at.sidebar.radio[0].set_value('Su Karar Laboratuvarı').run()
        self.assertFalse(at.exception)

    def test_geometry_preserved(self):
        import subprocess
        original=subprocess.check_output(['git','show','HEAD:pilot_parcels.geojson'])
        self.assertEqual(original,Path('pilot_parcels.geojson').read_bytes())

if __name__=='__main__': unittest.main()
