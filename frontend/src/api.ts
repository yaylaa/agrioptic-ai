export async function api<T>(url:string,init?:RequestInit):Promise<T>{
 const response=await fetch(url,init); let data;
 try{data=await response.json()}catch{throw new Error('Sunucudan geçerli yanıt alınamadı. API bağlantısını kontrol edin.')}
 if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'İstek doğrulanamadı. Girdi ve tarih aralığını kontrol edin.');
 return data;
}
export function download(name:string,data:unknown){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
export function csv(name:string,rows:Record<string,unknown>[]){if(!rows.length)return;const keys=Object.keys(rows[0]);const escape=(v:unknown)=>'"'+String(v??'').replaceAll('"','""')+'"';const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\uFEFF'+[keys.map(escape).join(','),...rows.map(r=>keys.map(k=>escape(r[k])).join(','))].join('\n')],{type:'text/csv;charset=utf-8;'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
