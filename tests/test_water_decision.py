import copy,unittest
from datetime import date,timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app
from backend.decision import DecisionQuery,planning_weather,optimize,verify_plan,run_decision,cost_breakdown
from backend.water_balance import simulate,crop_coefficient
from backend.weather import snapshot

SOIL={'awc_mm_m':150,'root_depth_m':1.2,'depletion_fraction':.65,'rain_infiltration':.8,'efficiency':.6}
def weather(n=14,et=6,rain=0):return [{'date':(date(2026,7,15)+timedelta(days=i)).isoformat(),'et0_mm':et,'rain_mm':rain} for i in range(n)]
class WaterBalanceTests(unittest.TestCase):
    def test_fao_taw_raw_ks(self):
        r=simulate(weather(1),SOIL,.9)[0]
        self.assertAlmostEqual(r['taw_mm'],180)
        self.assertAlmostEqual(r['raw_mm'],180*min(.8,max(.1,.65+.04*(5-r['etc_mm']))))
        self.assertLess(r['ks'],1);self.assertLess(r['eta_mm'],r['etc_mm'])
        self.assertAlmostEqual(r['eta_mm'],r['ks']*r['etc_mm'])
    def test_mass_balance_both_root_and_field(self):
        rows=simulate(weather(rain=4),SOIL,.6,{0:80,4:100})
        for r in rows:
            self.assertLess(abs(r['balance_residual_mm']),1e-9)
            self.assertTrue(0<=r['dr_mm']<=r['taw_mm'])
            delta=r['dr_start_mm']-r['dr_mm']
            self.assertAlmostEqual(r['rain_mm']+r['gross_irrigation_mm'],delta+r['runoff_mm']+r['application_loss_mm']+r['deep_percolation_mm']+r['eta_mm'])
    def test_rain_overflow_irrigation_and_dry_bounds(self):
        wet=simulate(weather(1,et=0,rain=500),SOIL,.1)[0]
        self.assertEqual(wet['dr_mm'],0);self.assertGreater(wet['deep_percolation_mm'],0)
        self.assertEqual(simulate(weather(1,et=0),SOIL,.5,{0:30})[0]['dr_mm'],72)
        dry=simulate(weather(14,et=20),SOIL,1)
        self.assertTrue(all(r['ks']==0 and r['eta_mm']==0 and r['dr_mm']==180 for r in dry))
    def test_stages_and_units(self):
        self.assertAlmostEqual(crop_coefficient('2026-05-06','2026-05-06')[0],.35)
        self.assertAlmostEqual(crop_coefficient('2026-08-01','2026-05-06')[0],1.15)
        q=DecisionQuery();p=optimize(q,weather(),0)
        area={f['properties']['id']:f['properties']['area_calculated_da'] for f in __import__('backend.parcels',fromlist=['parcels']).parcels()['features']}
        for e in p['events']:
            if e['method']!='wait':self.assertAlmostEqual(e['volume_m3'],area[e['parcel_id']]*q.gross_depth_mm)
    def test_invalid_meteorology(self):
        with self.assertRaises(ValueError):simulate([{'date':'2026-07-15','et0_mm':None,'rain_mm':0}],SOIL,.5)

class DecisionTests(unittest.TestCase):
    def test_delay_changes_water_state(self):
        q=DecisionQuery(transfer_enabled=False);w=weather()
        early=optimize(q,w,0,allow_transfer=False);late=optimize(q,w,8,allow_transfer=False)
        self.assertGreater(late['loss_mm'],early['loss_mm'])
    def test_all_constraints_and_multi_day_events(self):
        q=DecisionQuery(fixed_order=True,minimum_fields=3,canal_volume_m3=30000,canal_window_days=6,canal_flow_m3h=500,transfer_flow_m3h=500)
        p=optimize(q,weather(),5)
        self.assertIn(p['status'],['optimal','feasible_time_limit'])
        self.assertEqual(verify_plan(q,p['events'],5,1),[])
        self.assertTrue(any(e['reserved_hours']>q.daily_hours for e in p['events']))
    def test_reduced_capacity_changes_feasible_set(self):
        q=DecisionQuery(transfer_enabled=False,canal_volume_m3=0)
        p=optimize(q,weather(),0)
        self.assertTrue(all(e['method']=='wait' for e in p['events']))
        q.minimum_fields=3
        self.assertEqual(optimize(q,weather(),0)['status'],'infeasible')
    def test_flow_window_exclusion(self):
        q=DecisionQuery(canal_flow_m3h=1,transfer_enabled=False,canal_window_days=1)
        p=optimize(q,weather(),0)
        self.assertTrue(all(e['method']=='wait' for e in p['events']))
        self.assertTrue(all(p['reasons'].values()))
    def test_cost_can_change_choice(self):
        q=DecisionQuery();w=weather()
        cheap=optimize(q,w,8)
        q.economics.diesel_try_l=500;q.economics.cost_weight=5
        costly=optimize(q,w,8)
        self.assertNotEqual([(e['parcel_id'],e['method']) for e in cheap['events']],[(e['parcel_id'],e['method']) for e in costly['events']])
    def test_fixed_fee_not_marginal_and_transfer_liters(self):
        q=DecisionQuery();p=optimize(q,weather(),5)
        self.assertGreater(p['fixed_seasonal_try'],250000)
        self.assertEqual(p['fixed_seasonal_try'],p['sunk_try'])
        self.assertTrue(all(e['breakdown'].get('method_fee_try',0)==0 for e in p['events']))
        c=cost_breakdown(q,'786','transfer',10000,'seasonal')
        self.assertAlmostEqual(c['diesel_l'],127.76*2)
        self.assertEqual(c['diesel_try'],c['diesel_l']*55)
        self.assertGreater(cost_breakdown(q,'786','transfer',10000,'event')['method_fee_try'],0)
    def test_initial_conditions_change_output(self):
        q=DecisionQuery();p=optimize(q,weather(),5)
        for f in q.fields:f.initial_depletion=.1
        wet=optimize(q,weather(),5)
        self.assertLess(wet['loss_mm'],p['loss_mm'])
    def test_no_future_weather_or_satellite_leakage(self):
        q=DecisionQuery();observed=snapshot()['rows'];a,_=planning_weather(q,observed)
        changed=copy.deepcopy(observed)
        for r in changed:
            if r['date']>=q.decision_date.isoformat():r['et0_mm']=0;r['rain_mm']=80
        b,_=planning_weather(q,changed)
        self.assertEqual(a,b)
        with patch('backend.decision.get_weather',return_value={'rows':changed,'metadata':{'source':'test actual'}}):d=run_decision(q)
        self.assertFalse(d['provenance']['future_weather_used_in_optimization'])
        for f in d['satellite_context']:
            for v in f['indices'].values():
                if v:self.assertLess(v['date'],q.decision_date.isoformat())
    def test_deterministic_optimization(self):
        q=DecisionQuery();a=optimize(q,weather(),5);b=optimize(q,weather(),5)
        self.assertEqual(a['events'],b['events']);self.assertAlmostEqual(a['objective'],b['objective'])
    def test_api_validates_and_runs(self):
        c=TestClient(app)
        self.assertEqual(c.get('/api/water/defaults').json()['label'],'ILLUSTRATIVE SCENARIO')
        self.assertEqual(c.post('/api/water/decision',json={'decision_date':'2026-10-21'}).status_code,422)
        self.assertEqual(c.post('/api/water/decision',json={'soil':{'efficiency':0}}).status_code,422)
        r=c.post('/api/water/decision',json={})
        self.assertEqual(r.status_code,200)
        d=r.json();self.assertIn('MODEL ESTIMATE',r.text);self.assertIn('ILLUSTRATIVE SCENARIO',r.text)
        self.assertGreater(len(d['scenarios']),3);self.assertTrue(d['selected']['optimized']['checks_passed'])

if __name__=='__main__':unittest.main()
