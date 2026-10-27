"""Real co-registered 20 m Sentinel pixels, relative inspection groups; no agronomic doses."""
from datetime import date,timedelta,datetime,timezone
import io,json,math
import numpy as np
from pyproj import Transformer
from shapely.geometry import shape,box,mapping
from shapely.ops import transform
from .parcels import ROOT,parcel
from .satellite import utm,token,acquisitions,post,ProviderError
from .weather import get_weather
from .water_balance import crop_coefficient

STORE=ROOT/'data/satellite/cubes'
INDICES=('NDVI','NDMI','NDRE')
SCRIPT='''//VERSION=3
function setup(){return {input:[{bands:["B08","B04","B11","B8A","B05","SCL","dataMask"]}],output:{bands:4,sampleType:"FLOAT32"}};}
function evaluatePixel(s){let a=s.B08+s.B04,b=s.B08+s.B11,c=s.B8A+s.B05;
let ok=s.dataMask===1&&(s.SCL===4||s.SCL===5)&&a>0&&b>0&&c>0;
return ok?[(s.B08-s.B04)/a,(s.B08-s.B11)/b,(s.B8A-s.B05)/c,1]:[0,0,0,0];}'''

def grid(pid):
    geo,b=utm(pid);x0,y0,x1,y1=b
    x0=math.floor(x0/20)*20;y0=math.floor(y0/20)*20
    x1=math.ceil(x1/20)*20;y1=math.ceil(y1/20)*20
    return geo,[x0,y0,x1,y1],int((x1-x0)/20),int((y1-y0)/20)

def decode(pid,arr):
    geo,b,w,h=grid(pid)
    if arr.shape!=(h,w,4):raise ProviderError('Beklenmeyen Copernicus FLOAT32 raster boyutu.')
    poly=shape(geo);inv=Transformer.from_crs(32635,4326,always_xy=True).transform
    cells=[];eligible=0
    for r in range(h):
        for c in range(w):
            x=b[0]+20*c;y=b[3]-20*r
            intersection=poly.intersection(box(x,y-20,x+20,y));area=intersection.area
            if area<200:continue  # >=50% inside, boundary mixing still possible
            eligible+=1
            v=arr[r,c]
            if v[3]<.5 or not np.isfinite(v[:3]).all() or np.any(np.abs(v[:3])>1):continue
            cells.append({'id':f'{int(x)}_{int(y)}','geometry':mapping(transform(inv,intersection)),
                'area_m2':round(area,2),'indices':{k:float(v[i]) for i,k in enumerate(INDICES)}})
    return cells,eligible

def fetch_cube(pid,day):
    file=STORE/f'{pid}_{day}.json'
    if file.exists():return json.loads(file.read_text())
    bearer=token();catalog=acquisitions(pid,day,day,bearer)
    if not catalog:raise ProviderError('Bu günde gerçek edinim yok.',404)
    geo,b,w,h=grid(pid);end=(date.fromisoformat(day)+timedelta(days=1)).isoformat()
    body={'input':{'bounds':{'bbox':b,'geometry':geo,'properties':{'crs':'http://www.opengis.net/def/crs/EPSG/0/32635'}},'data':[{'type':'sentinel-2-l2a','dataFilter':{'timeRange':{'from':day+'T00:00:00Z','to':end+'T00:00:00Z'},'mosaickingOrder':'leastCC'},'processing':{'upsampling':'NEAREST','downsampling':'NEAREST'}}]},'output':{'width':w,'height':h,'responses':[{'identifier':'default','format':{'type':'image/tiff'}}]},'evalscript':SCRIPT}
    r=post('/api/v1/process',body,bearer)
    import tifffile
    try:arr=tifffile.imread(io.BytesIO(r.content))
    except Exception as exc:raise ProviderError('Copernicus sayısal TIFF rasterı okunamadı.') from exc
    cells,eligible=decode(pid,arr)
    if not cells:raise ProviderError('SCL maskesi ve sınır filtresinden geçen hücre yok; başka tarih seçin.',404)
    data={'parcel_id':pid,'date':day,'cells':cells,'eligible_cells':eligible,'valid_cells':len(cells),'acquisitions':catalog,
        'metadata':{'label':'REAL DATA','source':'Copernicus Sentinel-2 L2A / Process + Catalog API','retrieved_at':datetime.now(timezone.utc).isoformat(),'resolution_m':20,'crs':'EPSG:32635','sample_type':'FLOAT32','grid_bounds':b,'width':w,'height':h,'mask':'dataMask; SCL 4/5; positive denominators for all three indices; >=50% cell area inside parcel','note':'20 m fixed UTM grid; nearest resampling. Boundary cells can mix neighbouring land. Scene cloud is not parcel cloud. No synthetic pixels.'}}
    STORE.mkdir(parents=True,exist_ok=True);(STORE/f'{pid}_{day}.tiff').write_bytes(r.content)
    file.write_text(json.dumps(data,allow_nan=False));return data

def inventory(pid):
    saved=[]
    for p in sorted(STORE.glob(f'{pid}_*.json')):
        d=json.loads(p.read_text());saved.append({'date':d['date'],'valid_cells':d['valid_cells'],'eligible_cells':d['eligible_cells']})
    stats=json.loads((ROOT/'data/satellite'/f'{pid}_NDVI.json').read_text())
    return {'saved':saved,'available_dates':[r['date'] for r in stats['rows']],'label':'REAL DATA'}

def classify(values):
    lo,hi=np.quantile(values,[1/3,2/3])
    # Equal values stay together; a uniform field should not invent three different zones.
    return float(lo),float(hi)

def workspace(pid,day,index='NDVI'):
    cube=fetch_cube(pid,day);cells=cube['cells'];values=[c['indices'][index] for c in cells];lo,hi=classify(values);q1,q3=np.quantile(values,[.25,.75]);iqr=q3-q1;fence_low=float(q1-1.5*iqr);fence_high=float(q3+1.5*iqr);features=[]
    for c in cells:
        v=c['indices'][index];group='lower' if v<lo else 'higher' if v>hi else 'middle'
        features.append({'type':'Feature','geometry':c['geometry'],'properties':{**{k:v for k,v in c.items() if k!='geometry'},'group':group,'outlier':'low' if v<fence_low else 'high' if v>fence_high else 'none'}})
    groups=[]
    for g,title in [('lower','Görece düşük'),('middle','Orta bant'),('higher','Görece yüksek')]:
        cs=[f['properties'] for f in features if f['properties']['group']==g]
        groups.append({'id':g,'title':title,'count':len(cs),'area_da':sum(c['area_m2'] for c in cs)/1000,'means':{k:sum(c['indices'][k]*c['area_m2'] for c in cs)/sum(c['area_m2'] for c in cs) if cs else None for k in INDICES}})
    history={c['id']:[] for c in cells}
    for p in sorted(STORE.glob(f'{pid}_*.json')):
        d=json.loads(p.read_text())
        if d['date']>day:continue
        for c in d['cells']:
            if c['id'] in history:history[c['id']].append({'date':d['date'],**c['indices']})
    group_history=[]
    for dt in sorted({r['date'] for rows in history.values() for r in rows}):
        row={'date':dt}
        for g in ['lower','middle','higher']:
            pairs=[(r[index],f['properties']['area_m2']) for f in features if f['properties']['group']==g for r in history[f['properties']['id']] if r['date']==dt]
            row[g]=sum(v*a for v,a in pairs)/sum(a for _,a in pairs) if pairs else None
            row[g+'_count']=len(pairs)
        group_history.append(row)
    weather=get_weather();recent=[w for w in weather['rows'] if (date.fromisoformat(day)-timedelta(days=6)).isoformat()<=w['date']<=day]
    kc,stage=crop_coefficient(day,'2026-05-06')
    return {'type':'FeatureCollection','features':features,'parcel_id':pid,'date':day,'index':index,'groups':groups,'thresholds':[lo,hi],'outlier_fences':[fence_low,fence_high],'outlier_count':sum(f['properties']['outlier']!='none' for f in features),
        'history':history,'group_history':group_history,'quality':{'valid':len(cells),'eligible':cube['eligible_cells'],'ratio':len(cells)/cube['eligible_cells'],'acquisitions':cube['acquisitions']},
        'weather':{'rows':recent,'metadata':weather['metadata']},'stage':{'name':stage,'kc':kc,'label':'MODEL ESTIMATE','note':'6 Mayıs varsayılan ekim ve referans evre uzunlukları; saha fenolojisi değil.'},'metadata':cube['metadata'],
        'method':{'label':'MODEL ESTIMATE','name':f'{index} eşiklerine göre üç göreli grup; sınıflar besin eksikliği veya hastalık değildir.','coverage':'Sadece geçerli hücre alanı; tüm kadastro alanı değil.','history':'Seçili tarihe kadar indirilmiş aynı grid hücreleri; enterpolasyon yapılmaz.'}}

def monitoring(day,threshold=.08):
    result=[]
    for pid in ['786','832','837']:
        series={}
        for k in INDICES:
            d=json.loads((ROOT/'data/satellite'/f'{pid}_{k}.json').read_text())
            series[k]=[r for r in d['rows'] if r['date']<=day]
        rows=[]
        dates=sorted({r['date'] for s in series.values() for r in s})
        for dt in dates:
            row={'date':dt}
            for k,s in series.items():
                obs=next((r for r in s if r['date']==dt),None)
                row[k]=obs['value'] if obs else None
            rows.append(row)
        flags=[]
        for a,b in zip(series['NDVI'],series['NDVI'][1:]):
            gap=(date.fromisoformat(b['date'])-date.fromisoformat(a['date'])).days
            if gap<=15 and b['value']-a['value'] < -threshold:
                quality=min(a['valid_pixels']/a['sample_count'],b['valid_pixels']/b['sample_count'])
                flags.append({'date':b['date'],'previous_date':a['date'],'delta':b['value']-a['value'],'quality_ratio':quality,'priority':'inspect' if quality>=.5 else 'quality_first','label':'MODEL ESTIMATE','explanation':'İki gerçek edinim arasında eşik üstü NDVI düşüşü. Bulut/karma piksel, normal gelişim, hasat ve saha koşulları birlikte incelenmeli; neden teşhisi değildir.'})
        result.append({'parcel_id':pid,'rows':rows,'flags':flags,'source':'Copernicus Sentinel-2 L2A / saved Statistical API observations','label':'REAL DATA'})
    kc,stage=crop_coefficient(day,'2026-05-06')
    return {'parcels':result,'date':day,'threshold':threshold,'stage':stage,'kc':kc,'stage_label':'MODEL ESTIMATE','note':'Düşüş eşiği kullanıcı sezgisidir, doğrulanmış anomali sınıflandırıcısı değil. Geç dönem düşüşü olgunlaşma/hasatla ilişkili olabilir.'}
