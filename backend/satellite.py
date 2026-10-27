"""Copernicus provider. Real acquisitions, masked statistics and actual pixels only."""
from datetime import date,datetime,timedelta,timezone
from hashlib import sha256
import json, math, os, time, io
from dotenv import load_dotenv
import requests
from pyproj import Transformer
from shapely.geometry import shape,mapping
from shapely.ops import transform
from .parcels import ROOT,parcel

BASE='https://sh.dataspace.copernicus.eu'
TOKEN='https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
BANDS={'NDVI':('B08','B04'),'NDMI':('B08','B11'),'NDRE':('B8A','B05')}
CACHE=ROOT/'.cache/satellite'

class ProviderError(Exception):
    def __init__(self,message,status=502): self.message=message;self.status=status

def config():
    # Explicit process environment wins. File changes are re-read without returning values.
    from dotenv import dotenv_values
    local=dotenv_values(ROOT/'.env')
    return {k:os.environ.get(k) or local.get(k) or '' for k in ['COPERNICUS_CLIENT_ID','COPERNICUS_CLIENT_SECRET']}

def configured(): return all(config().values())

_oauth_cache={}
def token():
    c=config()
    if not all(c.values()): raise ProviderError('Copernicus bağlantısı yapılandırılmadı. Yerel .env dosyasını tamamlayın.',409)
    fingerprint=sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()
    if _oauth_cache.get('fingerprint')==fingerprint and _oauth_cache.get('until',0)>time.monotonic(): return _oauth_cache['token']
    for attempt in range(3):
        try:
            r=requests.post(TOKEN,data={'grant_type':'client_credentials','client_id':c['COPERNICUS_CLIENT_ID'],'client_secret':c['COPERNICUS_CLIENT_SECRET']},timeout=(5,25))
            if r.status_code in (400,401): raise ProviderError('Copernicus kimlik bilgileri kabul edilmedi. Client ID / Secret ve iptal durumunu kontrol edin.',401)
            r.raise_for_status();d=r.json();t=d['access_token']
            _oauth_cache.update(fingerprint=fingerprint,token=t,until=time.monotonic()+max(0,float(d.get('expires_in',300))-30))
            return t
        except (requests.RequestException,ValueError,KeyError):
            if attempt==2: raise ProviderError('Copernicus kimlik servisine erişilemiyor. Daha sonra tekrar deneyin.')
            time.sleep(.5*(attempt+1))

def post(path,body,bearer):
    for attempt in range(2):
        try:
            r=requests.post(BASE+path,json=body,headers={'Authorization':'Bearer '+bearer},timeout=(5,55))
            if r.status_code==403: raise ProviderError('Copernicus erişim izni yok. CDSE Sentinel Hub OAuth istemcisi ve hesabın API iznini kontrol edin.',403)
            if r.status_code==429: raise ProviderError('Copernicus istek kotası doldu. Hesap kotasını kontrol edip daha sonra deneyin.',429)
            if r.status_code>=500 and attempt==0: time.sleep(.4);continue
            if not r.ok: raise ProviderError(f'Copernicus isteği başarısız (HTTP {r.status_code}); mevcut gerçek veri korunuyor.')
            return r
        except requests.RequestException:
            if attempt==1: raise ProviderError('Copernicus bağlantısı kesildi veya zaman aşımı. Mevcut gerçek kayıt korunuyor.')
    raise ProviderError('Copernicus yanıt vermedi.')

def utm(pid):
    f=parcel(pid); g=transform(Transformer.from_crs(4326,32635,always_xy=True).transform,shape(f['geometry']))
    return mapping(g),g.bounds

def acquisitions(pid,start,end,bearer):
    f=parcel(pid)
    body={'collections':['sentinel-2-l2a'],'intersects':f['geometry'],'datetime':f'{start}T00:00:00Z/{end}T23:59:59Z','limit':100}
    features=[]
    for _ in range(10):
        d=post('/api/v1/catalog/1.0.0/search',body,bearer).json();features+=d.get('features',[])
        nxt=d.get('context',{}).get('next')
        if nxt is None: break
        body['next']=nxt
    result=[]
    for f in features:
        p=f.get('properties',{});dt=p.get('datetime')
        if dt: result.append({'id':f.get('id'),'acquired_at':dt,'date':dt[:10],'cloud_percent_scene':p.get('eo:cloud_cover')})
    return sorted(result,key=lambda x:x['acquired_at'])

def index_script(index,raster=False):
    a,b=BANDS[index]
    if raster:
        output='output:{bands:4,sampleType:"AUTO"}'
        ret='''if (!valid) return [0,0,0,0];
  let t=Math.max(0,Math.min(1,(v+0.2)/1.2));
  let stops=[[0.69,0.32,0.19],[0.88,0.70,0.36],[0.83,0.86,0.54],[0.36,0.65,0.42],[0.05,0.34,0.24]];
  let n=t*4, i=Math.min(3,Math.floor(n)), u=n-i;
  return [...stops[i].map((x,j)=>x*(1-u)+stops[i+1][j]*u),1];'''
    else:
        output='output:[{id:"index",bands:1,sampleType:"FLOAT32"},{id:"dataMask",bands:1}]'
        ret='return {index:[valid?v:0],dataMask:[valid?1:0]};'
    return f'''//VERSION=3
function setup(){{return {{input:[{{bands:["{a}","{b}","SCL","dataMask"]}}],{output}}};}}
function evaluatePixel(s){{let den=s.{a}+s.{b};let valid=s.dataMask===1 && den>0 && (s.SCL===4 || s.SCL===5);let v=den>0?(s.{a}-s.{b})/den:0;{ret}}}'''

def stats(pid,index,start,end,refresh=False):
    geometry,_=utm(pid)
    key=sha256(json.dumps([pid,index,start,end,geometry,'acquisition-v1'],sort_keys=True).encode()).hexdigest()
    file=CACHE/(key+'.json')
    if file.exists() and not refresh:
        d=json.loads(file.read_text());d['metadata']['mode']='cached_real';return d
    bearer=token(); catalog=acquisitions(pid,start,end,bearer)
    if not catalog:
        return {'parcel_id':pid,'index':index,'rows':[],'acquisitions':[],'metadata':metadata('api'),'warning':'Katalogda bu tarih aralığı için görüntü bulunamadı.'}
    end_exclusive=(date.fromisoformat(end)+timedelta(days=1)).isoformat()
    body={'input':{'bounds':{'geometry':geometry,'properties':{'crs':'http://www.opengis.net/def/crs/EPSG/0/32635'}},'data':[{'type':'sentinel-2-l2a','dataFilter':{'mosaickingOrder':'leastCC'}}]},'aggregation':{'timeRange':{'from':start+'T00:00:00Z','to':end_exclusive+'T00:00:00Z'},'aggregationInterval':{'of':'P1D'},'evalscript':index_script(index),'resx':20,'resy':20}}
    d=post('/statistics/v1',body,bearer).json();rows=[]; issues=[]
    for item in d.get('data',[]):
        day=item.get('interval',{}).get('from','')[:10]
        actual=[a for a in catalog if a['date']==day]
        if item.get('error'): issues.append(day);continue
        s=item.get('outputs',{}).get('index',{}).get('bands',{}).get('B0',{}).get('stats',{})
        n=s.get('sampleCount',0)-s.get('noDataCount',0); mean=s.get('mean')
        if actual and n>0 and mean is not None and math.isfinite(mean):
            rows.append({'date':day,'value':mean,'min':s.get('min'),'max':s.get('max'),'stdev':s.get('stDev'),'valid_pixels':n,'sample_count':s.get('sampleCount'),'acquired_at':[a['acquired_at'] for a in actual],'cloud_percent_scene':min((a['cloud_percent_scene'] for a in actual if a['cloud_percent_scene'] is not None),default=None)})
    result={'parcel_id':pid,'index':index,'rows':rows,'acquisitions':catalog,'metadata':metadata('api'),'failed_intervals':issues}
    CACHE.mkdir(parents=True,exist_ok=True);file.write_text(json.dumps(result,allow_nan=False));return result

def metadata(mode):
    return {'label':'REAL DATA','source':'Copernicus Sentinel-2 L2A / Statistical + Catalog API','retrieved_at':datetime.now(timezone.utc).isoformat(),'mode':mode,'resolution_m':20,'crs':'EPSG:32635','mask':'SCL 4 (vegetation), 5 (bare soil), dataMask and positive denominator','temporal':'Daily least-cloud mosaic; actual acquisitions listed; scene cloud percentage is not field cloud percentage.'}

def raster(pid,index,day):
    archived=ROOT/'data/satellite/rasters'/f'{pid}_{index}_{day}.json'
    if archived.exists(): return json.loads(archived.read_text())
    bearer=token(); catalog=acquisitions(pid,day,day,bearer)
    if not catalog: raise ProviderError('Seçili günde gerçek Sentinel-2 görüntüsü yok.',404)
    geometry,b=utm(pid);x0,y0,x1,y1=b
    width=math.ceil((x1-x0)/20);height=math.ceil((y1-y0)/20)
    x1=x0+width*20;y1=y0+height*20
    end=(date.fromisoformat(day)+timedelta(days=1)).isoformat()
    body={'input':{'bounds':{'bbox':[x0,y0,x1,y1],'geometry':geometry,'properties':{'crs':'http://www.opengis.net/def/crs/EPSG/0/32635'}},'data':[{'type':'sentinel-2-l2a','dataFilter':{'timeRange':{'from':day+'T00:00:00Z','to':end+'T00:00:00Z'},'mosaickingOrder':'leastCC'}}]},'output':{'width':width,'height':height,'responses':[{'identifier':'default','format':{'type':'image/png'}}]},'evalscript':index_script(index,True)}
    r=post('/api/v1/process',body,bearer)
    if not r.content.startswith(b'\x89PNG'): raise ProviderError('Copernicus geçerli PNG raster döndürmedi.')
    from PIL import Image
    image=Image.open(io.BytesIO(r.content)).convert('RGBA')
    if not image.getchannel('A').getbbox(): raise ProviderError('Görüntüde maskeden geçen gerçek piksel yok. Başka bir edinim günü seçin.',404)
    key=sha256(json.dumps([pid,index,day,geometry]).encode()).hexdigest();CACHE.mkdir(parents=True,exist_ok=True)
    (CACHE/(key+'.png')).write_bytes(r.content)
    inv=Transformer.from_crs(32635,4326,always_xy=True);lon0,lat0=inv.transform(x0,y0);lon1,lat1=inv.transform(x1,y1)
    return {'url':'/api/satellite/image/'+key,'bounds':[[lat0,lon0],[lat1,lon1]],'acquisitions':catalog,'metadata':{**metadata('api'),'source':'Copernicus Sentinel-2 L2A / Process API','index':index,'date':day,'legend_min':-.2,'legend_max':1,'note':'Actual masked pixels; PNG colors are a visualization, not a nitrogen or disease map.'}}
