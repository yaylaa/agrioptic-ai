"""FAO seasonal yield-response sensitivity, deliberately unvalidated, not a yield forecast."""
from pydantic import BaseModel,Field
from datetime import timedelta
from .decision import DecisionQuery,run_decision,field_areas
from .water_balance import crop_coefficient
from .weather import get_weather

class OutlookQuery(BaseModel):
    water:DecisionQuery=DecisionQuery()
    baseline_kg_da:float=Field(450,ge=50,le=1000)
    ky:float=Field(.85,ge=0,le=2)
    outside_window_deficit:float=Field(0,ge=0,le=.8)
    baseline_uncertainty:float=Field(.2,ge=0,le=.5)

def response(baseline,ky,deficit):return baseline*max(0,min(1,1-ky*deficit))

def outlook(q):
    water=run_decision(q.water);weather=get_weather()
    season=[w for w in weather['rows'] if '2026-05-06'<=w['date']<='2026-10-05']
    if len(season)!=153 or any(w['et0_mm'] is None for w in season):raise ValueError('Verim senaryosu için tam sezon ET₀ arşivi gerekli.')
    end=(q.water.decision_date+timedelta(days=q.water.horizon-1)).isoformat()
    outside_potential=sum(w['et0_mm']*crop_coefficient(w['date'],q.water.planting_date.isoformat(),**q.water.crop.model_dump())[0] for w in season if not q.water.decision_date.isoformat()<=w['date']<=end)
    results=[]
    for pid,(area,_) in field_areas().items():
        alternatives=[]
        for name,plan in [('Bekle',water['no_irrigation']),('Yalnız kanal',water['selected']['canal_only']),('Optimize seçenek',water['selected']['optimized'])]:
            rows=plan.get('rows',{}).get(pid)
            if not rows:continue
            window_etc=sum(r['etc_mm'] for r in rows);window_eta=sum(r['eta_mm'] for r in rows)
            # Outside-season ETc is measured-weather-derived potential, not a fabricated ETa observation.
            outside=outside_potential;potential=outside+window_etc
            deficit=((window_etc-window_eta)+outside*q.outside_window_deficit)/potential
            y=response(q.baseline_kg_da,q.ky,deficit)
            alternatives.append({'name':name,'window_etc_mm':window_etc,'window_eta_mm':window_eta,'season_etc_mm':potential,'season_deficit_fraction':deficit,'scenario_kg_da':y,'scenario_tonnes':y*area/1000,'low_kg_da':y*(1-q.baseline_uncertainty),'high_kg_da':y*(1+q.baseline_uncertainty),'label':'ILLUSTRATIVE SCENARIO'})
        results.append({'parcel_id':pid,'alternatives':alternatives})
    return {'label':'ILLUSTRATIVE SCENARIO','query':q.model_dump(mode='json'),'parcels':results,'water':water,
        'method':'Ya/Ym = max(0, 1 − Ky × (1 − ETa/ETm)); FAO-33 / FAO-56 Eq.90. Ky=0.85 seasonal cotton reference; not locally calibrated. Scenario ETc replaces observed-window potential in the equivalent-season denominator. Window uses modeled ET; outside-window deficit is an explicit user scenario. No seasonal Ky is directly applied to a short-window ratio.',
        'source':'https://www.fao.org/4/X0490E/x0490e0e.htm','weather_source':weather['metadata'],
        'limitations':'Kütlü pamuk baz verimi kullanıcı varsayımıdır. Aralık sadece baz verim duyarlılığıdır; güven aralığı veya doğruluk değildir. Mevsim geneli stres, çeşit, beslenme, hastalık ve hasat verimi bilinmiyor. Uydu indisleri gelişim bağlamıdır, verim dönüşüm katsayısı değil.'}
