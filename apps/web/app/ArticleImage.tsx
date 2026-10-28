"use client";

import { useEffect, useRef, useState } from "react";
const API=(process.env.NEXT_PUBLIC_API_URL||"http://localhost:8000").replace(/\/$/,"");
type ImageState={status:string;image_url?:string|null;generation_id?:string;external_id?:string|null;model?:string|null;alt_text?:string;error?:string|null};
const active=(s:string)=>["queued","requesting","generating"].includes(s);
const labels:Record<string,string>={not_started:"Nie zlecono",queued:"W kolejce",requesting:"Wysyłanie zlecenia",generating:"Generowanie w AI Horde",ready:"Gotowa",failed:"Błąd"};
async function request<T>(path:string,options?:RequestInit):Promise<T>{
 const response=await fetch(API+path,{cache:"no-store",...options});
 const body=await response.json().catch(()=>null);
 if(!response.ok)throw new Error(typeof body?.detail==="string"?body.detail:`HTTP ${response.status}`);
 return body as T;
}
export default function ArticleImage({articleId}:{articleId:number}){
 const [state,setState]=useState<ImageState|null>(null);
 const [error,setError]=useState("");
 const [busy,setBusy]=useState(false);
 const [version,setVersion]=useState(0);
 const posting=useRef(false);
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{
  let current=true;let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{
    const result=await request<ImageState>(`/articles/${articleId}/image`);
    if(current&&!posting.current){setState(result);setError("");}
    if(current)timer=setTimeout(()=>void poll(),active(result.status)?5000:15000);
   }catch(e){if(current){setError(e instanceof Error?e.message:"Błąd pobierania grafiki");timer=setTimeout(()=>void poll(),10000);}}
  }
  void poll();return()=>{current=false;clearTimeout(timer);};
 },[articleId,version]);
 async function generate(){
  if(posting.current)return;
  const retry=state?.status==="failed";
  if(!window.confirm(retry?"Ponowić generowanie grafiki? To utworzy nowe zlecenie AI Horde. Jeśli poprzednie miało błąd połączenia, mogło nadal działać po stronie usługi.":"Wygenerować jedną ilustrację 512×512 w AI Horde? Do usługi zostanie wysłany tytuł artykułu, bez pełnej treści. Nie zmieni to tekstu ani WordPressa."))return;
  posting.current=true;setBusy(true);setError("");
  try{
   const result=await request<ImageState>(`/articles/${articleId}/image`,{method:"POST"});
   if(alive.current)setState(result);
  }catch(e){if(alive.current)setError(e instanceof Error?e.message:"Nie udało się zlecić obrazu");}
  finally{posting.current=false;if(alive.current){setBusy(false);setVersion(v=>v+1);}}
 }
 return <section className="image-box">
  <div className="image-heading"><h4>Ilustracja artykułu</h4><span className={`image-status ${state?.status==="ready"?"ok":state?.status==="failed"?"bad":""}`}>{state?labels[state.status]||state.status:"Sprawdzanie…"}</span></div>
  {error&&<p className="image-error" role="alert">{error}</p>}
  {state?.error&&<p className="image-error" role="alert">{state.error}</p>}
  {state&&active(state.status)&&<p role="status">Zlecenie jest w toku. Status odświeża się automatycznie. Nie trzeba ponownie klikać generowania. Limit oczekiwania po wysłaniu do AI Horde: 15 minut.</p>}
  {state?.status==="ready"&&state.image_url&&<figure>
   {/* eslint-disable-next-line @next/next/no-img-element */}
   <img src={API+state.image_url} alt={state.alt_text||"Ilustracja artykułu"} width={512} height={512}/>
   <figcaption>AI Horde · {state.model||"model niepodany"} · 512 × 512</figcaption>
   <p>Roboczy tekst alternatywny: {state.alt_text}</p>
  </figure>}
  <div className="image-actions">
   {state?.status!=="ready"&&<button disabled={busy||!state||active(state.status)} onClick={()=>void generate()}>{busy?"Zlecanie…":state?.status==="failed"?"Ponów generowanie grafiki":"Generuj grafikę"}</button>}
   <button className="image-refresh" disabled={busy} onClick={()=>setVersion(v=>v+1)}>Odśwież status</button>
  </div>
  <p className="image-note">Etap testowy: ręczna grafika do istniejącego artykułu. Nie jest automatycznie wysyłana do WordPressa. Usunięcie lokalnego artykułu usuwa również jego lokalną grafikę.</p>
  <style jsx>{`
   .image-box{margin:18px 0;padding:18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px}.image-heading,.image-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.image-heading{justify-content:space-between}h4{margin:0}p{font-size:13px;line-height:1.6;overflow-wrap:anywhere}.image-status{font-size:12px;padding:6px 10px;background:#fef3c7;color:#92400e;border-radius:20px}.ok{background:#dcfce7;color:#166534}.bad{background:#fee2e2;color:#991b1b}.image-error{color:#991b1b;background:#fee2e2;padding:10px;border-radius:8px}button{background:#4f46e5;color:white;border:0;border-radius:8px;padding:10px 14px;font-weight:600;cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}.image-refresh{background:#e2e8f0;color:#334155}figure{margin:16px 0}img{display:block;width:min(100%,512px);height:auto;border-radius:10px}figcaption,.image-note{color:#64748b;font-size:12px}.image-actions{margin-top:14px}
  `}</style>
 </section>;
}
