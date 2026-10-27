"""Provider forecast and transparent field-visit screening; never synthetic weather fallback."""
from datetime import datetime,timedelta,timezone,date
from zoneinfo import ZoneInfo
import json,math,time
import requests
from .parcels import ROOT,center
from .satellite import ProviderError,stats

CACHE=ROOT/'.cache/forecast_latest.json'
SAT_CACHE=ROOT/'.cache/current_satellite.json'
TZ=ZoneInfo('Europe/Istanbul')
DAILY={'temperature_2m_max':'tmax_c','temperature_2m_min':'tmin_c','precipitation_sum':'rain_mm','precipitation_probability_max':'rain_probability','et0_fao_evapotranspiration':'et0_mm','wind_speed_10m_max':'wind_kmh'}

def now_local():return datetime.now(TZ)

def finite(v):return float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) else None

def normalize_forecast(payload):
    d=payload.get('daily',{});dates=d.get('time')
    if not isinstance(dates,list) or not dates:raise ValueError('Boş tahmin dönemi.')
    if any(not isinstance(d.get(k),list) or len(d[k])!=len(dates) for k in DAILY):raise ValueError('Tutarsız tahmin sütunları.')
    rows=[]
    for i,day in enumerate(dates):
        date.fromisoformat(day);r={'date':day,**{dest:finite(d[src][i]) for src,dest in DAILY.items()}}
        if any(r[k] is not None and r[k]<0 for k in ['rain_mm','et0_mm','wind_kmh']):raise ValueError('Geçersiz tahmin değeri.')
        if r['rain_probability'] is not None and not 0<=r['rain_probability']<=100:raise ValueError('Geçersiz yağış olasılığı.')
        rows.append(r)
    if len(set(dates))!=len(dates):raise ValueError('Tekrarlayan tahmin tarihi.')
    if not any(r['tmax_c'] is not None and r['rain_mm'] is not None for r in rows):raise ValueError('Kullanılabilir hava tahmini yok.')
    current=payload.get('current',{})
    return {'rows':sorted(rows,key=lambda r:r['date']),'current':{'time':current.get('time'),'temperature_c':finite(current.get('temperature_2m')),'wind_kmh':finite(current.get('wind_speed_10m')),'rain_mm':finite(current.get('precipitation'))}}

def cached():
    for f in [CACHE,ROOT/'data/forecast_snapshot.json']:
        if f.exists():
            try:return json.loads(f.read_text())
            except (ValueError,OSError):pass
    return None

def stamp(d,now,warning=None):
    # Provider periods must contain today. Retrieval time is not the model's issuance time.
    today=now.date().isoformat();age=(now-datetime.fromisoformat(d['metadata']['retrieved_at'])).total_seconds()/3600
    stale=age>6 or age<0 or today not in [r['date'] for r in d['rows']]
    return {**d,'today':today,'stale':stale,'age_hours':max(0,round(age,1)),'warning':warning or ('Bu kayıt güncel tahmin sayılmaz. Yenileyin; tarihsel tahmin olarak gösteriliyor.' if stale else None)}

def get_forecast(refresh=False):
    now=now_local();old=cached()
    if old and not refresh and not stamp(old,now)['stale']:return stamp(old,now)
    lat,lon=center();params={'latitude':round(lat,5),'longitude':round(lon,5),'timezone':'Europe/Istanbul','forecast_days':7,'daily':','.join(DAILY),'current':'temperature_2m,precipitation,wind_speed_10m'}
    for attempt in range(3):
        try:
            r=requests.get('https://api.open-meteo.com/v1/forecast',params=params,timeout=(5,18));r.raise_for_status();payload=r.json()
            d={**normalize_forecast(payload),'metadata':{'label':'MODEL ESTIMATE','source':'Open-Meteo Forecast API / best_match','retrieved_at':datetime.now(timezone.utc).isoformat(),'timezone':'Europe/Istanbul','latitude':payload.get('latitude'),'longitude':payload.get('longitude'),'elevation':payload.get('elevation'),'note':'Tahmin ve güncel durum meteorolojik modeldir, tarla istasyonu değil. Alınma zamanı modelin yayımlanma zamanı değildir. Model/gride göre çözünürlük değişir.'}}
            status=stamp(d,now_local())
            if status['stale']:raise ValueError('Sağlayıcı bugünü kapsamayan dönem döndürdü.')
            CACHE.parent.mkdir(exist_ok=True);CACHE.write_text(json.dumps(d,allow_nan=False));return status
        except (requests.RequestException,ValueError,KeyError):
            if attempt<2:time.sleep(.3*(attempt+1))
    if old:
        d=stamp(old,now,'Tahmin yenilenemedi. Son başarılı gerçek sağlayıcı kaydı gösteriliyor; yeni veri üretilmedi.')
        # Refresh failure alone does not invalidate a still-fresh <6h record; age is explicit.
        return d
    raise ProviderError('Hava tahminine şu an ulaşılamıyor. Yeniden deneyin; örnek hava verisi eklenmedi.',503)

def advice(forecast,rain_limit=2,wind_limit=20):
    if forecast['stale']:return {'days':[],'headline':'Öneri için güncel hava tahminini yenileyin.','reason':'Eski tahmine göre saha günü önermiyoruz.','label':'MODEL ESTIMATE'}
    days=[]
    for r in forecast['rows']:
        if r['date']<forecast['today']:continue
        complete=all(r[k] is not None for k in ['rain_mm','rain_probability','wind_kmh','tmin_c'])
        if not complete:status='unknown';reason='Gerekli yağış, olasılık, rüzgâr veya sıcaklık bilgisi eksik.'
        elif r['tmin_c']<=0:status='caution';reason=f"En düşük sıcaklık {r['tmin_c']:.1f} °C; don olasılığı için yerel koşulları kontrol edin."
        elif r['rain_mm']>rain_limit or r['rain_probability']>=60:status='caution';reason=f"Yağış {r['rain_mm']:.1f} mm, olasılık %{r['rain_probability']:.0f}; saha ziyaretini yeniden değerlendirin."
        elif r['wind_kmh']>wind_limit:status='caution';reason=f"Rüzgâr {r['wind_kmh']:.0f} km/sa; seçtiğiniz {wind_limit:g} km/sa eşiğinin üzerinde."
        else:status='candidate';reason=f"Yağış {r['rain_mm']:.1f} mm, olasılık %{r['rain_probability']:.0f}; rüzgâr {r['wind_kmh']:.0f} km/sa. Seçtiğiniz hava eşiklerinin içinde."
        days.append({**r,'status':status,'reason':reason})
    candidates=[r['date'] for r in days if r['status']=='candidate']
    return {'days':days,'headline':('Saha ziyareti için hava açısından aday günler var.' if candidates else 'Bu hafta saha ziyaretini hava koşullarına göre esnek planlayın.'),'reason':'Pamuk hasadı tamamlandı. Öneri; gözlem, örnekleme hazırlığı ve drenaj kontrolü için hava taramasıdır. Toprağın taşıma gücü, yol durumu ve numuneye uygunluğu yerinde doğrulanmalı. İlaçlama veya sulama talimatı değildir.','candidate_dates':candidates,'thresholds':{'rain_mm':rain_limit,'wind_kmh':wind_limit,'rain_probability':60},'label':'MODEL ESTIMATE'}

def recent_satellite(refresh=False):
    now=now_local();today=now.date();old=None
    if SAT_CACHE.exists():
        try:old=json.loads(SAT_CACHE.read_text())
        except ValueError:pass
    if old and not refresh:
        old['stale']=(now-datetime.fromisoformat(old['checked_at'])).total_seconds()>86400
        return old
    if not refresh:return {'results':[],'errors':[],'checked_at':None,'stale':False,'note':'Güncel uydu için son 21 günü sorgulayın. Sezon arşivi bu sorgudan ayrıdır.'}
    results=[];errors=[]
    for pid in ['786','832','837']:
        try:results.append(stats(pid,'NDVI',(today-timedelta(days=20)).isoformat(),today.isoformat(),True))
        except ProviderError as e:errors.append({'parcel_id':pid,'message':e.message})
    if not results:
        if old:return {**old,'stale':True,'errors':errors,'warning':'Güncel sorgu başarısız; önceki gerçek gözlemler gösteriliyor.'}
        raise ProviderError(errors[0]['message'] if errors else 'Güncel uydu verisi yok.')
    d={'results':results,'errors':errors,'checked_at':now.isoformat(),'stale':False,'note':'Son 21 günün gerçek Sentinel-2 gözlemleri. Hasat sonrası düşük bitki örtüsü tek başına stres veya hastalık değildir.'}
    SAT_CACHE.parent.mkdir(exist_ok=True);SAT_CACHE.write_text(json.dumps(d,allow_nan=False));return d
