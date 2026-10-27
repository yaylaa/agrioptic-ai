import {useEffect,useState} from 'react';
import {MapContainer,TileLayer,GeoJSON,ImageOverlay,Marker,Tooltip,useMap} from 'react-leaflet';
import L from 'leaflet';
import {Layers,LocateFixed,Maximize,MapPin} from 'lucide-react';
import type {Parcel,Parcels,Raster,Satellite} from './types';
import 'leaflet/dist/leaflet.css';
const colors=['#2d8468','#4a86b7','#b28d46'];
function Extent({data,selected,target}:{data:Parcels;selected:string;target:{mode:string;count:number}}){const map=useMap();useEffect(()=>{const f=target.mode==='field'?data.features.filter(f=>f.properties.id===selected):data.features;map.fitBounds(L.geoJSON({type:'FeatureCollection',features:f} as GeoJSON.FeatureCollection).getBounds(),{padding:[50,50],maxZoom:17});},[target,data,map,selected]);useEffect(()=>{const timer=setTimeout(()=>map.invalidateSize(),200);return()=>clearTimeout(timer)},[map]);return null;}
export function MiniField({field}:{field:Parcel}){let coords=field.geometry.coordinates[0];const xs=coords.map(p=>p[0]),ys=coords.map(p=>p[1]);const minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);const scale=Math.min(36/((maxx-minx)*.79),36/(maxy-miny));const path=coords.map(([x,y])=>`${7+(x-minx)*.79*scale},${43-(y-miny)*scale}`).join(' ');return <svg width="50" height="50" viewBox="0 0 50 50" aria-hidden="true"><polygon points={path} fill="currentColor" fillOpacity=".13" stroke="currentColor" strokeWidth="1.7"/></svg>}
export default function FieldMap({data,selected,onSelect,raster,satellite,caption}:{data:Parcels;selected:string;onSelect:(id:string)=>void;raster:Raster|null;satellite:Satellite[];caption?:string}){
 const [layer,setLayer]=useState('satellite');const [tileError,setTileError]=useState(false);const [target,setTarget]=useState({mode:'farm',count:0});
 const [rasterOn,setRasterOn]=useState(true);
 useEffect(()=>{if(raster)setRasterOn(true)},[raster]);
 const field=data.features.find(f=>f.properties.id===selected)!;
 return <section className="map-panel panel" aria-label="Pilot parsel haritası">
  <div className="map-toolbar"><div><MapPin size={15}/><strong>Güllübahçe, Söke</strong><span className="desktop-only">Aydın · Türkiye</span></div><div><label><Layers size={15}/><select aria-label="Harita altlığı" value={layer} onChange={e=>{setLayer(e.target.value);setTileError(false)}}><option value="street">Harita</option><option value="satellite">Uydu altlığı</option><option value="plain">Yalnızca sınırlar</option></select></label></div></div>
  <div className="map-stage">
   <MapContainer center={[37.62,27.306]} zoom={15} zoomControl={false} scrollWheelZoom={false}>
    <Extent data={data} selected={selected} target={target}/>
    {layer!=='plain'&&<TileLayer key={layer} url={layer==='street'?'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png':'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'} attribution={layer==='street'?'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors':'Tiles © Esri, Maxar, Earthstar Geographics'} eventHandlers={{tileerror:()=>setTileError(true)}}/>}
    {data.features.map((f,i)=><GeoJSON key={f.properties.id} data={f} style={{color:f.properties.id===selected?'#176546':colors[i],fillColor:colors[i],fillOpacity:f.properties.id===selected?.28:.12,weight:f.properties.id===selected?3:2}} eventHandlers={{click:()=>onSelect(f.properties.id)}}><Tooltip sticky>{f.properties.name} · {f.properties.area_reported_da.toFixed(2)} da<br/>REAL DATA · TKGM sınırı</Tooltip></GeoJSON>)}
    {data.features.map((f,i)=><Marker key={f.properties.id} position={f.properties.centroid} icon={L.divIcon({className:'field-marker',html:`<span style="border-color:${colors[i]}">287 / ${f.properties.id}</span>`,iconSize:[90,28],iconAnchor:f.properties.id==='832'?[5,14]:f.properties.id==='837'?[85,14]:[45,14]})} eventHandlers={{click:()=>onSelect(f.properties.id)}}/>)}
    {raster&&rasterOn&&raster.metadata.date&&<ImageOverlay url={raster.url} bounds={raster.bounds} opacity={.9}/>}
    <MapZoom/>
   </MapContainer>
   <div className="map-floating-top"><span className="map-chip"><i/>REAL DATA <span>TKGM parsel sınırları</span></span></div>
   <div className="map-actions"><button title="Tüm parselleri göster" aria-label="Tüm parselleri göster" onClick={()=>setTarget({mode:'farm',count:target.count+1})}><Maximize size={18}/></button><button title="Seçili parsele odaklan" aria-label="Seçili parsele odaklan" onClick={()=>setTarget({mode:'field',count:target.count+1})}><LocateFixed size={18}/></button></div>
   <div className="map-legend"><strong>{raster&&rasterOn?`${raster.metadata.index} · gerçek raster`:'Pilot parselleri'}</strong>{raster&&rasterOn?<><div className="index-scale"/><div className="scale-values"><span>−0,2</span><span>1,0</span></div><small>REAL DATA · {raster.metadata.date} · 20 m</small><button className="text-button" onClick={()=>setRasterOn(false)}>Rasteri gizle</button></>:<>{data.features.map((f,i)=><button key={f.properties.id} className={f.properties.id===selected?'legend-selected':''} onClick={()=>onSelect(f.properties.id)}><i style={{background:colors[i]}}/>287 / {f.properties.id}<span>{f.properties.area_reported_da.toFixed(2)} da</span></button>)}{raster&&<button className="text-button" onClick={()=>setRasterOn(true)}>Gerçek rasteri göster</button>}</>}</div>
   <div className="north" aria-hidden="true">N<span>↑</span></div>
   {tileError&&<div className="tile-warning">Altlık kısmen yüklenemedi. Sınırlar kullanılabilir; “Yalnızca sınırlar” katmanını seçebilirsiniz.</div>}
  </div>
  <div className="map-footer"><span><i className="dot green"/>Seçili: 287 / {field.properties.id}</span><span>{layer==='satellite'?'Altlık tarihi bilinmiyor · Sentinel analizi değildir':layer==='plain'?'Ağsız geometri görünümü':'OpenStreetMap · uydu analizi katmanı değil'}</span><span>{caption||`${satellite.find(s=>s.parcel_id===selected)?.rows.length||0} geçerli uydu günü`}</span></div>
 </section>
}
function MapZoom(){const map=useMap();return <div className="custom-zoom"><button aria-label="Haritayı yakınlaştır" onClick={()=>map.zoomIn()}>+</button><button aria-label="Haritayı uzaklaştır" onClick={()=>map.zoomOut()}>−</button></div>}
