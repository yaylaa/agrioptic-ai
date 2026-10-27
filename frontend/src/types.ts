import type {FeatureCollection,Feature,Polygon} from 'geojson';
export interface ParcelProps{ id:string;name:string;parsel:number;ada:number;product:string;area_calculated_da:number;area_reported_da:number;centroid:[number,number];source:string;planting_window:string;harvest_window:string;last_irrigation_reported:string;irrigation_method:string;}
export type Parcel=Feature<Polygon,ParcelProps>;
export type Parcels=FeatureCollection<Polygon,ParcelProps>;
export interface Meta{label:string;source:string;retrieved_at:string;mode:string;spatial_note?:string;resolution_m?:number;mask?:string;}
export interface WeatherRow{date:string;rain_mm:number|null;et0_mm:number|null;tmax_c:number|null;}
export interface Weather{rows:WeatherRow[];metadata:Meta;warning?:string;}
export interface Observation{date:string;value:number;min:number;max:number;stdev:number;valid_pixels:number;sample_count:number;acquired_at:string[];cloud_percent_scene:number|null;}
export interface Satellite{parcel_id:string;index:string;rows:Observation[];acquisitions:{date:string;acquired_at:string;cloud_percent_scene:number|null}[];metadata:Meta;warning?:string;}
export interface Raster{url:string;bounds:[[number,number],[number,number]];metadata:Meta&{date:string;index:string};}
