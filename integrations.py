"""AgriFlow Söke v0.2 — exploratory retrospective cotton multi-parcel PoC.

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
                                        headers={"User-Agent": "AgriFlow-Pilot/0.2.1"})
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


@st.cache_data(ttl=14400, show_spinner=False)
def satellite_stats(geometry_json, _token, index):
    bands = {"NDVI": ("B08", "B04"), "NDMI": ("B08", "B11"), "NDRE": ("B8A", "B05")}
    a, b = bands[index]
    script = EVALSCRIPT.replace('"B04","B08"', '"'+b+'","'+a+'"').replace('s.B08', 's.'+a).replace('s.B04', 's.'+b)
    body = {"input": {"bounds": {"geometry": json.loads(geometry_json), "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}}, "data": [{"type": "sentinel-2-l2a", "dataFilter": {"mosaickingOrder": "leastCC"}}]}, "aggregation": {"timeRange": {"from": "2026-05-05T00:00:00Z", "to": "2026-10-07T00:00:00Z"}, "aggregationInterval": {"of": "P10D"}, "evalscript": script, "resx": 0.000225, "resy": 0.00018}}
    r = requests.post("https://sh.dataspace.copernicus.eu/statistics/v1", json=body, headers={"Authorization": f"Bearer {_token}"}, timeout=120)
    r.raise_for_status()
    rows=[]
    for item in r.json().get("data", []):
        stats=item.get("outputs", {}).get("ndvi", {}).get("bands", {}).get("B0", {}).get("stats", {})
        n=stats.get("sampleCount",0)-stats.get("noDataCount",0)
        if n>0 and stats.get("mean") is not None:
            rows.append({"Tarih":pd.Timestamp(item["interval"]["from"]).tz_localize(None), index:stats["mean"], "Geçerli piksel":n})
    return pd.DataFrame(rows)
