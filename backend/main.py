from datetime import date
import re,json
from typing import Literal
from fastapi import FastAPI,HTTPException,UploadFile,File
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,model_validator
from .parcels import ROOT,parcels,parcel
from .weather import get_weather,import_weather
from .satellite import configured,stats,raster,CACHE,ProviderError

app=FastAPI(title='AgriOpticAI Field Intelligence',version='1.0.0')

class SatelliteQuery(BaseModel):
    parcel_ids:list[str]=Field(default=['786','832','837'],min_length=1,max_length=3)
    index:Literal['NDVI','NDMI','NDRE']='NDVI'
    start:date=date(2026,5,5)
    end:date=date(2026,10,6)
    refresh:bool=False
    @model_validator(mode='after')
    def valid_window(self):
        if not date(2026,5,5)<=self.start<=self.end<=date(2026,10,6): raise ValueError('2026 pilot sezonu aralığında tarih seçin.')
        if len(set(self.parcel_ids))!=len(self.parcel_ids) or any(parcel(p) is None for p in self.parcel_ids): raise ValueError('Geçersiz parsel seçimi.')
        return self

@app.exception_handler(ProviderError)
def provider_error(request,exc): return JSONResponse({'detail':exc.message},status_code=exc.status)

@app.get('/api/health')
def health(): return {'status':'ok','milestone':1}

@app.get('/api/config')
def configuration():
    return {'copernicus_configured':configured(),'season':2026,'season_status':'harvest_complete','credential_location':'.env (server only)','synthetic_observations':False}

@app.get('/api/parcels')
def fields(): return parcels()

@app.get('/api/weather')
def weather(refresh:bool=False): return get_weather(refresh)

@app.post('/api/weather/import')
async def upload_weather(file:UploadFile=File(...)):
    data=await file.read(5_000_001)
    try: return import_weather(data,file.filename or '')
    except (ValueError,KeyError,UnicodeDecodeError) as e: raise HTTPException(422,'Geçersiz Open-Meteo dosyası. time, precipitation_sum, et0_fao_evapotranspiration, temperature_2m_max sütunlarını kontrol edin.') from e

@app.get('/api/satellite/history')
def saved_satellite(index:Literal['NDVI','NDMI','NDRE']='NDVI'):
    results=[]
    for pid in ['786','832','837']:
        f=ROOT/'data/satellite'/f'{pid}_{index}.json'
        if f.exists():
            d=json.loads(f.read_text());d['metadata']['mode']='saved_real_snapshot';results.append(d)
    return {'results':results,'errors':[]}

@app.post('/api/satellite/stats')
def satellite(query:SatelliteQuery):
    results=[];errors=[]
    for pid in query.parcel_ids:
        try: results.append(stats(pid,query.index,query.start.isoformat(),query.end.isoformat(),query.refresh))
        except ProviderError as e: errors.append({'parcel_id':pid,'message':e.message,'status':e.status})
    if not results and errors: raise ProviderError(errors[0]['message'],errors[0]['status'])
    return {'results':results,'errors':errors}

@app.get('/api/satellite/raster/{pid}')
def pixels(pid:str,index:Literal['NDVI','NDMI','NDRE'],day:date):
    if parcel(pid) is None: raise HTTPException(404,'Parsel yok.')
    if not date(2026,5,5)<=day<=date(2026,10,6): raise HTTPException(422,'Pilot sezonu dışında tarih.')
    return raster(pid,index,day.isoformat())

@app.get('/api/satellite/image/{key}')
def image(key:str):
    if not re.fullmatch('[a-f0-9]{64}',key): raise HTTPException(404)
    file=CACHE/(key+'.png')
    if not file.is_file(): file=ROOT/'data/satellite/rasters'/(key+'.png')
    if not file.is_file(): raise HTTPException(404)
    return FileResponse(file,media_type='image/png',headers={'Cache-Control':'private, max-age=14400'})

from .decision import DecisionQuery,run_decision

@app.get('/api/water/defaults')
def water_defaults():
    return {'label':'ILLUSTRATIVE SCENARIO','query':DecisionQuery().model_dump(mode='json'),'note':'Geometri ve meteoroloji gerçek kaynaklardan; işletme, toprak ve maliyet girdileri ölçülmemiş varsayımlar.'}

@app.post('/api/water/decision')
def water_decision(query:DecisionQuery):
    try:return run_decision(query)
    except ValueError as e:raise HTTPException(422,str(e)) from e

from .zoning import inventory,workspace,monitoring,fetch_cube
from .outlook import OutlookQuery,outlook

@app.get('/api/zones/{pid}/inventory')
def zone_inventory(pid:str):
    if parcel(pid) is None:raise HTTPException(404,'Parsel yok.')
    return inventory(pid)

@app.get('/api/zones/{pid}')
def zones(pid:str,day:date,index:Literal['NDVI','NDMI','NDRE']='NDVI'):
    if parcel(pid) is None:raise HTTPException(404,'Parsel yok.')
    if not date(2026,5,5)<=day<=date(2026,10,6):raise HTTPException(422,'Pilot sezonu dışında tarih.')
    # GET is offline-only. Explicit POST downloads an additional real acquisition.
    if day.isoformat() not in [r['date'] for r in inventory(pid)['saved']]:raise HTTPException(404,'Bu gün için sayısal raster henüz indirilmedi. Gerçek veriyi getir seçeneğini kullanın.')
    return workspace(pid,day.isoformat(),index)

@app.post('/api/zones/{pid}/fetch')
def zone_fetch(pid:str,day:date):
    if parcel(pid) is None:raise HTTPException(404,'Parsel yok.')
    if not date(2026,5,5)<=day<=date(2026,10,6):raise HTTPException(422,'Pilot sezonu dışında tarih.')
    d=fetch_cube(pid,day.isoformat())
    return {'date':d['date'],'valid_cells':d['valid_cells'],'label':'REAL DATA'}

@app.get('/api/monitoring')
def crop_monitoring(day:date,threshold:float=.08):
    if not date(2026,5,5)<=day<=date(2026,10,6) or not .02<=threshold<=.4:raise HTTPException(422,'Geçersiz sezon tarihi veya düşüş eşiği.')
    return monitoring(day.isoformat(),threshold)

@app.post('/api/outlook')
def yield_outlook(query:OutlookQuery):
    try:return outlook(query)
    except ValueError as e:raise HTTPException(422,str(e)) from e

from .forecast import get_forecast,advice,recent_satellite

@app.get('/api/weather/forecast')
def live_forecast(refresh:bool=False,rain_limit:float=2,wind_limit:float=20):
    if not 0<=rain_limit<=20 or not 5<=wind_limit<=60:raise HTTPException(422,'Geçersiz saha ziyareti eşiği.')
    d=get_forecast(refresh)
    return {**d,'advice':advice(d,rain_limit,wind_limit)}

@app.get('/api/satellite/recent')
def live_satellite(refresh:bool=False):return recent_satellite(refresh)

DIST=ROOT/'frontend/dist'
if (DIST/'assets').exists(): app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')

@app.get('/{path:path}',include_in_schema=False)
def frontend(path:str):
    if path.startswith('api/'): raise HTTPException(404,'API route not found')
    if (DIST/'index.html').exists(): return FileResponse(DIST/'index.html')
    return JSONResponse({'message':'Frontend build not present. Run pnpm --dir frontend build or start Vite.'},status_code=503)
