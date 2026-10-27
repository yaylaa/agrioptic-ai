"""Real reanalysis / historical forecast only. Never implicit synthetic fallback."""
from datetime import datetime, timezone
import io, json, time
import pandas as pd
import requests
from .parcels import ROOT, center

ARCHIVE='https://archive-api.open-meteo.com/v1/archive'
FORECAST='https://historical-forecast-api.open-meteo.com/v1/forecast'
COLUMNS={'precipitation_sum':'rain_mm','et0_fao_evapotranspiration':'et0_mm','temperature_2m_max':'tmax_c'}

def normalize(d):
    daily=d.get('daily',{})
    keys=['time',*COLUMNS]
    if any(not isinstance(daily.get(k),list) for k in keys): raise ValueError('Gerekli günlük meteoroloji sütunları eksik.')
    if not daily['time'] or len({len(daily[k]) for k in keys})!=1: raise ValueError('Boş veya tutarsız günlük seri.')
    frame=pd.DataFrame({k:daily[k] for k in keys}).rename(columns={'time':'date',**COLUMNS})
    frame['date']=pd.to_datetime(frame['date'],errors='raise').dt.strftime('%Y-%m-%d')
    for c in COLUMNS.values(): frame[c]=pd.to_numeric(frame[c],errors='coerce').replace([float('inf'),-float('inf')],float('nan'))
    if frame[['rain_mm','et0_mm']].isna().all().all(): raise ValueError('Kullanılabilir yağış veya ET₀ yok.')
    if (frame[['rain_mm','et0_mm']]<0).any().any(): raise ValueError('Yağış ve ET₀ negatif olamaz.')
    return json.loads(frame.sort_values('date').drop_duplicates('date').to_json(orient='records'))

def snapshot():
    frame=pd.read_csv(ROOT/'data/weather_2026.csv').rename(columns={'Tarih':'date','Yağış (mm)':'rain_mm','ET₀ (mm)':'et0_mm','Tmax (°C)':'tmax_c'})
    meta=json.loads((ROOT/'data/weather_2026_source.json').read_text())
    return {'rows':json.loads(frame.to_json(orient='records')),'metadata':{**meta,'mode':'saved_snapshot','spatial_note':'Ortak parsel merkezi; meteorolojik model grididir, tarla sensörü değildir. Ürün/grid çözünürlüğü bu kayıtla doğrulanmamıştır.','timezone':'Europe/Istanbul'}}

LATEST=ROOT/'.cache/weather_latest.json'

def get_weather(refresh=False):
    if not refresh:
        if LATEST.exists():
            try:
                cached=json.loads(LATEST.read_text());cached['metadata']['mode']='cached_real';return cached
            except (ValueError,OSError): pass
        return snapshot()
    lat,lon=center(); errors=[]
    params={'latitude':round(lat,5),'longitude':round(lon,5),'start_date':'2026-05-05','end_date':'2026-10-06','timezone':'Europe/Istanbul','daily':','.join(COLUMNS)}
    for url,name in [(ARCHIVE,'Open-Meteo Historical Weather (reanalysis)'),(FORECAST,'Open-Meteo Historical Forecast (arşivlenmiş tahmin)')]:
        for attempt in range(2):
            try:
                r=requests.get(url,params=params,timeout=(5,18));r.raise_for_status()
                data={'rows':normalize(r.json()),'metadata':{'label':'MODEL ESTIMATE','source':name,'mode':'api','retrieved_at':datetime.now(timezone.utc).isoformat(),'latitude':lat,'longitude':lon,'timezone':'Europe/Istanbul','spatial_note':'Ortak merkezde meteorolojik model; tarla ölçümü değil. Grid çözünürlüğü seçilen kaynak modele bağlıdır.'}}
                LATEST.parent.mkdir(exist_ok=True); LATEST.write_text(json.dumps(data,allow_nan=False));return data
            except (requests.RequestException,ValueError) as exc:
                errors.append(type(exc).__name__)
                if isinstance(exc,requests.HTTPError) and exc.response.status_code in [400,401,403,404]: break
                if attempt==0: time.sleep(.3)
    old=get_weather(False)
    return {**old,'warning':'Canlı arşiv yenilenemedi. Önceki gerçek kayıt gösteriliyor; sentetik veri eklenmedi.','refresh_errors':errors}

def import_weather(content,filename):
    if len(content)>5_000_000: raise ValueError('Dosya 5 MB sınırını aşıyor.')
    text=content.decode('utf-8-sig')
    if filename.lower().endswith('.json'): data=json.loads(text)
    elif filename.lower().endswith('.csv'):
        lines=text.splitlines(); i=next((i for i,line in enumerate(lines) if line.lstrip().lower().startswith('time,')),None)
        if i is None: raise ValueError('Open-Meteo CSV başlığı bulunamadı: time,precipitation_sum,…')
        frame=pd.read_csv(io.StringIO('\n'.join(lines[i:]))); data={'daily':{k:frame[k].where(frame[k].notna(),None).tolist() for k in ['time',*COLUMNS]}}
    else: raise ValueError('Yalnızca Open-Meteo JSON / CSV desteklenir.')
    return {'rows':normalize(data),'metadata':{'label':'MODEL ESTIMATE','source':'Kullanıcı yüklemesi · Open-Meteo biçimi; kaynağı kullanıcı doğrulamalı','mode':'import','retrieved_at':datetime.now(timezone.utc).isoformat(),'spatial_note':'Dosyanın koordinatları ve kaynak modeli kullanıcı tarafından teyit edilmeli.','timezone':'Dosyadaki günlük tarihler'}}
