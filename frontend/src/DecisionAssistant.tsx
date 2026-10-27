import {useEffect,useRef,useState} from 'react';
import {ArrowDownToLine,ArrowRight,Check,CheckCircle2,ChevronRight,CornerDownLeft,FlaskConical,LoaderCircle,MessageSquare,Send,SlidersHorizontal,Sparkles} from 'lucide-react';
import type {Parcels} from './types';
import type {Query,Result,Plan} from './WaterLab';
import {api,download} from './api';
import {assistantExamples,proposeScenario} from './assistantScenario';
import type {Change,Proposal} from './assistantScenario';
import FieldMap from './FieldMap';

type Message={role:'user'|'assistant';text:string};
type Comparison={before:Result;after:Result;changes:Change[]};
const fmt=(v:number|undefined,d=1)=>v===undefined?'—':v.toLocaleString('tr-TR',{maximumFractionDigits:d});
const date=(s:string,offset=0)=>{const d=new Date(s+'T12:00:00');d.setDate(d.getDate()+offset);return d.toLocaleDateString('tr-TR',{day:'numeric',month:'long'})};
const method=(s:string)=>s==='transfer'?'Traktörle aktarım':s==='canal'?'Kanal':'Olay seçilmedi';
const validPlan=(p:Plan)=>Boolean(p.checks_passed&&['optimal','feasible_time_limit'].includes(p.status));
const intro='Koşullar değiştiğinde planı birlikte yeniden değerlendirelim. Bu etkileşim demosu belirli Türkçe ifadeleri kurallarla model girdisine çevirir; LLM bağlı değil. Değişiklikleri önizleyebilir, mevcut su motoruyla gerçek bir senaryo hesabı çalıştırabilirsiniz.';

export default function DecisionAssistant({parcels,selected,onSelect,onWater}:{parcels:Parcels;selected:string;onSelect:(id:string)=>void;onWater:()=>void}){
 const [query,setQuery]=useState<Query|null>(null),[result,setResult]=useState<Result|null>(null),[comparison,setComparison]=useState<Comparison|null>(null);
 const [messages,setMessages]=useState<Message[]>([{role:'assistant',text:intro}]);
 const [draft,setDraft]=useState(''),[pending,setPending]=useState<Extract<Proposal,{ok:true}>|null>(null),[busy,setBusy]=useState(true),[error,setError]=useState('');
 const [revision,setRevision]=useState(0);const mounted=useRef(true);const log=useRef<HTMLDivElement>(null);
 useEffect(()=>{mounted.current=true;const abort=new AbortController();setBusy(true);setError('');
  api<{query:Query}>('/api/water/defaults',{signal:abort.signal}).then(async d=>{
   let q=d.query;
   try{const saved=JSON.parse(localStorage.getItem('agriflow-water-query')||'null');if(saved&&typeof saved==='object'&&Array.isArray(saved.fields)&&saved.decision_date)q=saved}catch{/* defaults */}
   const r=await api<Result>('/api/water/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q),signal:abort.signal});
   if(!abort.signal.aborted){setQuery(r.query);setResult(r)}
  }).catch(e=>{if(!abort.signal.aborted)setError(e.message)}).finally(()=>{if(!abort.signal.aborted)setBusy(false)});
  return()=>{mounted.current=false;abort.abort()}
 },[revision]);
 useEffect(()=>{if(log.current)log.current.scrollTop=log.current.scrollHeight},[messages,pending,busy]);
 function submit(text=draft){if(!query||busy||pending||!text.trim())return;
  const proposal=proposeScenario(text,query);setDraft('');
  setMessages(old=>[...old,{role:'user',text:text.trim()},{role:'assistant',text:proposal.ok?'Şu koşulları değiştirmeyi öneriyorum. Hesaplamadan önce aşağıdaki önizlemeyi kontrol edin.':proposal.message}]);
  if(proposal.ok)setPending(proposal);
 }
 async function calculate(){if(!pending||!result||busy)return;const proposal=pending,previous=result;setBusy(true);setError('');
  try{const next=await api<Result>('/api/water/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(proposal.query)});
   if(!mounted.current)return;
   const a=previous.selected.optimized,b=next.selected.optimized;setResult(next);setQuery(next.query);setComparison({before:previous,after:next,changes:proposal.changes});setPending(null);
   const deltas=validPlan(a)&&validPlan(b)&&a.loss_mm!==undefined&&b.loss_mm!==undefined&&a.variable_cost_try!==undefined&&b.variable_cost_try!==undefined;
   setMessages(old=>[...old,{role:'assistant',text:validPlan(b)?`Yeni senaryo hesaplandı. ${deltas?`Önceki plana göre değişken gider ${fmt(b.variable_cost_try!-a.variable_cost_try!,0)} TL, karşılanamayan su talebi ${fmt(b.loss_mm!-a.loss_mm!,2)} mm değişti. `:''}Parsel bazındaki kaynak ve tarih farklarını sağda inceleyebilirsiniz. Bu sonuç girilen varsayımlara bağlıdır; gerçekleşmiş kazanç veya bugünkü sulama talimatı değildir.`:`Bu koşullarda doğrulanmış uygun plan sunulamıyor: ${b.reason||'Çözücü uygun bir sonuç döndürmedi.'} Önceki plan yeni koşulların sonucu olarak kullanılmamalı.`}]);
  }catch(e){if(mounted.current)setError((e as Error).message)}finally{if(mounted.current)setBusy(false)}
 }
 function openLab(){if(!query||pending||busy)return;try{localStorage.setItem('agriflow-water-query',JSON.stringify(query));onWater()}catch{setError('Tarayıcıya senaryo kaydedilemedi. Mevcut karşılaştırmayı rapor olarak indirebilirsiniz.')}}
 const plan=result?.selected.optimized;const before=comparison?.before.selected.optimized;
 return <div className="assistant-workspace">
  <section className="assistant-intro"><div><div className="eyebrow"><Sparkles size={14}/> AGRIOPTICAI ASİSTAN</div><h2>Koşullarınızı anlatın.<br/>Seçenekleri birlikte hesaplayalım.</h2><p>Bir plan, değişen koşullar, görünür gerekçeler. Tarımsal karar platformunun ilk etkileşim örneği su planlamasıyla çalışıyor.</p></div><div className="assistant-demo-label"><FlaskConical size={16}/><strong>ROADMAP DEMO</strong><span>LLM bağlı değil · sınırlı ifade kuralları<br/>Su dengesi ve optimizasyon hesabı mevcut motordan</span></div></section>
  <div className="assistant-context"><span><i className="dot green"/>Söke · 3 gerçek parsel</span><span>{query?`${date(query.decision_date)} 2026 · ${query.horizon} günlük tarihsel senaryo`:'Senaryo hazırlanıyor'}</span><span>ILLUSTRATIVE SCENARIO · Hasat tamamlandı</span></div>
  <div className="assistant-layout">
   <section className="assistant-chat panel"><header><div><MessageSquare size={18}/><strong>Kararı birlikte şekillendirelim</strong></div><span>Yerel etkileşim demosu</span></header>
    <div className="assistant-messages" ref={log} role="log" aria-label="Asistan konuşması" aria-live="polite">{messages.map((m,i)=><article className={'assistant-message '+m.role} key={i}><div className="assistant-speaker">{m.role==='assistant'?<><Sparkles size={13}/>AgriOpticAI · demo</>:'Siz'}</div><p>{m.text}</p></article>)}
     {pending&&<div className="assistant-proposal"><strong><SlidersHorizontal size={15}/>Anlaşılan değişiklikler</strong>{pending.changes.map(c=><div key={c.key}><span>{c.label}</span><small>{c.before}<ArrowRight size={12}/><b>{c.after}</b></small></div>)}<p>Diğer koşullar korunur. Henüz hesaplanmadı.</p><div className="assistant-proposal-actions"><button className="primary" onClick={calculate} disabled={busy}><Check size={15}/>Bu koşullarla hesapla</button><button className="text-button" onClick={()=>{setPending(null);setMessages(old=>[...old,{role:'assistant',text:'Önizleme iptal edildi. Mevcut hesap değişmedi.'}])}} disabled={busy}>Vazgeç</button></div></div>}
     {busy&&<div className="assistant-progress" role="status"><LoaderCircle size={16} className="spin"/>{result?'Yeni koşullar gerçek karar motorunda hesaplanıyor…':'Mevcut senaryo gerçek karar motorunda hazırlanıyor…'}</div>}
    </div>
    {error&&<div className="error" role="alert">{error} {result?'Yeni hesap uygulanmadı; son başarılı sonuç korunuyor.':<button className="text-button" onClick={()=>setRevision(n=>n+1)}>Yeniden dene</button>}</div>}
    <div className="assistant-suggestions"><span>Bir örnekle başlayın veya aynı biçimde yazın</span>{assistantExamples.slice(0,4).map(s=><button key={s} onClick={()=>submit(s)} disabled={!query||busy||!!pending}>{s}<ChevronRight size={13}/></button>)}</div>
    <form className="assistant-compose" onSubmit={e=>{e.preventDefault();submit()}}><label htmlFor="assistant-input">Koşullarda ne değişti?</label><div><textarea id="assistant-input" value={draft} maxLength={600} rows={2} placeholder="Örn. Kanal 2 gün daha gecikecek, traktör kullanılamıyor." onChange={e=>setDraft(e.target.value)} disabled={!query||busy||!!pending}/><button className="primary" aria-label="Mesajı gönder" type="submit" disabled={!draft.trim()||!query||busy||!!pending}><Send size={18}/></button></div><small><CornerDownLeft size={11}/>Gecikme, traktör, su bütçesi, dizel fiyatı ve günlük çalışma desteklenir. Mesajlar LLM servisine gönderilmez.</small></form>
   </section>
   <div className="assistant-evidence" aria-live="polite"><section className="panel assistant-plan"><div className="assistant-plan-heading"><div><div className="eyebrow">{comparison?'ÖNCE / SONRA':'BAŞLANGIÇ PLANI'}</div><h3>{comparison?'Yeni koşullar neyi değiştirdi?':'Senaryonuzun hesaplanan sonucu'}</h3></div><span className="badge model">MODEL ESTIMATE</span></div>
    {plan?<><div className="assistant-plan-metrics"><Metric label="Değişken plan gideri" before={before?.variable_cost_try} after={plan.variable_cost_try} unit="TL" decimals={0}/><Metric label="Karşılanamayan su talebi" before={before?.loss_mm} after={plan.loss_mm} unit="mm" decimals={2}/></div><div className={'assistant-plan-status '+(!validPlan(plan)?'needs-review':'')}><CheckCircle2 size={15}/>{validPlan(plan)?'Girilen kapasite ve sıra kısıtları sağlandı':plan.reason||'Bu koşullarda uygun plan doğrulanamadı'}</div>
    <div className="assistant-field-plans">{parcels.features.map(f=>{const id=f.properties.id,e=plan.events.find(e=>e.parcel_id===id),old=before?.events.find(e=>e.parcel_id===id);const changed=!!old&&!!e&&(old.method!==e.method||old.start_day!==e.start_day||old.completion_day!==e.completion_day);return <button key={id} onClick={()=>onSelect(id)} className={(selected===id?'selected ':'')+(changed?'changed':'')}><div><strong>287 / {id}</strong>{changed&&<span>Plan değişti</span>}</div>{old&&changed&&<small className="assistant-before">Önce: {method(old.method)}{old.method!=='wait'?` · ${date(result!.query.decision_date,old.start_day)}`:''}</small>}<span>{e?method(e.method):'Uygun olay yok'}{e&&e.method!=='wait'?` · ${date(result!.query.decision_date,e.start_day)}`:''}</span><small>{e&&e.method!=='wait'?`${e.reserved_hours} saat rezerve · ${fmt(e.volume_m3,0)} m³`:plan.reason||'Bu senaryoda tam-parsel sulama olayı seçilmedi'}</small></button>})}</div>
    <p className="assistant-model-note">Su talebi açığı model göstergesidir; doğrudan verim kaybı değildir. Toprak, kapasite ve gider varsayımları saha teyidi gerektirir.</p><div className="assistant-result-actions"><button className="secondary" onClick={openLab} disabled={busy||!!pending}>Senaryoyu laboratuvarda aç<ArrowRight size={14}/></button><button className="text-button" disabled={busy||!!pending} onClick={()=>download('agriflow-assistant-scenario.json',{label:'ILLUSTRATIVE SCENARIO',interaction:'Rule-based demo; no LLM',comparison,current:result})}><ArrowDownToLine size={14}/>Rapor</button></div></>:<div className="assistant-plan-placeholder">{busy?'Başlangıç hesabı hazırlanıyor…':'Hesap yüklenemedi. Konuşma alanından yeniden deneyin.'}</div>}
   </section>
   <FieldMap data={parcels} selected={selected} onSelect={onSelect} raster={null} satellite={[]} caption="REAL DATA · sınırlar; plan kartları MODEL ESTIMATE"/>
   <details className="assistant-roadmap panel"><summary>Platform vizyonu ve bu demonun sınırı</summary><p>Sulama bugün hesap motoruna bağlı. Gübreleme, iş gücü, ekipman takvimi ve verim kararları; alanlarına özgü veri, model ve doğrulamayla aynı konuşma deneyimine bağlanabilir. Bu demo bu alanlarda otomatik karar üretmez.</p><p>Bu oturum laboratuvar ayarlarını kendiliğinden değiştirmez. Aktarmak için “Senaryoyu laboratuvarda aç” kullanılır. Konuşma bu sayfadan çıkılınca temizlenir.</p><strong>Desteklenen örnekler</strong><ul>{assistantExamples.map(s=><li key={s}>{s}</li>)}</ul></details>
   </div>
  </div>
 </div>
}
function Metric({label,before,after,unit,decimals}:{label:string;before?:number;after?:number;unit:string;decimals:number}){return <div><span>{label}</span>{before!==undefined&&<small>Önce {fmt(before,decimals)} {unit}</small>}<strong>{fmt(after,decimals)}<em>{unit}</em></strong>{before!==undefined&&after!==undefined&&<small className="assistant-delta">Fark {after-before>0?'+':''}{fmt(after-before,decimals)} {unit}</small>}</div>}
