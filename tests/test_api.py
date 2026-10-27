import json,unittest
from unittest.mock import patch,Mock
from pathlib import Path
import requests
from fastapi.testclient import TestClient
from backend.main import app
from backend.weather import normalize,get_weather,import_weather
from backend.satellite import configured,stats,index_script,ProviderError
from backend.synthetic import operational_scenario

client=TestClient(app)
class APITests(unittest.TestCase):
    def test_geometry_and_metadata(self):
        data=client.get('/api/parcels').json()
        original=json.loads(Path('pilot_parcels.geojson').read_text())
        self.assertEqual(len(data['features']),3)
        for a,b in zip(data['features'],original['features']):
            self.assertEqual(a['geometry'],b['geometry']);self.assertGreater(a['properties']['area_calculated_da'],75)
        self.assertEqual(client.get('/api/health').json()['milestone'],1)
    def test_weather_snapshot(self):
        d=client.get('/api/weather').json()
        self.assertEqual(len(d['rows']),155)
        self.assertEqual(d['metadata']['label'],'MODEL ESTIMATE')
        self.assertIsNotNone(d['rows'][0]['tmax_c'])
        self.assertIn('source',d['metadata'])
    def test_weather_outage_preserves_real(self):
        with patch('backend.weather.requests.get',side_effect=requests.ConnectionError()),patch('backend.weather.time.sleep'):
            d=get_weather(True)
            self.assertEqual(len(d['rows']),155)
            self.assertIn('warning',d)
            self.assertEqual(d['metadata']['label'],'MODEL ESTIMATE')
    def test_import_rejects_malformed_and_preserves_gaps(self):
        self.assertEqual(client.post('/api/weather/import',files={'file':('bad.json',b'{}')}).status_code,422)
        d={'daily':{'time':['2026-07-01','2026-07-02'],'precipitation_sum':[1,None],'et0_fao_evapotranspiration':[4,5],'temperature_2m_max':[30,31]}}
        out=import_weather(json.dumps(d).encode(),'meteo.json')
        self.assertIsNone(out['rows'][1]['rain_mm'])
        self.assertEqual(out['metadata']['mode'],'import')
        d['daily']['precipitation_sum']=[-1,0]
        with self.assertRaises(ValueError): normalize(d)
    def test_query_bounds_and_secret_redaction(self):
        self.assertEqual(client.post('/api/satellite/stats',json={'parcel_ids':['fake']}).status_code,422)
        self.assertEqual(client.post('/api/satellite/stats',json={'start':'2026-10-07'}).status_code,422)
        d=client.get('/api/config').json()
        self.assertNotIn('COPERNICUS_CLIENT_SECRET',json.dumps(d))
        with patch('backend.satellite.config',return_value={'COPERNICUS_CLIENT_ID':'','COPERNICUS_CLIENT_SECRET':''}),patch('backend.satellite.CACHE',Path('/private/tmp/agriflow-no-satellite-test-cache')):
            d=client.post('/api/satellite/stats',json={'parcel_ids':['786']})
            self.assertEqual(d.status_code,409)
    def test_index_bands_and_mask(self):
        self.assertIn('B11',index_script('NDMI'))
        self.assertIn('B8A',index_script('NDRE'))
        self.assertIn('s.SCL===4 || s.SCL===5',index_script('NDVI'))
        self.assertIn('dataMask:[valid?1:0]',index_script('NDVI'))
    def test_statistics_uses_actual_dates_and_valid_samples(self):
        catalog=[{'date':'2026-07-01','acquired_at':'2026-07-01T09:00:00Z','cloud_percent_scene':8}]
        response={'data':[{'interval':{'from':'2026-07-01T00:00:00Z'},'outputs':{'index':{'bands':{'B0':{'stats':{'mean':.7,'min':.5,'max':.8,'stDev':.1,'sampleCount':100,'noDataCount':20}}}}}},{'interval':{'from':'2026-07-02T00:00:00Z'},'outputs':{'index':{'bands':{'B0':{'stats':{'mean':.5,'sampleCount':100,'noDataCount':0}}}}}}]}
        import tempfile
        with tempfile.TemporaryDirectory() as temp,patch('backend.satellite.CACHE',Path(temp)),patch('backend.satellite.token',return_value='hidden'),patch('backend.satellite.acquisitions',return_value=catalog),patch('backend.satellite.post',return_value=Mock(json=lambda:response)):
            d=stats('786','NDVI','2026-07-01','2026-07-02')
            self.assertEqual(len(d['rows']),1)
            self.assertEqual(d['rows'][0]['valid_pixels'],80)
            self.assertEqual(d['rows'][0]['acquired_at'],['2026-07-01T09:00:00Z'])
    def test_saved_actual_indices_and_raster(self):
        for index in ['NDVI','NDMI','NDRE']:
            d=client.get('/api/satellite/history',params={'index':index}).json()
            self.assertEqual(len(d['results']),3)
            self.assertEqual([len(r['rows']) for r in d['results']],[42,43,43])
            for series in d['results']:
                self.assertEqual(series['metadata']['label'],'REAL DATA')
                for row in series['rows']:
                    self.assertTrue(-1<=row['value']<=1)
                    self.assertTrue(row['acquired_at'])
                    self.assertGreater(row['valid_pixels'],0)
        with patch('backend.satellite.token',side_effect=AssertionError('offline')):
            r=client.get('/api/satellite/raster/786',params={'index':'NDVI','day':'2026-07-17'})
            self.assertEqual(r.status_code,200)
            self.assertTrue(client.get(r.json()['url']).content.startswith(b'\x89PNG'))

    def test_synthetic_separation(self):
        self.assertEqual(operational_scenario(),operational_scenario())
        self.assertEqual(operational_scenario()['label'],'ILLUSTRATIVE SCENARIO')
        self.assertNotIn('ILLUSTRATIVE SCENARIO',client.get('/api/weather').text)
        self.assertEqual(client.get('/api/satellite/image/../../.env').status_code,404)

if __name__=='__main__':unittest.main()
