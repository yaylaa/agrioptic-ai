import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,Mock
from tempfile import TemporaryDirectory
from pathlib import Path
import requests
from backend import forecast as f

class ForecastTests(unittest.TestCase):
    def payload(self):
        return {'daily':{'time':['2026-10-21','2026-10-22'],**{k:[1,1] for k in f.DAILY}},'current':{'time':'2026-10-21T12:00','temperature_2m':20}}
    def record(self,now):
        r=f.normalize_forecast(self.payload());r['metadata']={'retrieved_at':now.isoformat(),'source':'test fixture'};return r
    def test_missing_values_remain_missing(self):
        p=self.payload();p['daily']['precipitation_sum'][1]=None
        self.assertIsNone(f.normalize_forecast(p)['rows'][1]['rain_mm'])
    def test_invalid_arrays_and_probability(self):
        p=self.payload();p['daily']['wind_speed_10m_max']=[]
        with self.assertRaises(ValueError):f.normalize_forecast(p)
        p=self.payload();p['daily']['precipitation_probability_max'][0]=101
        with self.assertRaises(ValueError):f.normalize_forecast(p)
    def test_stale_has_no_advice(self):
        now=datetime(2026,10,21,15,tzinfo=timezone.utc)
        r=f.stamp(self.record(now-timedelta(hours=7)),now)
        self.assertTrue(r['stale']);self.assertEqual(f.advice(r)['days'],[])
    def test_date_mismatch_is_stale(self):
        now=datetime(2026,10,24,15,tzinfo=timezone.utc)
        self.assertTrue(f.stamp(self.record(now),now)['stale'])
    def test_thresholds_change_candidate_days(self):
        now=datetime(2026,10,21,15,tzinfo=timezone.utc);r=f.stamp(self.record(now),now)
        self.assertEqual(len(f.advice(r,2,20)['candidate_dates']),2)
        self.assertEqual(len(f.advice(r,0,20)['candidate_dates']),0)
        r['rows'][0]['wind_kmh']=None
        self.assertEqual(f.advice(r)['days'][0]['status'],'unknown')
    def test_provider_failure_never_creates_weather(self):
        with patch.object(f,'cached',return_value=None),patch.object(f.requests,'get',side_effect=requests.ConnectionError),patch.object(f.time,'sleep'):
            with self.assertRaises(f.ProviderError):f.get_forecast(True)
    def test_failure_keeps_old_record_and_no_stale_advice(self):
        now=datetime(2026,10,21,15,tzinfo=timezone.utc);old=self.record(now-timedelta(hours=8))
        with patch.object(f,'now_local',return_value=now),patch.object(f,'cached',return_value=old),patch.object(f.requests,'get',side_effect=requests.Timeout),patch.object(f.time,'sleep'):
            result=f.get_forecast(True)
            self.assertEqual(result['rows'],old['rows']);self.assertTrue(result['stale']);self.assertIn('yenilenemedi',result['warning']);self.assertEqual(f.advice(result)['days'],[])
    def test_fresh_fetch_is_accepted_after_request_time(self):
        before=datetime(2026,10,21,15,tzinfo=timezone.utc);after=before+timedelta(seconds=2)
        response=Mock();response.json.return_value=self.payload()
        with TemporaryDirectory() as d,patch.object(f,'CACHE',Path(d)/'forecast.json'),patch.object(f,'cached',return_value=None),patch.object(f,'now_local',side_effect=[before,after]),patch.object(f,'datetime') as dt,patch.object(f.requests,'get',return_value=response):
            dt.now.return_value=before+timedelta(seconds=1);dt.fromisoformat=datetime.fromisoformat
            result=f.get_forecast(True)
            self.assertFalse(result['stale']);self.assertEqual(len(result['rows']),2);self.assertEqual(response.json.call_count,1)
    def test_recent_satellite_uses_actual_current_window(self):
        now=datetime(2026,10,21,15,tzinfo=timezone.utc)
        with TemporaryDirectory() as d,patch.object(f,'SAT_CACHE',Path(d)/'sat.json'),patch.object(f,'now_local',return_value=now),patch.object(f,'stats',return_value={'rows':[]}) as stats:
            f.recent_satellite(True)
            self.assertEqual(stats.call_count,3)
            self.assertEqual(stats.call_args.args[2:4],('2026-10-01','2026-10-21'))
    def test_routes_validate_and_return_explanations(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client=TestClient(app);now=datetime(2026,10,21,15,tzinfo=timezone.utc)
        with patch('backend.main.get_forecast',return_value=f.stamp(self.record(now),now)):
            self.assertEqual(client.get('/api/weather/forecast?rain_limit=-1').status_code,422)
            self.assertEqual(client.get('/api/weather/forecast?wind_limit=nan').status_code,422)
            result=client.get('/api/weather/forecast?rain_limit=0').json()
            self.assertEqual(result['advice']['candidate_dates'],[])
            self.assertEqual(result['advice']['label'],'MODEL ESTIMATE')
