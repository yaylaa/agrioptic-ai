"""Discrete whole-field operation candidates + SciPy/HiGHS MILP. No yield/profit model."""
from datetime import date,timedelta
from itertools import product
import json,math
import numpy as np
from scipy.optimize import milp,Bounds,LinearConstraint
from scipy.sparse import coo_matrix
from pydantic import BaseModel,Field,model_validator
from .parcels import parcels,ROOT
from .weather import get_weather
from .water_balance import simulate

class FieldInput(BaseModel):
    id:str
    initial_depletion:float=Field(.65,ge=0,le=1)
    canal:bool=True
    transfer:bool=True
    priority:float=Field(1,ge=.1,le=5)

class Soil(BaseModel):
    awc_mm_m:float=Field(150,ge=30,le=300)
    root_depth_m:float=Field(1.2,ge=.2,le=2)
    depletion_fraction:float=Field(.65,ge=.1,le=.8)
    rain_infiltration:float=Field(.8,ge=0,le=1)
    efficiency:float=Field(.6,ge=.2,le=1)

class Crop(BaseModel):
    initial_days:int=Field(30,ge=1,le=90)
    development_days:int=Field(50,ge=1,le=100)
    mid_days:int=Field(40,ge=1,le=100)
    late_days:int=Field(33,ge=1,le=100)
    kc_initial:float=Field(.35,ge=.1,le=1.5)
    kc_mid:float=Field(1.15,ge=.1,le=1.5)
    kc_end:float=Field(.5,ge=.1,le=1.5)

class Economics(BaseModel):
    fee_basis:str='seasonal'
    union_try_da:float=Field(860,ge=0,le=10000)
    seasonal_already_paid:bool=True
    transfer_fee_ratio:float=Field(.55,ge=0,le=2)
    diesel_l_100da:float=Field(200,ge=0,le=2000)
    diesel_try_l:float=Field(55,ge=0,le=500)
    machine_try_hour:float=Field(150,ge=0,le=10000)
    labor_try_hour:float=Field(30,ge=0,le=5000)
    transfer_other_try:float=Field(500,ge=0,le=100000)
    cost_weight:float=Field(.5,ge=0,le=50)
    @model_validator(mode='after')
    def basis(self):
        if self.fee_basis not in ['seasonal','event']:raise ValueError('Ücret temeli seasonal veya event olmalı.')
        return self

class DecisionQuery(BaseModel):
    decision_date:date=date(2026,7,15)
    planting_date:date=date(2026,5,6)
    horizon:int=Field(14,ge=7,le=21)
    expected_arrival:int=Field(0,ge=0,le=7)
    delay:int=Field(5,ge=0,le=10)
    canal_window_days:int=Field(3,ge=1,le=10)
    daily_hours:int=Field(12,ge=2,le=24)
    canal_flow_m3h:float=Field(1000,ge=1,le=10000)
    canal_volume_m3:float=Field(18000,ge=0,le=100000)
    transfer_flow_m3h:float=Field(800,ge=1,le=10000)
    transfer_volume_m3:float=Field(16000,ge=0,le=100000)
    transfer_enabled:bool=True
    gross_depth_mm:float=Field(80,ge=10,le=150)
    fixed_order:bool=False
    order:list[str]=['786','832','837']
    minimum_fields:int=Field(0,ge=0,le=3)
    et0_multiplier:float=Field(1,ge=.5,le=1.8)
    scenario_rain_mm:float=Field(0,ge=0,le=80)
    scenario_rain_day:int=Field(3,ge=0,le=20)
    initial_uncertainty:float=Field(.15,ge=0,le=.4)
    depth_shortfall_fraction:float=Field(.25,ge=0,le=.5)
    fields:list[FieldInput]=[FieldInput(id='786',initial_depletion=.6),FieldInput(id='832',initial_depletion=.7),FieldInput(id='837',initial_depletion=.55)]
    soil:Soil=Soil()
    crop:Crop=Crop()
    economics:Economics=Economics()
    @model_validator(mode='after')
    def validate_inputs(self):
        if set(self.order)!={'786','832','837'} or len(self.order)!=3:raise ValueError('Üç gerçek parselin her biri sırada bir kez bulunmalı.')
        if sorted(f.id for f in self.fields)!=['786','832','837']:raise ValueError('Üç gerçek parselin girdileri gerekli.')
        if not date(2026,5,6)<=self.decision_date or self.decision_date+timedelta(days=self.horizon-1)>date(2026,10,5):raise ValueError('Karar penceresi 2026 ekim/hasat sınırlarında kalmalı.')
        if not date(2026,5,5)<=self.planting_date<=date(2026,5,7) or self.decision_date<self.planting_date:raise ValueError('Ekim tarihi pilot bildirimiyle uyumlu olmalı.')
        if self.scenario_rain_day>=self.horizon:raise ValueError('Yağış günü ufuk içinde olmalı.')
        return self

def planning_weather(q,observed):
    cutoff=q.decision_date.isoformat()
    prior=[w for w in observed if w['date']<cutoff and w['date']>=(q.decision_date-timedelta(days=7)).isoformat() and w['et0_mm'] is not None]
    if not prior:raise ValueError('Karar tarihinden önce kullanılabilir ET₀ yok.')
    et0=sum(w['et0_mm'] for w in prior)/len(prior)*q.et0_multiplier
    return [{'date':(q.decision_date+timedelta(days=i)).isoformat(),'et0_mm':et0,'rain_mm':q.scenario_rain_mm if i==q.scenario_rain_day else 0} for i in range(q.horizon)],len(prior)

def field_areas():return {f['properties']['id']:(f['properties']['area_calculated_da'],f['properties']['area_reported_da']) for f in parcels()['features']}

def cost_breakdown(q,pid,method,volume,basis):
    e=q.economics;area,fee_area=field_areas()[pid]
    hours=volume/(q.canal_flow_m3h if method=='canal' else q.transfer_flow_m3h)
    liters=e.diesel_l_100da*fee_area/100 if method=='transfer' else 0
    return {'diesel_l':liters,'diesel_try':liters*e.diesel_try_l,'machine_try':hours*e.machine_try_hour if method=='transfer' else 0,'labor_try':hours*e.labor_try_hour,'other_try':e.transfer_other_try if method=='transfer' else 0,'method_fee_try':fee_area*e.union_try_da*(e.transfer_fee_ratio if method=='transfer' else 1) if basis=='event' else 0,'operating_hours':hours}

def trajectory(q,pid,weather,event=None,initial_shift=0,depth_factor=1):
    f=next(f for f in q.fields if f.id==pid)
    return simulate(weather,q.soil.model_dump(),min(1,max(0,f.initial_depletion+initial_shift)),{} if not event or event['method']=='wait' else {event['completion_day']:q.gross_depth_mm*depth_factor},q.planting_date.isoformat(),q.crop.model_dump())

def metrics(rows):return sum(r['unmet_et_mm'] for r in rows),sum(r['exposure_mm'] for r in rows)

def candidates(q,weather,delay,volume_factor,allow_transfer,basis):
    areas=field_areas();total=sum(a[0] for a in areas.values());result=[];reasons={};memo={}
    per_day=q.daily_hours//2; horizon_slots=q.horizon*per_day
    for f in q.fields:
        rows=trajectory(q,f.id,weather);loss,exposure=metrics(rows)
        result.append({'parcel_id':f.id,'method':'wait','start':0,'end':0,'completion_day':0,'volume_m3':0,'cost_try':0,'breakdown':{},'loss_mm':loss,'exposure_mm_days':exposure,'rows':rows})
        reasons[f.id]=[]
        for method,enabled,flow,volume,arrival,last_day in [('canal',f.canal,q.canal_flow_m3h,q.canal_volume_m3*volume_factor,q.expected_arrival+delay,min(q.horizon,q.expected_arrival+delay+q.canal_window_days)),('transfer',allow_transfer and q.transfer_enabled and f.transfer,q.transfer_flow_m3h,q.transfer_volume_m3,0,q.horizon)]:
            if not enabled:reasons[f.id].append(method+': uygunluk kapalı');continue
            v=areas[f.id][0]*q.gross_depth_mm
            duration=max(1,math.ceil(v/flow/2-1e-10))
            start_min=arrival*per_day;end_max=min(horizon_slots,last_day*per_day)
            if v>volume+1e-8:reasons[f.id].append(method+': tek olay hacmi kaynak bütçesini aşıyor');continue
            if end_max-start_min<duration:reasons[f.id].append(method+': pencere / debi / çalışma süresi yetersiz');continue
            breakdown=cost_breakdown(q,f.id,method,v,basis);cost=sum(breakdown[k] for k in ['diesel_try','machine_try','labor_try','other_try','method_fee_try'])
            for start in range(start_min,end_max-duration+1):
                end=start+duration;day=(end-1)//per_day
                if (f.id,day) not in memo:memo[f.id,day]=trajectory(q,f.id,weather,{'method':method,'completion_day':day})
                rows=memo[f.id,day];loss,exposure=metrics(rows)
                result.append({'parcel_id':f.id,'method':method,'start':start,'end':end,'completion_day':day,'volume_m3':v,'cost_try':cost,'breakdown':breakdown,'loss_mm':loss,'exposure_mm_days':exposure,'rows':rows})
    for c in result:
        weight=areas[c['parcel_id']][0]/total*next(f.priority for f in q.fields if f.id==c['parcel_id'])
        c['objective']=weight*(c['loss_mm']+.03*c['exposure_mm_days'])+q.economics.cost_weight*c['cost_try']/1000
    return result,reasons

def verify_plan(q,events,delay,volume_factor):
    issues=[]
    if len(events)!=3 or {e['parcel_id'] for e in events}!={'786','832','837'}:issues.append('one choice per field')
    perday=q.daily_hours//2;used=set();vol={'canal':0,'transfer':0};byid={e['parcel_id']:e for e in events}
    for e in events:
        if e['method']=='wait':continue
        f=next(f for f in q.fields if f.id==e['parcel_id']);m=e['method']
        if m=='canal' and not f.canal:issues.append('canal suitability')
        if m=='transfer' and not(q.transfer_enabled and f.transfer):issues.append('transfer suitability')
        for t in range(e['start'],e['end']):
            if t in used:issues.append('shared crew overlap')
            used.add(t)
        if e['end']>q.horizon*perday or e['start']<0:issues.append('horizon')
        if m=='canal' and (e['start']<(q.expected_arrival+delay)*perday or e['end']>(q.expected_arrival+delay+q.canal_window_days)*perday):issues.append('window')
        flow=q.canal_flow_m3h if m=='canal' else q.transfer_flow_m3h
        if e['volume_m3']>flow*(e['end']-e['start'])*2+1e-5:issues.append('flow')
        vol[m]+=e['volume_m3']
    if vol['canal']>q.canal_volume_m3*volume_factor+1e-5 or vol['transfer']>q.transfer_volume_m3+1e-5:issues.append('water budget')
    if sum(e['method']!='wait' for e in events)<q.minimum_fields:issues.append('minimum fields')
    if q.fixed_order:
        for a,b in zip(q.order,q.order[1:]):
            if byid[b]['method']!='wait' and (byid[a]['method']=='wait' or byid[a]['end']>byid[b]['start']):issues.append('physical sequence')
    return list(set(issues))

def optimize(q,weather,delay,volume_factor=1,allow_transfer=True,basis='seasonal',force_wait=False):
    cs,reasons=candidates(q,weather,delay,volume_factor,allow_transfer,basis)
    if force_wait:
        chosen=[c for c in cs if c['method']=='wait'];status='reference';gap=0
    else:
        constraints=[];lower=[];upper=[];n=len(cs)
        def row(values,lo=-np.inf,hi=np.inf):constraints.append(values);lower.append(lo);upper.append(hi)
        for f in q.fields:row({j:1 for j,c in enumerate(cs) if c['parcel_id']==f.id},1,1)
        for t in range(q.horizon*(q.daily_hours//2)):row({j:1 for j,c in enumerate(cs) if c['method']!='wait' and c['start']<=t<c['end']},hi=1)
        for method,budget in [('canal',q.canal_volume_m3*volume_factor),('transfer',q.transfer_volume_m3)]:row({j:c['volume_m3'] for j,c in enumerate(cs) if c['method']==method},hi=budget)
        row({j:1 for j,c in enumerate(cs) if c['method']!='wait'},lo=q.minimum_fields)
        if q.fixed_order:
            big=q.horizon*(q.daily_hours//2)+1
            for a,b in zip(q.order,q.order[1:]):
                row({j:(1 if c['parcel_id']==b else -1) for j,c in enumerate(cs) if c['method']!='wait' and c['parcel_id'] in [a,b]},hi=0)
                row({j:(c['end'] if c['parcel_id']==a else big-c['start']) for j,c in enumerate(cs) if c['method']!='wait' and c['parcel_id'] in [a,b]},hi=big)
        rr=[];cc=[];vv=[]
        for i,values in enumerate(constraints):
            for j,v in values.items():rr.append(i);cc.append(j);vv.append(v)
        A=coo_matrix((vv,(rr,cc)),shape=(len(constraints),n)).tocsc()
        opt=milp(np.array([c['objective'] for c in cs]),integrality=np.ones(n),bounds=Bounds(0,1),constraints=LinearConstraint(A,lower,upper),options={'time_limit':3,'mip_rel_gap':.0001})
        if opt.x is None:return {'status':'infeasible' if opt.status==2 else 'solver_unresolved','events':[],'reason':'Belirtilen zorunlu sulama sayısı, kaynaklar ve sıra altında uygun plan yok.' if opt.status==2 else 'Çözücü süre sınırında uygulanabilir plan bulamadı.','reasons':reasons,'candidate_count':n,'label':'MODEL ESTIMATE'}
        chosen=[c for c,x in zip(cs,opt.x) if x>.5]
        issues=verify_plan(q,chosen,delay,volume_factor)
        if issues:raise ValueError('Çözücü plan doğrulaması başarısız: '+', '.join(issues))
        status='optimal' if opt.status==0 else 'feasible_time_limit';gap=float(getattr(opt,'mip_gap',0) or 0)
    areas=field_areas();total=sum(a[0] for a in areas.values());loss=sum(c['loss_mm']*areas[c['parcel_id']][0] for c in chosen)
    cost=sum(c['cost_try'] for c in chosen);fixed=sum(a[1] for a in areas.values())*q.economics.union_try_da if basis=='seasonal' else 0
    out=[]
    for c in chosen:
        e={k:v for k,v in c.items() if k!='rows'}
        e.update(start_day=c['start']//(q.daily_hours//2),start_work_hour=c['start']%(q.daily_hours//2)*2,finish_work_hour=((c['end']-1)%(q.daily_hours//2)+1)*2 if c['method']!='wait' else 0,reserved_hours=(c['end']-c['start'])*2)
        out.append(e)
    return {'status':status,'events':out,'rows':{c['parcel_id']:c['rows'] for c in chosen},'loss_m3':loss,'loss_mm':loss/total,'exposure_mm_days':sum(c['exposure_mm_days']*areas[c['parcel_id']][0] for c in chosen)/total,'variable_cost_try':cost,'fixed_seasonal_try':fixed,'sunk_try':fixed if q.economics.seasonal_already_paid else 0,'total_accounting_try':cost+fixed,'objective':sum(c['objective'] for c in chosen),'candidate_count':len(cs),'usage':{'canal_m3':sum(c['volume_m3'] for c in chosen if c['method']=='canal'),'transfer_m3':sum(c['volume_m3'] for c in chosen if c['method']=='transfer'),'reserved_hours':sum((c['end']-c['start'])*2 for c in chosen),'canal_budget_m3':q.canal_volume_m3*volume_factor,'transfer_budget_m3':q.transfer_volume_m3},'solver_gap':gap,'reasons':reasons,'checks_passed':not bool(verify_plan(q,chosen,delay,volume_factor)),'label':'MODEL ESTIMATE'}

def evidence(q):
    out=[]
    for f in q.fields:
        values={}
        for index in ['NDVI','NDMI','NDRE']:
            p=ROOT/'data/satellite'/f'{f.id}_{index}.json'
            if p.exists():
                d=json.loads(p.read_text());rows=[r for r in d['rows'] if r['date']<q.decision_date.isoformat()]
                values[index]=rows[-1] if rows else None
        out.append({'parcel_id':f.id,'indices':values,'label':'REAL DATA','note':'Yalnızca karar gününden önceki edinimler. Geçmiş veri yayın/erişim zamanı teyit edilmedi; modele sayısal asimilasyon yok.'})
    return out

def run_decision(q):
    source=get_weather();planning,n=planning_weather(q,source['rows']);cases=[]
    delays=sorted(set([0,2,5,8,q.delay]))
    for d in delays:
        cases.append({'id':f'delay-{d}','delay':d,'volume_factor':1,'title':f'{d} gün gecikme','canal_only':optimize(q,planning,d,allow_transfer=False,basis=q.economics.fee_basis),'optimized':optimize(q,planning,d,basis=q.economics.fee_basis)})
    cases.append({'id':'reduced','delay':q.delay,'volume_factor':.5,'title':'Seçili gecikme + %50 kanal suyu','canal_only':optimize(q,planning,q.delay,.5,False,q.economics.fee_basis),'optimized':optimize(q,planning,q.delay,.5,True,q.economics.fee_basis)})
    selected=next(c for c in cases if c['id']==f'delay-{q.delay}')
    plan=selected['optimized'];baseline=optimize(q,planning,q.delay,basis=q.economics.fee_basis,force_wait=True)
    alternate_basis='event' if q.economics.fee_basis=='seasonal' else 'seasonal'
    fee_alternative=optimize(q,planning,q.delay,basis=alternate_basis)
    uncertainty=[];realized={};areas=field_areas();total=sum(a[0] for a in areas.values())
    if plan['events']:
        for shift,factor in product([-q.initial_uncertainty,0,q.initial_uncertainty],[1-q.depth_shortfall_fraction,1]):
            loss=sum(metrics(trajectory(q,e['parcel_id'],planning,e,shift,factor))[0]*areas[e['parcel_id']][0] for e in plan['events'])
            uncertainty.append({'initial_depletion_shift':shift,'delivered_depth_factor':factor,'loss_mm':loss/total})
        lookup={w['date']:w for w in source['rows']};actual=[lookup.get(w['date']) for w in planning]
        if all(w and w['et0_mm'] is not None and w['rain_mm'] is not None for w in actual):
            realized={e['parcel_id']:trajectory(q,e['parcel_id'],actual,e) for e in plan['events']}
    operation=[e for e in plan['events'] if e['method']!='wait']
    transfer=[e for e in operation if e['method']=='transfer'];waiting=[e['parcel_id'] for e in plan['events'] if e['method']=='wait']
    if not plan['events']:recommendation=plan['reason']
    elif transfer:recommendation=f"Varsayılan {q.delay} günlük gecikmede, {', '.join('287/'+e['parcel_id'] for e in transfer)} için alternatif aktarım seçiliyor. Planın değişken işletme gideri {plan['variable_cost_try']:,.0f} TRY; karşılanamayan su talebi {plan['loss_mm']:.1f} mm. Uygunluk ve kapasite varsayımları sahada teyit edilmeli."
    elif operation:recommendation=f"Varsayılan {q.delay} günlük gecikmede kanal penceresini kullanın: {' → '.join('287/'+e['parcel_id'] for e in sorted(operation,key=lambda e:e['start']))}. Karşılanamayan su talebi {plan['loss_mm']:.1f} mm; değişken gider {plan['variable_cost_try']:,.0f} TRY. Bu tarihsel bir senaryodur."
    else:recommendation='Girilen koşullarda beklemek seçildi. Uygun olay yok veya varsayılan maliyet ağırlığı müdahaleyi baskılıyor; ölçülmüş kayıp veya kâr sonucu değildir.'
    return {'query':q.model_dump(mode='json'),'label':'MODEL ESTIMATE','input_label':'ILLUSTRATIVE SCENARIO','selected':selected,'scenarios':cases,'no_irrigation':baseline,'fee_alternative':{'basis':alternate_basis,'plan':fee_alternative},'uncertainty':uncertainty,'realized_weather_evaluation':realized,'satellite_context':evidence(q),'recommendation':recommendation,'waiting_parcels':waiting,'provenance':{'weather':source['metadata'],'planning_weather_label':'ILLUSTRATIVE SCENARIO','planning_weather':f'Karar gününden önceki {n} günlük ET₀ ortalaması × çarpan; yağış kullanıcı senaryosu. Gerçek bir arşiv tahmini değildir. Reanalysis geçmişte o anda erişilebilir veri olarak doğrulanmadı.','future_weather_used_in_optimization':False,'satellite_assimilated':False,'soil':'FAO-56 referans parametreleri + kullanıcı varsayımları; toprak ölçümü yok.','last_irrigation':'21 Ağustos 2026, üç parsel için kullanıcı tarafından doğrulandı; hesap penceresine otomatik bilinmeyen hacim eklenmez.','solver':'SciPy milp / HiGHS, 2 saatlik ikili olay kararları, paylaşılan tek ekip, parsel başına en fazla bir olay.','objective':'Alan ve öncelik ağırlıklı karşılanmayan ET (mm) + 0.03 × RAW üzeri maruziyet (mm·gün) + λ × değişken maliyet / 1000. λ bir tercih katsayısı, ekonomik zarar modeli değildir.','water_timing':'Tam-parsel net suyu olayın tamamlandığı günün başında günlük modele girer; saat içi ıslanma cephesi modellenmez.','uncertainty':'Aynı planın başlangıç Dr ± aralık ve eksik uygulama derinliği altında duyarlılığı; daha fazla su kapasitesi varsayılmaz.'}}
