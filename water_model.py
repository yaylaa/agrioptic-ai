"""Transparent event scheduling demonstrator; no yield or profit calibration."""
import pandas as pd

def kc(day):
    age=(day-pd.Timestamp('2026-05-06')).days
    if age<30: return .35
    if age<80: return .35+(age-30)/50*.8
    if age<125: return 1.15
    return max(.5,1.15-(age-125)/28*.65)

def balance(weather, start, initial, capacity, efficiency, depth, events, rain_factor=1):
    frame=weather[(weather.Tarih>=pd.Timestamp(start)) & (weather.Tarih<pd.Timestamp(start)+pd.Timedelta(days=14))].copy()
    if len(frame)!=14 or frame[['ET₀ (mm)','Yağış (mm)']].isna().any().any():
        raise ValueError('14 tam günlük meteoroloji girdisi gerekli.')
    water=initial; rows=[]
    for i,row in enumerate(frame.to_dict('records')):
        etc=row['ET₀ (mm)']*kc(row['Tarih'])
        incoming=depth*efficiency if i in events else 0
        available=water+row['Yağış (mm)']*.75*rain_factor+incoming
        deficit=max(0,etc-available)
        water=min(capacity,max(0,available-etc))
        rows.append({'Tarih':row['Tarih'],'Kök bölgesi tamponu (mm)':water,'Karşılanmayan ETc (mm)':deficit,'ETc (mm)':etc,'Net sulama (mm)':incoming})
    return pd.DataFrame(rows)

def schedule(fields, delay, window, flow, hours, depth, budget, pump, pump_flow):
    """Whole-field events, serial fixed order, daily canal window and shared budget."""
    remaining=budget; day=delay; used=0; pump_day=0; pump_used=0; plans=[]
    for f in fields:
        volume=f['area']*depth
        duration=volume/flow if flow>0 else float('inf')
        if used+duration>hours: day+=1; used=0
        feasible=duration<=hours and day<min(14,delay+window) and volume<=remaining
        if feasible:
            plans.append((f['name'],day,'Kanal',volume,duration)); remaining-=volume; used+=duration
        elif pump and pump_flow>0 and volume/pump_flow<=hours and volume<=remaining:
            pump_duration=volume/pump_flow
            if pump_used+pump_duration>hours: pump_day+=1; pump_used=0
            if pump_day<14:
                plans.append((f['name'],pump_day,'Alternatif pompa (varsayım)',volume,pump_duration)); remaining-=volume; pump_used+=pump_duration
            else: plans.append((f['name'],None,'Uygulanabilir olay yok',0,0))
        else: plans.append((f['name'],None,'Uygulanabilir olay yok',0,0))
    return plans
