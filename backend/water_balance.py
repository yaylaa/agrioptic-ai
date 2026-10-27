"""FAO-56 single-Kc daily bucket. All soil/irrigation states are explicit assumptions."""
from datetime import date

def crop_coefficient(day,planting,initial_days=30,development_days=50,mid_days=40,late_days=33,kc_initial=.35,kc_mid=1.15,kc_end=.5):
    age=(date.fromisoformat(day)-date.fromisoformat(planting)).days
    if age<initial_days:return kc_initial,'Başlangıç'
    if age<initial_days+development_days:
        return kc_initial+(kc_mid-kc_initial)*(age-initial_days)/development_days,'Gelişme'
    if age<initial_days+development_days+mid_days:return kc_mid,'Orta dönem'
    t=min(1,max(0,(age-initial_days-development_days-mid_days)/late_days))
    return kc_mid+(kc_end-kc_mid)*t,'Geç dönem'

def simulate(weather,soil,initial_fraction,events=None,planting='2026-05-06',crop=None):
    """Inputs at day start, drainage, Ks, then ET. Storage balance closes exactly.
    No capillary rise; constant effective root depth; single Kc reduces soil evaporation too.
    events maps day index to gross whole-field irrigation depth (mm).
    """
    taw=soil['awc_mm_m']*soil['root_depth_m'];dr=taw*initial_fraction;out=[]
    for i,w in enumerate(weather):
        if w['et0_mm'] is None or w['rain_mm'] is None:raise ValueError('Eksiksiz günlük yağış ve ET₀ gerekli.')
        if w['et0_mm']<0 or w['rain_mm']<0:raise ValueError('Negatif meteoroloji girdisi.')
        kc,stage=crop_coefficient(w['date'],planting,**(crop or {}));etc=kc*w['et0_mm']
        p=min(.8,max(.1,soil['depletion_fraction']+.04*(5-etc)));raw=p*taw
        initial=dr;rain=w['rain_mm']*soil['rain_infiltration'];gross=(events or {}).get(i,0)
        if gross<0:raise ValueError('Negatif sulama olayı.')
        irrigation=gross*soil['efficiency'];available=taw-dr+rain+irrigation
        dp=max(0,available-taw);storage=min(taw,available);wet_dr=taw-storage
        ks=1. if wet_dr<=raw else min(1,max(0,storage/(taw-raw)))
        eta=min(storage,etc*ks);dr=taw-(storage-eta)
        residual=(taw-initial)+rain+irrigation-dp-eta-(taw-dr)
        out.append({'date':w['date'],'stage':stage,'kc':kc,'et0_mm':w['et0_mm'],'rain_mm':w['rain_mm'],'effective_rain_mm':rain,'runoff_mm':w['rain_mm']-rain,'gross_irrigation_mm':gross,'irrigation_net_mm':irrigation,'application_loss_mm':gross-irrigation,'taw_mm':taw,'raw_mm':raw,'dr_start_mm':initial,'dr_mm':dr,'ks':ks,'etc_mm':etc,'eta_mm':eta,'unmet_et_mm':etc-eta,'exposure_mm':max(0,dr-raw),'deep_percolation_mm':dp,'balance_residual_mm':residual,'net_refill_mm':dr,'gross_refill_mm':dr/soil['efficiency'],'label':'MODEL ESTIMATE'})
    return out
