import type {Query} from './WaterLab';

export interface Change {key:string;label:string;before:string;after:string}
export type Proposal={ok:true;query:Query;changes:Change[]}|{ok:false;message:string};
export const assistantExamples=[
 'Kanal 2 gün daha gecikecek, traktör kullanılamıyor.',
 'Kanal 8 gün gecikecek.',
 'Traktör kullanılabilir.',
 'Kanal suyu yarıya düşsün.',
 'Mazot 70 TL olsun.',
 'Günlük çalışma 8 saat olsun.',
];
const normalize=(s:string)=>s.toLocaleLowerCase('tr-TR').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/ı/g,'i').replace(/³/g,'3').trim();
const numbers:Record<string,string>={sifir:'0',bir:'1',iki:'2',uc:'3',dort:'4',bes:'5',alti:'6',yedi:'7',sekiz:'8',dokuz:'9',on:'10'};
const fmt=(v:number)=>v.toLocaleString('tr-TR',{maximumFractionDigits:2});

/** Explicit phrase rules for a local interaction demo. No LLM or broad NLU claim.
 * Reject the entire proposal on any unsupported clause; never apply partial intent.
 */
export function proposeScenario(text:string,current:Query):Proposal{
 const fail=(message:string):Proposal=>({ok:false,message});
 if(!text.trim()||text.length>600)return fail('1–600 karakterlik bir senaryo yazın. Örnek ifadelerden başlayabilirsiniz.');
 let clean=normalize(text).replace(/\b(sifir|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on)\b/g,m=>numbers[m]);
 clean=clean.replace(/\s+/g,' ');
 const clauses=clean.split(/\s*(?:[;.!?]|\bve\b|,\s+(?=[a-z]))\s*/).filter(Boolean);
 if(!clauses.length||clauses.length>4)return fail('Bir mesajda en fazla dört desteklenen değişikliği belirtin.');
 const next=structuredClone(current);const changes:Change[]=[];const keys=new Set<string>();
 function set(key:string,label:string,before:string,after:string,apply:()=>void){
  if(keys.has(key))throw new Error('Aynı koşul için iki farklı ifade var. Tek bir değer belirterek yeniden yazın.');
  keys.add(key);if(before!==after){apply();changes.push({key,label,before,after})}
 }
 try{for(const clause of clauses){let m:RegExpMatchArray|null;
  if((m=clause.match(/^(?:kanal(?: suyu)?|su) (\d+) gun (daha )?(?:gecikecek|geciksin|gecikti)$/))){
   const value=Number(m[1])+(m[2]?current.delay:0);
   if(value>10)throw new Error(`Toplam gecikme ${value} gün oluyor. Bu motor 0–10 günlük gecikmeyi destekliyor; aralık içinde bir senaryo belirtin.`);
   set('delay','Kanal gecikmesi',`${current.delay} gün`,`${value} gün`,()=>{next.delay=value});
  }else if(/^(?:traktor(?:le aktarim)?|aktarim) (?:yok|kullanilamiyor|musait degil|kapali|arizali|calismiyor)$/.test(clause)){
   set('transfer_enabled','Traktörle aktarım',current.transfer_enabled?'Mümkün':'Kapalı','Kapalı',()=>{next.transfer_enabled=false});
  }else if(/^(?:traktor(?:le aktarim)?|aktarim) (?:var|musait|kullanilabilir|acik)$/.test(clause)){
   set('transfer_enabled','Traktörle aktarım',current.transfer_enabled?'Mümkün':'Kapalı','Mümkün',()=>{next.transfer_enabled=true});
  }else if(/^kanal suyu (?:yariya (?:insin|dussun|dustu)|yuzde 50 (?:azalsin|azaldi))$/.test(clause)){
   set('canal_volume_m3','Kanal suyu bütçesi',`${fmt(current.canal_volume_m3)} m³`,`${fmt(current.canal_volume_m3/2)} m³`,()=>{next.canal_volume_m3=current.canal_volume_m3/2});
  }else if((m=clause.match(/^kanal suyu (\d+) (?:m3|metrekup)(?: olsun| olacak)?$/))){
   const value=Number(m[1]);if(value>100000)throw new Error('Bu prototipte kanal bütçesi en fazla 100.000 m³ olabilir.');
   set('canal_volume_m3','Kanal suyu bütçesi',`${fmt(current.canal_volume_m3)} m³`,`${fmt(value)} m³`,()=>{next.canal_volume_m3=value});
  }else if((m=clause.match(/^(?:mazot|dizel)(?: fiyati)? (\d+(?:,\d+)?) (?:tl|lira)(?: olsun)?$/))){
   const value=Number(m[1].replace(',','.'));if(value>500)throw new Error('Bu prototipte dizel fiyatı 0–500 TL/L aralığında olmalı.');
   set('diesel_try_l','Dizel fiyatı',`${fmt(Number(current.economics.diesel_try_l))} TL/L`,`${fmt(value)} TL/L`,()=>{next.economics.diesel_try_l=value});
  }else if((m=clause.match(/^(?:gunluk )?calisma(?: suresi)? (\d+) saat(?: olsun)?$/))){
   const value=Number(m[1]);if(value<2||value>24)throw new Error('Günlük çalışma 2–24 saat aralığında olmalı.');
   set('daily_hours','Günlük çalışma',`${current.daily_hours} saat`,`${value} saat`,()=>{next.daily_hours=value});
  }else{
   return fail('Bu ifadeyi güvenle bir model girdisine çeviremedim. Bu demo; gecikme, traktör, kanal suyu, dizel fiyatı ve günlük çalışma ifadelerini destekliyor. Örneklerden birini kullanın. Hiçbir koşul değiştirilmedi. Gübre, hastalık ve saatlik ekipman takvimi için hesap bağlantısı henüz yok.');
  }
 }}catch(e){return fail((e as Error).message)}
 return changes.length?{ok:true,query:next,changes}:fail('Belirttiğiniz koşul mevcut senaryoyla aynı. Başka bir değişiklik deneyin.');
}
