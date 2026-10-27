import json,unittest
from unittest.mock import patch
import numpy as np
import tifffile
from shapely.geometry import shape
from fastapi.testclient import TestClient
from backend.main import app
from backend.parcels import ROOT,parcel
from backend.zoning import workspace,inventory,decode,grid,classify,monitoring,STORE
from backend.satellite import ProviderError
from backend.outlook import outlook,OutlookQuery,response

class IntelligenceTests(unittest.TestCase):
    def test_actual_cube_archives_and_tiff_roundtrip(self):
        for pid in ['786','832','837']:
            self.assertGreaterEqual(len(inventory(pid)['saved']),4)
            for day in ['2026-07-17','2026-08-06','2026-08-26','2026-09-15']:
                saved=json.loads((STORE/f'{pid}_{day}.json').read_text())
                decoded,eligible=decode(pid,tifffile.imread(STORE/f'{pid}_{day}.tiff'))
                self.assertEqual(eligible,saved['eligible_cells']);self.assertEqual(len(decoded),saved['valid_cells'])
                self.assertEqual(decoded[0]['indices'],saved['cells'][0]['indices'])
                self.assertEqual(saved['metadata']['sample_type'],'FLOAT32')
                self.assertTrue(all(a['date']==day for a in saved['acquisitions']))
    def test_original_geometry_clipping_and_resolution(self):
        for pid in ['786','832','837']:
            poly=shape(parcel(pid)['geometry']);d=workspace(pid,'2026-08-26')
            self.assertEqual(d['metadata']['resolution_m'],20)
            for f in d['features']:
                self.assertLess(shape(f['geometry']).difference(poly.buffer(1e-8)).area,1e-12)
                self.assertGreaterEqual(f['properties']['area_m2'],200)
                self.assertLessEqual(f['properties']['area_m2'],400)
                self.assertTrue(all(-1<=v<=1 for v in f['properties']['indices'].values()))
    def test_mask_rejects_invalid_and_nonfinite_pixels(self):
        _,_,w,h=grid('786');arr=np.zeros((h,w,4),dtype=np.float32)
        self.assertEqual(decode('786',arr)[0],[])
        arr[:,:,:]=[.5,.2,.4,1]
        cells,_=decode('786',arr);self.assertEqual(len(cells),326)
        arr[:,:,0]=np.nan;self.assertEqual(decode('786',arr)[0],[])
        with self.assertRaises(Exception):decode('786',np.zeros((1,1,4)))
    def test_grouping_and_missing_quality_are_honest(self):
        d=workspace('786','2026-09-15','NDMI')
        self.assertEqual(sum(g['count'] for g in d['groups']),d['quality']['valid'])
        self.assertEqual(len(d['group_history']),4)
        self.assertEqual(sum(d['group_history'][-1][g+'_count'] for g in ['lower','middle','higher']),d['quality']['valid'])
        self.assertLess(d['quality']['ratio'],1)
        low,high=d['outlier_fences']
        for f in d['features']:
            v=f['properties']['indices']['NDMI']
            self.assertEqual(f['properties']['outlier'],'low' if v<low else 'high' if v>high else 'none')
        self.assertEqual(d['outlier_count'],sum(f['properties']['outlier']!='none' for f in d['features']))
        self.assertEqual(classify([.5]*100),(.5,.5))
        for f in d['features']:
            v=f['properties']['indices']['NDMI'];g=f['properties']['group'];lo,hi=d['thresholds']
            self.assertEqual(g,'lower' if v<lo else 'higher' if v>hi else 'middle')
    def test_same_cell_history_cutoff_and_weather_cutoff(self):
        d=workspace('832','2026-08-06')
        for rows in d['history'].values():
            self.assertEqual(len(rows),2);self.assertTrue(all(r['date']<=d['date'] for r in rows))
        self.assertEqual(len(d['weather']['rows']),7)
        self.assertTrue(all(r['date']<=d['date'] for r in d['weather']['rows']))
    def test_monitoring_threshold_and_cutoff(self):
        low=monitoring('2026-09-15',.02);high=monitoring('2026-09-15',.4)
        self.assertGreater(sum(len(p['flags']) for p in low['parcels']),sum(len(p['flags']) for p in high['parcels']))
        for p in low['parcels']:
            self.assertTrue(all(r['date']<='2026-09-15' for r in p['rows']))
            self.assertTrue(all(f['delta']<-.02 and f['previous_date']<f['date'] for f in p['flags']))
    def test_fao_response_bounds(self):
        self.assertEqual(response(450,.85,0),450)
        self.assertEqual(response(450,.85,.2),373.5)
        self.assertEqual(response(450,2,1),0)
    def test_yield_uses_season_denominator_not_window_ky(self):
        q=OutlookQuery();d=outlook(q)
        self.assertEqual(d['label'],'ILLUSTRATIVE SCENARIO')
        for p in d['parcels']:
            for r in p['alternatives']:
                fraction=(r['window_etc_mm']-r['window_eta_mm'])/r['season_etc_mm']
                self.assertAlmostEqual(fraction,r['season_deficit_fraction'])
                self.assertAlmostEqual(r['scenario_kg_da'],450*(1-.85*fraction))
                self.assertGreater(r['season_etc_mm'],r['window_etc_mm'])
                self.assertLessEqual(r['low_kg_da'],r['scenario_kg_da']);self.assertGreaterEqual(r['high_kg_da'],r['scenario_kg_da'])
    def test_yield_inputs_change_model(self):
        q=OutlookQuery();base=outlook(q)
        q.baseline_kg_da=600;higher=outlook(q)
        q.outside_window_deficit=.3;stressed=outlook(q)
        for a,b,c in zip(base['parcels'],higher['parcels'],stressed['parcels']):
            self.assertAlmostEqual(b['alternatives'][0]['scenario_kg_da']/a['alternatives'][0]['scenario_kg_da'],600/450)
            self.assertLess(c['alternatives'][0]['scenario_kg_da'],b['alternatives'][0]['scenario_kg_da'])
    def test_zero_ky_and_infeasible_optimizer(self):
        q=OutlookQuery(ky=0);q.water.minimum_fields=3;q.water.canal_volume_m3=0;q.water.transfer_enabled=False
        d=outlook(q)
        self.assertTrue(all(r['scenario_kg_da']==450 for p in d['parcels'] for r in p['alternatives']))
        self.assertTrue(all(len(p['alternatives'])==1 for p in d['parcels']))
    def test_fetch_outage_is_explicit_and_preserves_observations(self):
        c=TestClient(app);before=inventory('786')
        with patch('backend.main.fetch_cube',side_effect=ProviderError('Bağlantı kesildi',502)):
            r=c.post('/api/zones/786/fetch?day=2026-07-15')
        self.assertEqual(r.status_code,502)
        self.assertEqual(r.json()['detail'],'Bağlantı kesildi')
        self.assertEqual(inventory('786'),before)
        self.assertEqual(c.get('/api/zones/786?day=2026-08-26').status_code,200)

    def test_routes_validation_and_no_implicit_network(self):
        c=TestClient(app)
        with patch('backend.zoning.token',side_effect=AssertionError('No implicit provider call')):
            self.assertEqual(c.get('/api/zones/786?day=2026-08-26').status_code,200)
            self.assertEqual(c.get('/api/zones/786?day=2026-07-15').status_code,404)
        self.assertEqual(c.get('/api/zones/999/inventory').status_code,404)
        self.assertEqual(c.get('/api/zones/786?day=2025-01-01').status_code,422)
        self.assertEqual(c.get('/api/zones/786?day=2026-08-26&index=FAKE').status_code,422)
        self.assertEqual(c.get('/api/monitoring?day=2026-08-26&threshold=9').status_code,422)
        self.assertEqual(c.post('/api/outlook',json={'baseline_kg_da':-1}).status_code,422)
        self.assertEqual(c.post('/api/outlook',json={}).status_code,200)

if __name__=='__main__':unittest.main()
