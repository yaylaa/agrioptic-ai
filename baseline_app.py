"""AgriOpticAI Söke v0.2 — exploratory retrospective cotton multi-parcel PoC.

Uses real TKGM user-provided geometries, optional Copernicus satellite stats,
and optional Open-Meteo archived weather. Scenarios and LP are hypothetical,
NOT a validated irrigation scheduling system or a model of observed yields.
"""
from __future__ import annotations

import io
import json
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import folium
import pandas as pd
import requests
import streamlit as st
from pyproj import Geod
from scipy.optimize import linprog
from shapely.geometry import Polygon, mapping, shape
from streamlit_folium import st_folium

BASE = Path(__file__).resolve().parent
st.set_page_config(page_title="AgriOpticAI • Söke Pamuk Pilot", page_icon="🌿", layout="wide")
st.title("🌿 AgriOpticAI — Söke Pamuk Pilot")
st.caption("v0.2.1 · 3 gerçek parsel · 2026 sezonunun geriye dönük analizi · senaryo bazlı deneysel su dağıtımı")
st.warning("**Dikkat:** Ekim 2026 itibarıyla bildirilen hasat sona ermiş durumdadır. Buradaki senaryolar, geçmiş sezon için varsayımsal karşılaştırmalardır; gerçek sulama önerisi, doğrulanmış ürün kaybı veya gerçekleşmiş tasarruf değildir.")

GEOD = Geod(ellps="WGS84")
COLORS = ["#118c74", "#387dca", "#ef9d34", "#ac58a7", "#df675a", "#64748b"]

def from_kml(file) -> dict:
    root = ET.fromstring(file.getvalue())
    coords_el = root.find(".//{*}Polygon/{*}outerBoundaryIs/{*}LinearRing/{*}coordinates")
    if coords_el is None or not coords_el.text:
        raise ValueError("KML'de kapalı poligon bulunamadı.")
    pts = []
    for v in coords_el.text.split():
        raw = v.split(",")
        pts.append([float(raw[0]), float(raw[1])])
    poly = Polygon(pts)
    if not poly.is_valid or poly.is_empty:
        raise ValueError("Geçersiz parsel geometrisi.")
    placemark_name = root.find(".//{*}Placemark/{*}name")
    name = placemark_name.text if placemark_name is not None else file.name
    return {"type": "Feature", "geometry": mapping(poly), "properties": {"name": name, "product": "Belirtilmedi", "source": "Kullanıcı yüklemesi"}}

WEATHER_DAILY = "precipitation_sum,et0_fao_evapotranspiration,temperature_2m_max"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

def weather_params(lat: float, lon: float) -> dict:
    return {"latitude": round(lat, 5), "longitude": round(lon, 5),
            "start_date": "2026-05-05", "end_date": "2026-10-06",
            "timezone": "Europe/Istanbul", "daily": WEATHER_DAILY}

def weather_to_frame(d: dict) -> pd.DataFrame:
    daily = d.get("daily") or {}
    names = ["time", "precipitation_sum", "et0_fao_evapotranspiration", "temperature_2m_max"]
    if any(daily.get(key) is None for key in names):
        raise ValueError("Hava verisinde gerekli günlük sütunlar bulunamadı")
    lengths = [len(daily[key]) for key in names]
    if min(lengths) == 0 or len(set(lengths)) != 1:
        raise ValueError("Hava verisi boş veya sütun uzunlukları tutarsız")
    frame = pd.DataFrame({"Tarih": pd.to_datetime(daily["time"]),
                          "Yağış (mm)": daily["precipitation_sum"],
                          "ET₀ (mm)": daily["et0_fao_evapotranspiration"],
                          "Tmax (°C)": daily["temperature_2m_max"]})
    for c in ["Yağış (mm)", "ET₀ (mm)", "Tmax (°C)"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.sort_values("Tarih").drop_duplicates(subset=["Tarih"])
    if frame.empty or frame[["Yağış (mm)", "ET₀ (mm)"]].isna().all(axis=None):
        raise ValueError("Kullanılabilir geçmiş meteoroloji gözlemi yok")
    return frame

@st.cache_data(ttl=14400, show_spinner=False)
def historical_weather(lat: float, lon: float) -> tuple[pd.DataFrame,str]:
    params = weather_params(lat, lon)
    errors = []
    sources = [(ARCHIVE_URL, "Open-Meteo Historical Weather (reanalysis)"),
               (HISTORICAL_FORECAST_URL, "Open-Meteo Historical Forecast (arşivlenmiş tahminler)")]
    # Some networks/API nodes abruptly close connections. Retry and switch endpoint.
    for url, source in sources:
        for attempt in range(2):
            try:
                response = requests.get(url, params=params, timeout=(10, 30),
                                        headers={"User-Agent": "AgriOpticAI-Pilot/0.2.1"})
                response.raise_for_status()
                data = weather_to_frame(response.json())
                return data, source
            except (requests.RequestException, ValueError, KeyError) as exc:
                errors.append(f"{source}, deneme {attempt+1}: {str(exc)[:140]}")
                # An invalid argument/permission will not be fixed by retries.
                if isinstance(exc, requests.HTTPError) and response.status_code in (400, 401, 403, 404):
                    break
                if attempt == 0:
                    time.sleep(1)
    raise RuntimeError(" | ".join(errors[-4:]))

def uploaded_weather(file) -> tuple[pd.DataFrame, str]:
    if file.name.lower().endswith(".json"):
        d = json.loads(file.getvalue().decode("utf-8-sig"))
    else:
        raw = file.getvalue().decode("utf-8-sig")
        lines = raw.splitlines()
        header_at = next((i for i, line in enumerate(lines)
                          if line.lower().lstrip().startswith("time,")), None)
        if header_at is None:
            raise ValueError("CSV içinde time,precipitation_sum,... başlıklarını bulamadım")
        csvframe = pd.read_csv(io.StringIO("\n".join(lines[header_at:])))
        d = {"daily": {key: csvframe[key].tolist() for key in
                         ["time", "precipitation_sum", "et0_fao_evapotranspiration",
                          "temperature_2m_max"]}}
    return weather_to_frame(d), "Kullanıcının yüklediği Open-Meteo JSON/CSV dosyası"

def get_token(client_id, client_secret):
    r = requests.post("https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
      data={"grant_type":"client_credentials", "client_id":client_id,"client_secret":client_secret}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

EVALSCRIPT = """//VERSION=3
function setup() {
  return {input:[{bands:["B04","B08","SCL","dataMask"]}],
          output:[{id:"ndvi",bands:1,sampleType:"FLOAT32"}, {id:"dataMask",bands:1}]};
}
function evaluatePixel(s) {
  let den=s.B08+s.B04;
  let valid=(s.dataMask===1 && den>0 && (s.SCL===4 || s.SCL===5));
  return {ndvi:[valid ? (s.B08-s.B04)/den : 0],dataMask:[valid ? 1 : 0]};
}"""

@st.cache_data(ttl=14400, show_spinner=False)
def cdse_ndvi_stats(geometry_json, _token):
    bounds = json.loads(geometry_json)
    body = {"input": {"bounds": {"geometry": bounds, "properties":{"crs":"http://www.opengis.net/def/crs/EPSG/0/4326"}},
                      "data":[{"type":"sentinel-2-l2a", "dataFilter":{"mosaickingOrder":"leastCC"}}]},
            "aggregation":{"timeRange":{"from":"2026-05-05T00:00:00Z","to":"2026-10-07T00:00:00Z"},
                           "aggregationInterval":{"of":"P10D"}, "evalscript": EVALSCRIPT,
                           "resx":0.00009, "resy":0.00009}}
    r=requests.post("https://sh.dataspace.copernicus.eu/statistics/v1", json=body,
                    headers={"Authorization":f"Bearer {_token}","Content-Type":"application/json"}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Copernicus HTTP {r.status_code}: {r.text[:250]}")
    results=[]
    for item in r.json().get("data", []):
        s=item.get("outputs",{}).get("ndvi",{}).get("bands",{}).get("B0",{}).get("stats",{})
        n_valid=s.get("sampleCount",0)-s.get("noDataCount",0)
        if n_valid > 0 and s.get("mean") is not None:
            results.append({"Tarih": pd.Timestamp(item["interval"]["from"]).tz_localize(None),
                            "NDVI": float(s["mean"]), "Geçerli piksel": int(n_valid)})
    return pd.DataFrame(results)

features=json.loads((BASE/"pilot_parcels.geojson").read_text(encoding="utf-8"))["features"]
extra = st.file_uploader("İstersen ek bir parseli (örn. 287/465) KML olarak yükle", type=["kml"], accept_multiple_files=True)
for f in extra or []:
    try:
        feat=from_kml(f)
        if not any(p["properties"]["name"] == feat["properties"]["name"] for p in features):
            features.append(feat)
    except Exception as exc:
        st.error(f"{f.name} okunamadı: {exc}")

valid=[]
for idx,f in enumerate(features):
    g=shape(f["geometry"])
    if isinstance(g, Polygon) and g.is_valid and not g.is_empty:
        a=abs(GEOD.geometry_area_perimeter(g)[0]);
        valid.append({"name":f["properties"].get("name",f"Parsel {idx+1}"),
                      "geom":g,"area":a,"label":f["properties"].get("name", "?"), "color":COLORS[idx%len(COLORS)]})

if not valid:
    st.stop()
area_total=sum(x["area"] for x in valid)
meanlat=sum(x["geom"].centroid.y*x["area"] for x in valid)/area_total
meanlon=sum(x["geom"].centroid.x*x["area"] for x in valid)/area_total

st.header("1 · Gerçek pilot parselleri")
m1,m2,m3=st.columns(3)
m1.metric("Parsel sayısı",len(valid))
m2.metric("Hesaplanan toplam alan", f"{area_total/1000:,.2f} da")
m3.metric("Ürün", "Pamuk (bildirilen üç parsel)")
mapview=folium.Map(location=[meanlat,meanlon],zoom_start=15, tiles="OpenStreetMap",control_scale=True)
for p in valid:
    gj=folium.GeoJson(mapping(p["geom"]), name=p["name"],
        style_function=lambda _,c=p["color"]:{"color":c,"fillColor":c,"weight":3,"fillOpacity":0.18})
    gj.add_to(mapview)
    folium.Marker([p["geom"].centroid.y,p["geom"].centroid.x],
      tooltip=f'{p["name"]}: {p["area"]/1000:.1f} da',
      icon=folium.DivIcon(html=f'<div style="font-size:12px;font-weight:bold;white-space:nowrap;background:white;border-radius:5px;padding:3px">{p["name"].split()[-1]}</div>')).add_to(mapview)
minxs=[p["geom"].bounds[0] for p in valid]; minys=[p["geom"].bounds[1] for p in valid]
maxxs=[p["geom"].bounds[2] for p in valid]; maxys=[p["geom"].bounds[3] for p in valid]
mapview.fit_bounds([[min(minys),min(minxs)],[max(maxys),max(maxxs)]])
st_folium(mapview, height=440,use_container_width=True, returned_objects=[])
st.dataframe(pd.DataFrame([{"Parsel":p["name"],"Alan (da, KML ölçümü)":round(p["area"]/1000,2),
                            "Enlem":round(p["geom"].centroid.y,6),"Boylam":round(p["geom"].centroid.x,6)} for p in valid]),
             hide_index=True,use_container_width=True)
st.caption("Üç KML geometrisi gerçek, koordinat ölçümüyle alanlar hesaplanmıştır. Bu ekran mülkiyet, sulama bağlantısı veya debi doğrulaması yapmaz.")

st.header("2 · 2026 sezonu meteoroloji arşivi")
st.caption("Open-Meteo Historical Weather API (alternatif: Historical Forecast). Parseller yakın olduğundan ortak merkez koordinatından model verisi kullanılır, mikroklima ölçümü değildir.")
with st.expander("Bağlantı sorunu / dosyadan veri yükle", expanded=False):
    st.caption("Aşağıdaki adres yalnızca parsellerin konumunu içerir, gizli anahtar içermez. Tarayıcıda açılıyorsa JSON dosyasını indirip uygulamaya yükleyebilirsin.")
    test_url = ARCHIVE_URL + "?" + urlencode(weather_params(meanlat, meanlon))
    st.link_button("Open-Meteo sorgusunu tarayıcıda aç", test_url)
    uploaded_meteo = st.file_uploader("Open-Meteo JSON veya CSV dosyası yükle (isteğe bağlı)", type=["json", "csv"], key="weather_file")
    if st.button("Hava verisini yeniden sorgula"):
        historical_weather.clear()
        st.rerun()
meteo = None
meteo_source = None
if uploaded_meteo is not None:
    try:
        meteo, meteo_source = uploaded_weather(uploaded_meteo)
        st.success("Meteoroloji verisi yüklenen dosyadan okundu.")
    except Exception as exc:
        st.error(f"Yüklenen hava verisi okunamadı: {exc}")
else:
    try:
        with st.spinner("Open-Meteo geçmiş verileri sorgulanıyor; bağlantı koparsa yeniden denenecek..."):
            meteo, meteo_source = historical_weather(meanlat, meanlon)
    except Exception as exc:
        st.error(f"Hava arşivi alınamadı: {exc}")
        st.info("Parsel haritası ve Copernicus analizi bağımsız çalışır. Yukarıdaki bağlantıyı tarayıcıda deneyebilir ya da Open-Meteo JSON/CSV dosyası yükleyebilirsin.")
if meteo is not None:
    st.success(f"Meteoroloji verisi hazır · Kaynak: {meteo_source}")
    if "Historical Forecast" in meteo_source:
        st.info("Birincil arşiv servisi başarısız olduğu için arşivlenmiş geçmiş hava tahminleri kullanıldı; bu, reanalysis ile aynı veri ürünü değildir.")
    st.line_chart(meteo.set_index("Tarih")[["Yağış (mm)", "ET₀ (mm)"]])
    st.dataframe(meteo.tail(10),hide_index=True,use_container_width=True)

st.header("3 · Uydu gelişim örüntüleri · 2026")
st.caption("Sentinel-2 L2A gerçek NDVI parsel ortalaması, 10 günlük aralıklarla. Her parsel için ayrı Copernicus Statistical API sorgusu yapılır.")
with st.expander("Copernicus bağlantısı (isteğe bağlı)",expanded=False):
    st.warning("Daha önce ekran görüntüsünde görünen Client Secret'ı Copernicus panelinden iptal edip yenisini kullanın. Gizli anahtarı sohbetlerde veya ekran görüntülerinde paylaşmayın.")
    cid=st.text_input("OAuth Client ID")
    secret=st.text_input("OAuth Client Secret",type="password")
    if st.button("Üç parselin gerçek NDVI geçmişini çek",disabled=not(cid and secret)):
        try:
            token=get_token(cid,secret)
            all_stats=[]
            progress=st.progress(0)
            for j,p in enumerate(valid):
                frame=cdse_ndvi_stats(json.dumps(mapping(p["geom"]),sort_keys=True),token)
                if not frame.empty:
                    frame["Parsel"]=p["name"]
                    all_stats.append(frame)
                progress.progress((j+1)/len(valid))
            st.session_state["multi_ndvi"]=pd.concat(all_stats,ignore_index=True) if all_stats else pd.DataFrame()
        except Exception as exc:
            st.error(f"NDVI yüklenemedi: {exc}")
if "multi_ndvi" in st.session_state:
    ndvi=st.session_state["multi_ndvi"]
    if not ndvi.empty:
        graph=ndvi.pivot_table(index="Tarih",columns="Parsel",values="NDVI")
        st.line_chart(graph)
        st.dataframe(ndvi,hide_index=True,use_container_width=True)
    else:
        st.warning("Seçilen sezon için geçerli NDVI gözlemi bulunamadı.")
st.info("Uydu verilerinin su stresi, hastalık veya verim kaybı nedeni olduğunu yalnızca NDVI ile söyleyemeyiz. 2026'nın gerçek sulama miktarı ve tarihleri bilinmediğinden doğrulanmış geriye dönük model kurulamıyor.")

st.header("4 · Deneysel su tahsisi · Ne olurdu? (LP demo)")
st.caption("Seçilen 2026 haftasının tarihsel hava koşullarını kullanır. **Kc, başlangıç toprak suyu, sulama verimliliği, su kotası ve öncelikler varsayımsaldır.** Salma sulamada parseller arası su aktarımı sahada mümkün değilse bu tahsis uygulanamaz.")
if meteo is not None:
    left,right=st.columns(2)
    with left:
        week=st.date_input("Geriye dönük 7 günlük pencerenin başlangıcı",value=date(2026,7,15),min_value=date(2026,5,5),max_value=date(2026,9,29))
        kc=st.slider("Örnek ürün katsayısı Kc — gelişim evresine göre kalibre edilmedi", 0.30,1.30,1.05,0.05)
        effective_rain=st.slider("Yağışın etkili kısmı (varsayım)",0.0,1.0,0.75,0.05)
    with right:
        initial_water=st.slider("Başlangıç kullanılabilir su tamponu (mm; varsayım)",0.0,50.0,6.0,1.0)
        efficiency=st.slider("Sulama uygulama verimliliği (varsayım)",0.30,1.00,0.60,0.05)
        budget=st.number_input("Üç parsele kullanılabilir TOPLAM su (m³; varsayım)",min_value=0.0,value=8000.0,step=500.0)
    window=meteo[(meteo["Tarih"]>=pd.Timestamp(week)) & (meteo["Tarih"]<pd.Timestamp(week)+pd.Timedelta(days=7))]
    if len(window)<7:
        st.warning("Arşivde bu hafta için yedi gün bulunamadı.")
    else:
        etc=float(window["ET₀ (mm)"].sum())*kc
        rain=float(window["Yağış (mm)"].sum())*effective_rain
        net_depth=max(0.0,etc-rain-initial_water)
        st.write(f"**Bu haftanın model girdileri:** ET₀ × Kc ≈ {etc:.1f} mm; etkili yağış ≈ {rain:.1f} mm; varsayımsal net su açığı ≈ {net_depth:.1f} mm.")
        rows=[]
        for p in valid:
            w=st.slider(f"{p['name']} — varsayımsal ekonomik/zirai öncelik",1,5,3,key=f"priority_{p['name']}")
            req=net_depth*p["area"]/1000/max(efficiency,0.01)
            rows.append({"Parsel":p["name"],"Alan (da)":round(p["area"]/1000,2),"Öncelik (varsayım)":w,"Model talebi (m³)":req})
        tab=pd.DataFrame(rows)
        bounds=[(0.0,float(v)) for v in tab["Model talebi (m³)"]]
        opt=linprog(c=-tab["Öncelik (varsayım)"].to_numpy(dtype=float),A_ub=[[1.0]*len(rows)],b_ub=[budget],bounds=bounds,method="highs")
        if opt.success:
            tab["Model tahsisi (m³)"]=opt.x.round(1)
            tab["Model açığı (m³)"]=(tab["Model talebi (m³)"]-opt.x).clip(lower=0).round(1)
            display=tab.copy();display["Model talebi (m³)"]=display["Model talebi (m³)"].round(1)
            st.dataframe(display,hide_index=True,use_container_width=True)
            st.bar_chart(display.set_index("Parsel")[["Model talebi (m³)","Model tahsisi (m³)"]])
            st.caption("**LP'nin gerçekten yaptığı:** Girilen öncelik puanlarıyla ağırlıklandırılmış toplam tahsisi maksimize eder; toplam su bütçesi ile parselin hesaplanan talebini aşmama kısıtları uygulanır. Bu bir ürün verim/kâr modeli değildir; su nöbeti, kanal yönü, akış debisi ve gün/saat kısıtları henüz modele dahil değildir.")
        else:
            st.error(f"Çözücü başarısız: {opt.message}")

st.divider()
st.caption("AgriOpticAI pilot PoC v0.2.1 — TKGM KML kullanıcı paylaşımı; ücretsiz veri servisleri üçüncü taraf kullanım şartlarına tabidir. Sulama talimatı değildir.")
