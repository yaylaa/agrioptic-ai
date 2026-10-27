"""Canonical pilot geometry. Never replace or simplify source polygons."""
import json
from pathlib import Path
from pyproj import Geod
from shapely.geometry import shape

ROOT=Path(__file__).resolve().parents[1]
GEOD=Geod(ellps='WGS84')

def parcels():
    data=json.loads((ROOT/'pilot_parcels.geojson').read_text())
    for f in data['features']:
        g=shape(f['geometry']); p=f['properties']
        p['id']=str(p['parsel']); f['id']=p['id']
        p['area_calculated_da']=round(abs(GEOD.geometry_area_perimeter(g)[0])/1000,3)
        p['area_reported_da']=round(p.get('area_cadastre_m2',0)/1000,3)
        p['centroid']=[g.centroid.y,g.centroid.x]
        p['label']='REAL DATA'
    return data

def parcel(pid):
    return next((f for f in parcels()['features'] if f['properties']['id']==pid),None)

def center():
    fs=parcels()['features']; total=sum(f['properties']['area_calculated_da'] for f in fs)
    return tuple(sum(f['properties']['centroid'][i]*f['properties']['area_calculated_da'] for f in fs)/total for i in [0,1])
