from __future__ import annotations
import json, os
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import streamlit as st
import folium
from shapely.geometry import shape
from streamlit_folium import st_folium
from integrations import historical_weather, uploaded_weather, from_kml, get_token, satellite_stats, GEOD
from water_model import balance, schedule

st.set_page_config(page_title='AgriOpticAI • Karar Merkezi',page_icon='🌿',layout='wide')
st.markdown('''<style>
.block-container{padding-top:2.2rem;max-width:1500px}h1{font-size:2.3rem!important;letter-spacing:-1.2px}h2{letter-spacing:-.7px}h3{font-size:1.12rem!important}section[data-testid="stSidebar"]{border-right:1px solid #e1e8df}div[data-testid="stMetric"]{background:white;border:1px solid #e1e8df;padding:20px;border-radius:14px}div[data-testid="stMetricValue"]{font-size:1.9rem} .eyebrow{font-size:12px;letter-spacing:2px;color:#167c65;font-weight:700}.hero{background:#163c35;color:#fff;padding:26px 30px;border-radius:16px;margin:18px 0 25px}.hero p{color:#c4dcd0}.badge{display:inline-block;font-size:10px;font-weight:700;letter-spacing:1px;border:1px solid #c8d9ce;border-radius:5px;padding:4px 8px;color:#227057;margin-right:6px} .muted{color:#74827d;font-size:13px}
</style>''',unsafe_allow_html=True)
BASE=Path(__file__).parent
features=json.loads((BASE/'pilot_parcels.geojson').read_text())['features']
def badge(kind, text=''):
    st.markdown(f'<span class="badge">{kind}</span> <span class="muted">{text}</span>',unsafe_allow_html=True)
def download(label,data,name):
    st.download_button(label,json.dumps(data,ensure_ascii=False,indent=2,default=str),name,'application/json')
with st.sidebar:
    st.markdown('## 🌿 AgriOpticAI',unsafe_allow_html=True)
    st.caption('TARIMSAL KARAR MERKEZİ')
    st.divider()
    page=st.radio('Çalışma alanı',['Genel Bakış','Uydu Analizi','Su Karar Laboratuvarı','Besin & Keşif Zonları','Bitki Riskleri','Verim Görünümü','Pilot Veri & Varsayımlar'],label_visibility='collapsed')
    st.divider()
    st.markdown('**Söke · Güllübahçe**')
    st.caption('Pamuk / 2026 retrospektif\n\nHasat bildirimi: 5–6 Ekim 2026')
    offline=st.toggle('Örnek hava senaryosuna geç',value=False,help='Örnek hava gerçek meteoroloji değildir. Gerçek arşiv veya dosya kullanmak için kapatın.')
    extra=st.file_uploader('Ek parsel · KML',type=['kml'])
    if extra:
        try: features.append(from_kml(extra))
        except Exception as e: st.error(str(e))
fields=[]
for f in features:
    g=shape(f['geometry']); area=abs(GEOD.geometry_area_perimeter(g)[0])/1000
    fields.append({'name':f['properties']['name'],'area':area,'geometry':f['geometry'],'props':f['properties']})
selected=st.selectbox('Pilot parseli', [f['name'] for f in fields])
f=next(x for x in fields if x['name']==selected)
st.markdown('<div class="eyebrow">SÖKE PAMUK PİLOTU / 2026 RETROSPEKTİF</div>',unsafe_allow_html=True)
st.title(page)
st.caption('21 Ekim 2026 · Tamamlanmış sezon analizi. Bu ekran bugün için sulama talimatı üretmez.')
weather=None; weather_source=''
with st.sidebar.expander('Meteoroloji kaynağı'):
    upload=st.file_uploader('Open-Meteo JSON / CSV',type=['json','csv'])
    load=st.button('Gerçek arşivi getir / yenile')
if upload:
    try: weather,weather_source=uploaded_weather(upload)
    except Exception as e: st.error(str(e))
elif load:
    try:
        with st.spinner('Open-Meteo arşivi sorgulanıyor…'):
            total_area=sum(p['area'] for p in fields)
            lat=sum(shape(p['geometry']).centroid.y*p['area'] for p in fields)/total_area
            lon=sum(shape(p['geometry']).centroid.x*p['area'] for p in fields)/total_area
            weather,weather_source=historical_weather(lat,lon)
            st.session_state['weather']=(weather,weather_source)
    except Exception: st.warning('Arşiv bağlantısı kurulamadı. Dosya yükleyin veya çevrimdışı senaryoyu açın.')
elif not offline and 'weather' in st.session_state: weather,weather_source=st.session_state['weather']
if weather is None and not offline and not upload and (BASE/'data/weather_2026.csv').exists():
    weather=pd.read_csv(BASE/'data/weather_2026.csv',parse_dates=['Tarih'])
    metadata=json.loads((BASE/'data/weather_2026_source.json').read_text())
    weather_source=metadata['source']+' · kayıtlı gerçek API yanıtı · '+metadata['retrieved_at'][:10]
if weather is None and offline:
    days=pd.date_range('2026-05-05','2026-10-06'); x=np.arange(len(days))
    weather=pd.DataFrame({'Tarih':days,'ET₀ (mm)':4.8+1.4*np.sin(x/len(x)*np.pi),'Yağış (mm)':np.where(x%19==0,5.,0.),'Tmax (°C)':29+6*np.sin(x/len(x)*np.pi)})
    weather_source='ILLUSTRATIVE SCENARIO · deterministik örnek hava; arşiv gözlemi değil'
weather_label='ILLUSTRATIVE SCENARIO' if 'ILLUSTRATIVE' in weather_source else 'MODEL ESTIMATE'
def map_fields(zones=False):
    center=shape(f['geometry']).centroid
    m=folium.Map(location=[center.y,center.x],zoom_start=15,tiles=None,control_scale=True)
    folium.TileLayer('OpenStreetMap',name='OpenStreetMap · çevrimiçi').add_to(m)
    colors=['#167c65','#4386b5','#c99645']
    for i,p in enumerate(fields):
        folium.GeoJson({'type':'Feature','properties':{'Parsel':p['name'],'Alan':f"{p['area']:.2f} da",'Kaynak':'REAL DATA · TKGM KML'},'geometry':p['geometry']},style_function=lambda _,c=colors[i%3],s=p['name']==selected:{'color':c,'weight':4 if s else 2,'fillOpacity':.3 if s else .1},tooltip=folium.GeoJsonTooltip(fields=['Parsel','Alan','Kaynak'])).add_to(m)
    bounds=[shape(p['geometry']).bounds for p in fields]
    m.fit_bounds([[min(b[1] for b in bounds),min(b[0] for b in bounds)],[max(b[3] for b in bounds),max(b[2] for b in bounds)]])
    result=st_folium(m,height=440,use_container_width=True,key='map_'+page,returned_objects=['last_object_clicked'])
    with st.expander('Ağsız gerçek geometri önizlemesi · REAL DATA'):
        import math, html
        xmin=min(b[0] for b in bounds); xmax=max(b[2] for b in bounds)
        ymin=min(b[1] for b in bounds); ymax=max(b[3] for b in bounds)
        coslat=math.cos(math.radians((ymin+ymax)/2))
        scale=min(560/((xmax-xmin)*coslat),300/(ymax-ymin))
        svg=[]
        for i,p in enumerate(fields):
            paths=[]
            for ring in p['geometry']['coordinates']:
                points=[f"{20+(x-xmin)*coslat*scale:.2f},{320-(y-ymin)*scale:.2f}" for x,y,*_ in ring]
                paths.append('M'+' L'.join(points)+' Z')
            svg.append(f'<path d="{" ".join(paths)}" fill="{colors[i%3]}" fill-opacity="0.3" stroke="{colors[i%3]}" stroke-width="2" fill-rule="evenodd"><title>{html.escape(p["name"])}</title></path>')
            center=shape(p['geometry']).representative_point()
            svg.append(f'<text x="{20+(center.x-xmin)*coslat*scale:.2f}" y="{320-(center.y-ymin)*scale:.2f}" font-size="11" text-anchor="middle" fill="#172d36">{html.escape(p["name"].split()[-1])}</text>')
        st.image('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="350" viewBox="0 0 600 350" role="img" aria-label="REAL DATA TKGM parsel geometrileri" style="width:100%;background:#edf1e9;border-radius:12px">'+''.join(svg)+'</svg>',width='stretch')
        st.caption('REAL DATA · Orijinal WGS84 sınırları, yerel enlem ölçeği ile şematik görünüm. Altlık veya uydu görüntüsü değil.')
    if result.get('last_object_clicked'):
        from shapely.geometry import Point
        pt=result['last_object_clicked']; point=Point(pt['lng'],pt['lat'])
        for p in fields:
            if shape(p['geometry']).covers(point): st.info(f"Seçilen alan: {p['name']} · {p['area']:.2f} da · REAL DATA")

if page=='Genel Bakış':
    cols=st.columns(4)
    for c,label,value in zip(cols,['Gerçek pilot parseli','Bildirilen kadastro alanı','Son sulama bildirimi','Sezon durumu'],['3','299,13 da','21 Ağu','Hasat tamamlandı']):
        c.metric(label,value)
    badge('REAL DATA','Çiftçi bildirimi / TKGM · son sulama tarihi parsel bazında teyit bekliyor')
    st.markdown('<div class="hero"><div class="eyebrow" style="color:#93ceae">KARAR ODAĞI</div><h2>Su geldiğinde uygulanabilir bir plan.</h2><p>Kanalın ne zaman açılacağını bilmiyoruz. Gecikme, çalışma süresi ve parsel sırasının geçmiş sezon üzerindeki etkisini birlikte inceleyin.</p></div>',unsafe_allow_html=True)
    a,b=st.columns([2.2,1])
    with a:
        st.subheader('Pilotun gerçek sınırları'); badge('REAL DATA','WGS84 poligonları · uydu raster katmanı yok'); map_fields()
    with b:
        st.subheader('Saha defteri')
        st.write('**Ürün** · Pamuk\n\n**Ekim** · 5–7 Mayıs 2026\n\n**Yöntem** · Salma / vahşi sulama\n\n**Kaynak** · Kanal; bazı durumlarda traktörle yönlendirme')
        st.info('Debi, kanal sırası ve ilk iki sulama tarihi bilinmiyor. Laboratuvar girdileri varsayımdır.')
        st.metric('Seçili geometriden alan',f"{f['area']:.2f} da")
        badge('REAL DATA','Jeodezik hesap; kadastro alanından farklı olabilir')
    st.subheader('Meteoroloji · sezon bağlamı')
    if weather is not None:
        badge(weather_label,weather_source); st.line_chart(weather.set_index('Tarih')[['ET₀ (mm)','Yağış (mm)']])
        st.download_button('Meteoroloji CSV indir',weather.assign(source=weather_source,label=weather_label).to_csv(index=False),'weather-2026.csv','text/csv')
    else: st.info('Sol menüden gerçek arşivi getirin veya dosya yükleyin.')

elif page=='Uydu Analizi':
    badge('REAL DATA','Sentinel-2 L2A · yalnızca servis yanıtı geldiğinde gerçek seri gösterilir')
    index=st.radio('Spektral gösterge',['NDVI','NDMI','NDRE'],horizontal=True)
    st.caption('NDVI: (B08−B04)/(B08+B04) · NDMI: (B08−B11)/(B08+B11) · NDRE: (B8A−B05)/(B8A+B05). Tüm indeksler yaklaşık 20 m gridde; SCL 4/5 ve dataMask filtresi. Bulut/gölge ve diğer sınıflar dışlanır.')
    dates=st.date_input('Gösterim aralığı',(date(2026,5,5),date(2026,10,6)),min_value=date(2026,5,5),max_value=date(2026,10,6))
    if st.button('Üç parselin gerçek serisini getir'):
        cid=os.getenv('COPERNICUS_CLIENT_ID'); secret=os.getenv('COPERNICUS_CLIENT_SECRET')
        if not(cid and secret): st.warning('Sunucuda COPERNICUS_CLIENT_ID ve COPERNICUS_CLIENT_SECRET tanımlanmalı. Önceden ifşa edilen anahtarı iptal edip yenileyin.')
        else:
            try:
                token=get_token(cid,secret); rows=[]
                with st.spinner('Copernicus parsel istatistikleri…'):
                    for p in fields:
                        frame=satellite_stats(json.dumps(p['geometry'],sort_keys=True),token,index)
                        if not frame.empty: frame['Parsel']=p['name']; rows.append(frame)
                st.session_state[index]=pd.concat(rows) if rows else pd.DataFrame()
            except Exception: st.error('Copernicus verisi alınamadı. Kimlik bilgilerini ve servis erişimini sunucuda kontrol edin.')
    data=st.session_state.get(index,pd.DataFrame())
    if not data.empty and len(dates)==2:
        filtered=data[(data.Tarih>=pd.Timestamp(dates[0]))&(data.Tarih<=pd.Timestamp(dates[1]))]
        if filtered.empty: st.info('Bu aralıkta geçerli gözlem yok.')
        else:
            st.line_chart(filtered.pivot_table(index='Tarih',columns='Parsel',values=index))
            st.dataframe(filtered,hide_index=True)
            st.download_button('Kaynak etiketli CSV',filtered.assign(source='REAL DATA · Copernicus Sentinel-2 L2A',resolution='~20m',mask='SCL 4/5').to_csv(index=False),'satellite.csv','text/csv')
    else: st.info('Henüz gerçek uydu serisi yok. Grafik veya piksel değeri uydurulmaz; bağlantı olmadan gerçek parsel haritası kullanılabilir.')
    map_fields()
    st.caption('NDMI bir spektral nem vekilidir; kök bölgesi su ölçümü veya hastalık teşhisi değildir. Gerçek raster indirilmediği için ısı haritası gösterilmez.')

elif page=='Su Karar Laboratuvarı':
    badge('ILLUSTRATIVE SCENARIO','Geçmiş sezon / kısıtlı erişim ve tek salma sulama olayı karşılaştırması')
    if weather is None: st.info('Meteoroloji kaynağı seçin.'); st.stop()
    badge(weather_label,weather_source)
    a,b,c=st.columns(3)
    with a:
        start=st.date_input('14 günlük tarihsel pencere',date(2026,7,15),min_value=date(2026,5,5),max_value=date(2026,9,22))
        delay=st.slider('Kanal gecikmesi (gün)',0,10,3)
        window=st.slider('Kanal erişim penceresi (gün)',1,7,3)
        order=st.multiselect('Sabit parsel sırası · sırayla seçin',[p['name'] for p in fields],default=[p['name'] for p in fields])
    with b:
        flow=st.number_input('Kanal debisi (m³/saat; varsayım)',min_value=1.,value=1000.,step=100.)
        hours=st.slider('Günlük çalışma süresi (saat)',1,24,12)
        depth=st.slider('Tek salma olayı brüt derinliği (mm)',10,120,60)
        budget=st.number_input('Toplam erişilebilir su (m³)',min_value=0.,value=20000.,step=1000.)
    with c:
        initial=st.slider('Başlangıç tamponu (mm)',0,100,20)
        efficiency=st.slider('Salma uygulama verimi (varsayım)',.3,.9,.6,.05)
        rain=st.slider('Yağış senaryosu çarpanı',0.,2.,1.,.1)
        pump=st.toggle('Alternatif pompa sahada mümkün (varsayım)')
        pump_flow=st.number_input('Alternatif debi (m³/saat)',min_value=1.,value=800.)
        fuel=st.number_input('Pompa değişken maliyeti (TRY/saat)',min_value=0.,value=750.)
    if len(weather[(weather.Tarih>=pd.Timestamp(start)) & (weather.Tarih<pd.Timestamp(start)+pd.Timedelta(days=14))].dropna(subset=['ET₀ (mm)','Yağış (mm)']))!=14:
        st.warning('Seçili tarih için 14 tam günlük meteoroloji gerekli; tarih veya veri kaynağını değiştirin.'); st.stop()
    chosen=[next(p for p in fields if p['name']==name) for name in order]
    if not chosen: st.info('En az bir parsel seçin.'); st.stop()
    rows=[]; curves=[]; trace=[]
    for d in sorted(set([0,3,5,8,delay])):
        plans=schedule(chosen,d,window,flow,hours,depth,budget,pump,pump_flow)
        total=0; exposure=0
        for name,event,source,volume,duration in plans:
            series=balance(weather,start,initial,100,efficiency,depth,[] if event is None else [event],rain)
            area=next(p['area'] for p in chosen if p['name']==name)
            total+=series['Karşılanmayan ETc (mm)'].sum()*area
            exposure+=(30-series['Kök bölgesi tamponu (mm)']).clip(lower=0).sum()*area/sum(p['area'] for p in chosen)
            if d==delay:
                curves.append(series.assign(Parsel=name))
                trace.append({'Parsel':name,'Olay günü':None if event is None else str(pd.Timestamp(start)+pd.Timedelta(days=event))[:10],'Kaynak':source,'Brüt m³':round(volume),'Süre (saat)':round(duration,1),'Model pompa maliyeti TRY':round(duration*fuel) if 'pompa' in source else 0,'Etiket':'ILLUSTRATIVE SCENARIO'})
        rows.append({'Gecikme (gün)':d,'Karşılanmayan ETc eşdeğeri (m³)':round(total,1),'Tampon eşik altı maruziyet (mm·gün)':round(exposure,1)})
    st.subheader('Gecikmenin etkisi'); badge('MODEL ESTIMATE','Kc × ET₀ / günlük tampon dengesi; gerçek kayıp veya verim değildir')
    measure=st.radio('Karşılaştırma göstergesi',['Tampon eşik altı maruziyet (mm·gün)','Karşılanmayan ETc eşdeğeri (m³)'],horizontal=True)
    st.bar_chart(pd.DataFrame(rows).set_index('Gecikme (gün)')[[measure]])
    st.caption('Maruziyet = günlük max(0, 30 mm − kalan tampon) toplamının alan ağırlıklı ortalaması. 30 mm eşiği varsayımdır; fizyolojik stres veya verim kaybı değildir. Aynı toplam suyla dönem sonu ETc açığı eşit kalabilir; zamanlama maruziyeti yine değişebilir.')
    st.subheader('Seçili senaryonun uygulanabilir olayları'); st.dataframe(trace,hide_index=True)
    st.info('Neden? Sabit sırada her parselin bütün olay hacmi = alan × brüt mm. Olay bir çalışma gününe sığmalı; kanal penceresi, toplam hacim ve 14 günlük pencere aşılmamalı. Sığmayan olay için uygunluğu varsayılan pompa denenir. Kanal topolojisi doğrulanmadı; bu bir saha optimizasyonu değildir.')
    joined=pd.concat(curves); st.line_chart(joined.pivot_table(index='Tarih',columns='Parsel',values='Kök bölgesi tamponu (mm)'))
    st.subheader('Başlangıç nemi belirsizliği')
    uncertainty=[]
    for water in [max(0,initial-15),initial,min(100,initial+15)]:
        s=balance(weather,start,water,100,efficiency,depth,[],rain)
        uncertainty.append({'Başlangıç mm':water,'Sulamasız ETc açığı mm':round(s['Karşılanmayan ETc (mm)'].sum(),1)})
    st.dataframe(uncertainty,hide_index=True)
    st.caption('Varsayımlar: 100 mm tampon kapasitesi, etkili yağış %75 × senaryo çarpanı, 6 Mayıs ekim; Kc 0,35 → 1,15 → 0,50. Kalibrasyon yok. Su stresi göstergesi fizyolojik stres ölçümü değildir. Sabit birlik ücreti tasarrufa çevrilmez.')
    download('Senaryo ve varsayımları indir',{'label':'ILLUSTRATIVE SCENARIO','weather_source':weather_source,'start':start,'delay':delay,'window':window,'flow':flow,'hours':hours,'depth':depth,'budget':budget,'initial':initial,'capacity':100,'exposure_threshold_mm':30,'efficiency':efficiency,'rain_factor':rain,'pump':pump,'pump_flow':pump_flow,'fuel_hourly':fuel,'comparison':rows,'plan':trace,'initial_uncertainty':uncertainty},'water-scenario.json')

elif page=='Besin & Keşif Zonları':
    badge('ILLUSTRATIVE SCENARIO','Demonstratör · agronomist doğrulaması gerekli')
    st.subheader('Bir reçete değil, örnekleme planı')
    n=st.slider('Keşif zonu sayısı',2,5,3)
    from shapely.geometry import box, mapping
    g=shape(f['geometry']); x0,y0,x1,y1=g.bounds
    m=folium.Map(tiles=None); folium.TileLayer('OpenStreetMap').add_to(m)
    zones=[]
    for i in range(n):
        z=g.intersection(box(x0+(x1-x0)*i/n,y0,x0+(x1-x0)*(i+1)/n,y1))
        if z.is_empty: continue
        folium.GeoJson(mapping(z),style_function=lambda _,c=['#167c65','#76ac78','#e4ba65','#6da4bd','#b6a2cb'][i]:{'color':'white','weight':2,'fillColor':c,'fillOpacity':.65},tooltip=f'ILLUSTRATIVE SCENARIO · Keşif zonu {i+1}').add_to(m)
        zones.append({'Zon':i+1,'Alan da':round(abs(GEOD.geometry_area_perimeter(z)[0])/1000,2),'Görev':'Toprak örneği + saha fotoğrafı','Kaynak':'ILLUSTRATIVE SCENARIO · geometrik bölme'})
    m.fit_bounds([[y0,x0],[y1,x1]]); st_folium(m,height=420,use_container_width=True,returned_objects=[])
    st.dataframe(zones,hide_index=True)
    st.warning('Renkler geometrik örnekleme bölgeleridir; ölçülmüş vigor, besin eksikliği veya azot ihtiyacı değildir. VRA için toprak analizi, besin bütçesi ve ekipman doğrulaması gerekir.')
    download('Keşif planını indir',{'label':'ILLUSTRATIVE SCENARIO','parcel':selected,'zones':zones},'scouting.json')

elif page=='Bitki Riskleri':
    badge('ROADMAP DEMO','Uydu tek başına hastalık veya eksiklik teşhis edemez')
    st.subheader('Gözlem → saha kontrolü → doğrulama')
    threshold=st.slider('Göreli NDVI düşüşü eşiği (örnek)',.05,.4,.15,.01)
    data=st.session_state.get('NDVI',pd.DataFrame())
    if not data.empty:
        badge('MODEL ESTIMATE','Gerçek NDVI değişiminden basit eşik; doğrulanmış risk modeli değil')
        d=data[data.Parsel==selected].sort_values('Tarih').copy(); d['Değişim']=d.NDVI.diff()
        st.dataframe(d.assign(İnceleme=d['Değişim'] < -threshold),hide_index=True)
        st.caption('Hasat, gelişim evresi, bulut maskesi ve örnek sayısı değişimi de düşüş yaratabilir.')
    else: st.info('Gerçek NDVI henüz yüklenmedi. Uydu Analizi sayfasından seri alın; gerçek risk alarmı üretilmedi.')
    observation=st.text_area('Parsel saha notu (oturum içinde)',key='note_'+selected,placeholder='Tarih, gözlem, fotoğraf referansı…')
    download('Saha notunu indir',{'parcel':selected,'note':observation,'source':'Kullanıcı girişi; doğrulanmadı','status':'ROADMAP DEMO'},'field-note.json')

elif page=='Verim Görünümü':
    badge('ILLUSTRATIVE SCENARIO','Duyarlılık analizi · gerçek parsel verimi veya tahmin doğruluğu bilinmiyor')
    st.subheader('Varsayımların sonucu nasıl değiştirdiğini görün')
    baseline=st.number_input('Varsayılan başlangıç verimi (kg/da)',min_value=0.,value=400.)
    stress=st.slider('Varsayılan göreli ET açığı',0.,1.,.15,.01)
    ky=st.slider('Varsayılan tepki katsayısı Ky',0.,2.,.8,.05)
    band=st.slider('Varsayımsal belirsizlik aralığı ± (%)',5,60,25)
    estimate=max(0,baseline*(1-ky*stress))
    st.metric('Varsayımsal merkez (kg/da)',f'{estimate:.0f}')
    st.bar_chart(pd.DataFrame({'kg/da':[estimate*(1-band/100),estimate,estimate*(1+band/100)]},index=['Alt senaryo','Merkez','Üst senaryo']))
    st.caption('Bu aralık istatistiksel güven aralığı değildir. Formül: başlangıç × max(0, 1 − Ky × açık). Toplam tonaj, doğrulanmış kazanç veya ML tahmini sunulmaz.')
    download('Duyarlılık varsayımlarını indir',{'source':'ILLUSTRATIVE SCENARIO','baseline_kg_da':baseline,'relative_deficit':stress,'Ky':ky,'assumed_band_pct':band,'central_kg_da':estimate},'yield-sensitivity.json')

else:
    badge('REAL DATA','Kaynak sicili ve pilot eksikleri')
    st.dataframe([{'Kaynak':'TKGM / kullanıcı KML','Durum':'REAL DATA','Kapsam':'3 gerçek WGS84 poligonu'}, {'Kaynak':'Çiftçi beyanı','Durum':'REAL DATA','Kapsam':'Ekim, hasat, salma; son sulama parsel teyidi gerekli'}, {'Kaynak':weather_source or 'Yüklenmedi','Durum':weather_label,'Kapsam':'Ölçüm değil; model/reanalysis veya açık örnek hava'}, {'Kaynak':'Copernicus','Durum':'REAL DATA yalnızca bağlantı sonrası','Kapsam':'NDVI / NDMI / NDRE; raster yok'}],hide_index=True)
    st.subheader('Parsel bazında kayıt / varsayım')
    st.caption('Aşağıdaki tarihler teyit edilmiş gözlem sayılmaz. Değişiklikler oturumda tutulur; kalıcı kayıt için indirin.')
    records=st.data_editor(pd.DataFrame([{'Parsel':p['name'],'İlk sulama':'','İkinci sulama':'','Son sulama bildirimi':'2026-08-21','Teyit':'Bekliyor'} for p in fields]),key='irrigation_records',hide_index=True)
    download('Sulama kayıtlarını indir',{'source':'Kullanıcı kaydı / doğrulanmadı','records':records.to_dict('records')},'irrigation-records.json')
    download('Gerçek parsel GeoJSON indir',{'type':'FeatureCollection','features':features,'source':'REAL DATA · TKGM KML kullanıcı paylaşımı'},'pilot-parcels.geojson')
    st.subheader('Çalışan özellikler / demonstratör sınırı')
    st.success('Çalışan: gerçek parsel haritası, KML ekleme, arşiv retry/fallback ve JSON/CSV aktarımı, sunucudan Copernicus indeks sorguları, kısıtlı olay senaryosu, CSV/JSON dışa aktarma.')
    st.warning('Demonstratör: geometrik keşif zonları, basit NDVI eşik işaretleri, verim duyarlılığı. Doğrulanmış sulama optimizeri, hastalık teşhisi, N reçetesi, verim tahmini veya ekonomik tasarruf modeli yok.')
    st.write('**Toplanacak:** günlük debi ve erişim saatleri, kanal topolojisi, parsel sırası, pompa uygunluğu, ilk sulamalar, toprak suyu ve analizleri, gerçek hasat kg, ücretin sezon/olay temeli.')

st.divider(); st.caption('AgriOpticAI · Gerçek sınırlar. Açık varsayımlar. Uygulanabilir kararlar için pilot. / 2026 retrospektif')
