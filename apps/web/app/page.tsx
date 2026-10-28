"use client";

import { useEffect, useState } from "react";
import ArticleImage from "./ArticleImage";
import type { FormEvent } from "react";

const API = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
type Project = {id:number; name:string; domain:string; keywords?:string|null};
type Run = {id:number; project_id:number; project_name:string; status:string; started_at:string|null};
type Article = {id:number; run_id:number; project_name?:string; title:string; content:string; llm_provider:string; llm_model:string; publication_status?:string; wordpress_post_id?:number|null};
type Brief = {summary:string; key_topics:string[]; key_findings:string[]; content_gaps:string[]; source_notes:string[]; recommended_angle:string; sources?:{source_id:number;title:string;url:string}[]};
type Research = {id:number;project_id:number;query:string;status:string;unique_count:number;target_count:number;brief?:Brief|null;error?:string|null;content_job_id?:string|null;brief_job_id?:string|null;article_job_id?:string|null;report?:{results?:{collection_order:number;serp_page:number;title:string;url:string}[]}|null};
type ResearchPage = {id:number;title:string;url:string;content_text:string;char_count:number};
type Phase = "content"|"brief"|"article";
type JobState = {job_id:string;status:string;error?:string|null};
const labels:Record<string,string>={queued:"W kolejce",started:"W trakcie",running:"W trakcie",scheduled:"Zaplanowane",deferred:"Oczekujące",completed:"Zakończone",finished:"Zakończone",failed:"Błąd",stopped:"Przerwane",canceled:"Anulowane",expired:"Status wygasł",pending:"Wysyłanie szkicu",draft_created:"Szkic w WordPressie",not_published:"Niewysłane"};
const active=(s?:string)=>["queued","started","running","scheduled","deferred"].includes(s||"");
const color=(s:string)=>["completed","finished","draft_created"].includes(s)?"green":["failed","stopped","canceled"].includes(s)?"red":"amber";
async function request<T>(path:string,options?:RequestInit):Promise<T>{
 const response=await fetch(`${API}${path}`,{cache:"no-store",...options});
 if(!response.ok){const data=await response.json().catch(()=>null);throw new Error(typeof data?.detail==="string"?data.detail:`Błąd API: HTTP ${response.status}`);}
 return response.json();
}

export default function Home(){
 const [projects,setProjects]=useState<Project[]>([]);
 const [runs,setRuns]=useState<Run[]>([]);
 const [articles,setArticles]=useState<Article[]>([]);
 const [selectedRun,setSelectedRun]=useState<number|null>(null);
 const [researchId,setResearchId]=useState<number|null>(null);
 const [research,setResearch]=useState<Research|null>(null);
 const [pages,setPages]=useState<ResearchPage[]>([]);
 const [jobs,setJobs]=useState<Partial<Record<Phase,JobState>>>({});
 const [idInput,setIdInput]=useState("7");
 const [name,setName]=useState(""); const [domain,setDomain]=useState(""); const [keywords,setKeywords]=useState("");
 const [busy,setBusy]=useState(false); const [connected,setConnected]=useState(false);
 const [error,setError]=useState(""); const [notice,setNotice]=useState("");
 const [version,setVersion]=useState(0);
 const refresh=()=>setVersion(v=>v+1);

 useEffect(()=>{
  let alive=true; let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{
    const [p,r,a]=await Promise.all([request<Project[]>("/projects"),request<Run[]>("/runs"),request<Article[]>("/articles")]);
    if(alive){setProjects(p);setRuns(r);setArticles(a);setConnected(true);}
   }catch(e){if(alive){setConnected(false);setError(e instanceof Error?e.message:"Brak połączenia z API");}}
   finally{if(alive)timer=setTimeout(()=>void poll(),5000);}
  }
  void poll();return()=>{alive=false;clearTimeout(timer);};
 },[version]);

 useEffect(()=>{
  if(researchId===null)return;
  let alive=true;let timer:ReturnType<typeof setTimeout>;
  async function poll(){
   try{
    const [r,p]=await Promise.all([request<Research>(`/research/${researchId}`),request<ResearchPage[]>(`/research/${researchId}/pages`)]);
    const next:Partial<Record<Phase,JobState>>={};
    const fields:[Phase,string|null|undefined][]=[["content",r.content_job_id],["brief",r.brief_job_id],["article",r.article_job_id]];
    await Promise.all(fields.map(async([phase,id])=>{if(id)next[phase]=await request<JobState>(`/jobs/${id}`);}));
    if(alive){setResearch(r);setPages(p);setJobs(next);}
   }catch(e){if(alive)setError(e instanceof Error?e.message:"Błąd odczytu researchu");}
   finally{if(alive)timer=setTimeout(()=>void poll(),3000);}
  }
  void poll();return()=>{alive=false;clearTimeout(timer);};
 },[researchId,version]);

 async function action(task:()=>Promise<void>){
  if(busy)return;setBusy(true);setError("");setNotice("");
  try{await task();}catch(e){setError(e instanceof Error?e.message:"Operacja nie powiodła się");}finally{setBusy(false);}
 }
 async function createProject(e:FormEvent<HTMLFormElement>){
  e.preventDefault();await action(async()=>{
   await request("/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name.trim(),domain:domain.trim(),keywords:keywords.trim()||null})});
   setName("");setDomain("");setKeywords("");setNotice("Dodano projekt.");refresh();
  });
 }
 async function openResearch(){
  const id=Number(idInput);if(!Number.isSafeInteger(id)||id<1){setError("Podaj poprawny numer researchu.");return;}
  await action(async()=>{
   const [r,p]=await Promise.all([request<Research>(`/research/${id}`),request<ResearchPage[]>(`/research/${id}/pages`)]);
   setResearchId(id);setResearch(r);setPages(p);setJobs({});setNotice(`Wczytano research #${id}.`);refresh();
  });
 }
 async function startResearch(p:Project){
  if(!window.confirm(`Uruchomić nowy research dla „${p.name}”? To wywoła Serper.`))return;
  await action(async()=>{
   const r=await request<{research_id:number}>(`/projects/${p.id}/research/start`,{method:"POST"});
   setResearch(null);setPages([]);setJobs({});setResearchId(r.research_id);setIdInput(String(r.research_id));setNotice(`Research #${r.research_id} dodano do kolejki.`);refresh();
  });
 }
 async function startPlainArticle(p:Project){
  if(!window.confirm(`Wygenerować artykuł dla „${p.name}” BEZ briefu? To wywoła model.`))return;
  await action(async()=>{const r=await request<{id:number}>(`/runs/start?project_id=${p.id}`,{method:"POST"});setSelectedRun(r.id);setNotice(`Zadanie #${r.id} dodano do kolejki.`);refresh();});
 }
 async function startPhase(phase:Phase){
  if(!research)return;const id=research.id;
  const question=phase==="article"?"Wygenerować artykuł z briefu? To wywoła Groq, ale nie wyśle tekstu do WordPressa.":phase==="brief"?"Wygenerować brief? To wywoła Groq. Po sukcesie zastąpi poprzedni brief.":"Pobrać treści pierwszych 10 wyników?";
  if(!window.confirm(question))return;
  await action(async()=>{
   const route=phase==="content"?"fetch-content":phase;
   const r=await request<{job_id:string;run_id?:number}>(`/research/${id}/${route}`,{method:"POST"});
   setJobs(current=>({...current,[phase]:{job_id:r.job_id,status:"queued"}}));
   if(r.run_id)setSelectedRun(r.run_id);
   setNotice("Zadanie przyjęto. Status odświeża się automatycznie.");refresh();
  });
 }
 async function sendDraft(a:Article){
  if(!window.confirm(`Utworzyć szkic w WordPressie: „${a.title}”?`))return;
  await action(async()=>{
   await request(`/articles/${a.id}/publish`,{method:"POST"});
   setArticles(current=>current.map(x=>x.id===a.id?{...x,publication_status:"pending"}:x));
   setNotice("Wysyłanie szkicu dodano do kolejki.");refresh();
  });
 }
 async function removeArticle(a:Article){
  if(!window.confirm(`Usunąć artykuł #${a.id} „${a.title}” z lokalnej bazy?\n\nOperacji nie można cofnąć. Historia zadania pozostanie.\nWpis w WordPressie NIE zostanie usunięty.`))return;
  await action(async()=>{
   await request(`/articles/${a.id}`,{method:"DELETE"});
   setArticles(current=>current.filter(x=>x.id!==a.id));setNotice(`Usunięto lokalny artykuł #${a.id}. WordPress pozostaje bez zmian.`);refresh();
  });
 }
 async function removeResearch(){
  if(!research)return;const id=research.id;
  if(!window.confirm(`Usunąć research #${id} dla frazy „${research.query}”?\n\nZnikną wyniki SERP, brief i pobrane strony.\nArtykuły, historia zadań i kopie briefu użyte do ich tworzenia pozostaną.\n\nOperacji nie można cofnąć.`))return;
  await action(async()=>{
   await request(`/research/${id}`,{method:"DELETE"});
   setResearchId(null);setResearch(null);setPages([]);setJobs({});setIdInput("");
   setNotice(`Usunięto research #${id}. Wygenerowane artykuły pozostały.`);refresh();
  });
 }
 const processing=Object.values(jobs).some(j=>active(j?.status));
 const blocked=busy||!connected||processing;
 const visibleArticles=selectedRun===null?articles:articles.filter(a=>a.run_id===selectedRun);
 const selectedStatus=runs.find(r=>r.id===selectedRun)?.status;

 return <main className="dashboard">
  <header><div><span className="muted">PANEL TREŚCI</span><h1>AI Publisher</h1><p>Research → brief → artykuł → szkic WordPress</p></div><span className={`badge ${connected?"green":"amber"}`}>{connected?"API połączone":"Brak połączenia z API"}</span></header>
  {error&&<div className="message red" role="alert">{error}<button className="secondary" onClick={()=>setError("")}>Zamknij</button></div>}
  {notice&&<div className="message green" role="status">{notice}</div>}
  <section className="stats"><div className="panel">Projekty <b>{projects.length}</b></div><div className="panel">Artykuły <b>{articles.length}</b></div><div className="panel">Aktywne generowania <b>{runs.filter(r=>active(r.status)).length}</b></div></section>
  <section className="panel"><h2>Otwórz zapisany research</h2><div className="actions"><label>Numer researchu<input type="number" min="1" value={idInput} onChange={e=>setIdInput(e.target.value)}/></label><button disabled={blocked} onClick={()=>void openResearch()}>Otwórz / odśwież</button></div></section>
  {research&&<section className="panel">
   <div className="actions"><h2>Research #{research.id}</h2><button className="danger" disabled={blocked||active(research.status)} onClick={()=>void removeResearch()}>Usuń research</button></div>
   <p>Fraza: {research.query}</p><div className="actions"><span className={`badge ${color(research.status)}`}>{labels[research.status]||research.status}</span><span>Wyniki: {research.unique_count}/{research.target_count}</span><span>Pobrane strony: {pages.length}</span></div>
   {research.error&&<p className="message red">{research.error}</p>}
   <div className="actions"><button className="secondary" disabled={blocked||research.status!=="completed"} onClick={()=>void startPhase("content")}>Pobierz treści 10 stron</button><button className="secondary" disabled={blocked||!pages.length||research.status!=="completed"} onClick={()=>void startPhase("brief")}>Generuj brief researchowy</button><button disabled={blocked||!research.brief||research.status!=="completed"} onClick={()=>void startPhase("article")}>Generuj artykuł z briefu</button></div>
   {(["content","brief","article"] as Phase[]).map(phase=>jobs[phase]&&<div className="job" key={phase}>{phase==="content"?"Pobieranie stron":phase==="brief"?"Generowanie briefu":"Generowanie artykułu"}: <span className={`badge ${color(jobs[phase]!.status)}`}>{labels[jobs[phase]!.status]||jobs[phase]!.status}</span>{jobs[phase]!.error&&<p className="message red">{jobs[phase]!.error}</p>}</div>)}
   {research.brief&&<div className="brief"><div className="message green">Brief jest zapisany w bazie.{active(jobs.brief?.status)?" Trwa tworzenie nowej wersji; poniżej poprzednia.":""}</div><h3>Brief researchowy</h3><p>{research.brief.summary}</p>{(["key_topics","key_findings","content_gaps","source_notes"] as const).map(key=><div key={key}><h4>{{key_topics:"Tematy",key_findings:"Ustalenia",content_gaps:"Luki w analizowanych fragmentach",source_notes:"Ograniczenia źródeł"}[key]}</h4><ul>{research.brief![key]?.map((t,i)=><li key={i}>{t}</li>)}</ul></div>)}<h4>Kierunek artykułu</h4><p>{research.brief.recommended_angle}</p><details><summary>Źródła briefu</summary>{research.brief.sources?.map(s=><p key={s.source_id}>[{s.source_id}] {s.title}<br/><span className="url">{s.url}</span></p>)}</details></div>}
   {!!pages.length&&<details><summary>Pobrane treści ({pages.length})</summary>{pages.map(p=><details key={p.id} className="page"><summary>{p.title} · {p.char_count} znaków</summary><p className="url">{p.url}</p><div className="content">{p.content_text}</div></details>)}</details>}
   {!!research.report?.results?.length&&<details><summary>Wyniki SERP</summary><div className="table-wrap"><table><thead><tr><th>#</th><th>SERP</th><th>Tytuł</th><th>Adres</th></tr></thead><tbody>{research.report.results.map((r,i)=><tr key={i}><td>{r.collection_order}</td><td>{r.serp_page}</td><td>{r.title}</td><td className="url">{r.url}</td></tr>)}</tbody></table></div></details>}
  </section>}
  <div className="columns"><section className="panel"><h2>Nowy projekt</h2><form onSubmit={createProject}><label>Nazwa<input required value={name} onChange={e=>setName(e.target.value)}/></label><label>Domena<input required value={domain} onChange={e=>setDomain(e.target.value)}/></label><label>Słowa kluczowe<textarea rows={3} value={keywords} onChange={e=>setKeywords(e.target.value)}/></label><button disabled={busy||!connected}>Dodaj projekt</button></form></section><section className="panel"><h2>Twoje projekty</h2>{projects.map(p=><div className="project" key={p.id}><h3>{p.name}</h3><p className="muted">{p.domain}</p><p>{p.keywords}</p><div className="actions"><button disabled={blocked||!p.keywords?.trim()} onClick={()=>void startResearch(p)}>Nowy research</button><button className="secondary" disabled={busy||!connected} onClick={()=>void startPlainArticle(p)}>Artykuł bez briefu</button></div></div>)}</section></div>
  <section className="panel"><h2>Historia generowania</h2><p className="muted">Usunięcie artykułu nie usuwa historii jego zadania.</p><div className="table-wrap"><table><thead><tr><th>ID</th><th>Projekt</th><th>Status</th><th>Utworzono</th><th>Podgląd</th></tr></thead><tbody>{runs.map(r=><tr key={r.id}><td>#{r.id}</td><td>{r.project_name}</td><td><span className={`badge ${color(r.status)}`}>{labels[r.status]||r.status}</span></td><td>{r.started_at?new Date(r.started_at).toLocaleString("pl-PL"):"—"}</td><td><button className="secondary" onClick={()=>setSelectedRun(r.id)}>Artykuły zadania</button></td></tr>)}</tbody></table></div></section>
  <section className="panel"><div className="actions"><h2>{selectedRun===null?"Wszystkie artykuły":`Artykuły zadania #${selectedRun}`}</h2>{selectedRun!==null&&<button className="secondary" onClick={()=>setSelectedRun(null)}>Pokaż wszystkie</button>}</div>
   {!visibleArticles.length&&<p>{selectedStatus==="failed"?"Generowanie zakończyło się błędem. Sprawdź logi workera.":selectedRun!==null&&active(selectedStatus)?"Generowanie trwa. Artykuł pojawi się automatycznie.":"Brak artykułów. Jeśli wcześniej usunięto tekst, historia zadania nadal pozostaje."}</p>}
   {visibleArticles.map(a=><article key={a.id}><p className="muted">Artykuł #{a.id} · zadanie #{a.run_id} · {a.project_name} · {a.llm_provider}/{a.llm_model}</p><h3>{a.title}</h3><ArticleImage articleId={a.id} /><details><summary>Pokaż treść</summary><div className="article-content">{a.content}</div></details><div className="actions"><span className={`badge ${color(a.publication_status||"not_published")}`}>{labels[a.publication_status||"not_published"]||a.publication_status}</span><button disabled={busy||!connected||["pending","draft_created"].includes(a.publication_status||"")} onClick={()=>void sendDraft(a)}>Wyślij szkic do WordPressa</button><button className="danger" disabled={busy||!connected||a.publication_status==="pending"} onClick={()=>void removeArticle(a)}>Usuń artykuł</button>{a.wordpress_post_id&&<span>ID WordPress: {a.wordpress_post_id}</span>}</div><p className="muted">Usuwanie dotyczy lokalnej bazy. Nie usuwa wpisu w WordPressie.</p></article>)}
  </section>
  <style jsx>{`
   .dashboard{max-width:1200px;margin:auto;padding:32px 22px 70px;font-family:Arial,sans-serif;color:#172033}header,.actions{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}header{margin-bottom:24px}h1{font-size:36px;margin:8px 0}h2{font-size:21px;margin:0 0 16px}h3{margin:10px 0}h4{margin:18px 0 8px}p,li{line-height:1.65}.muted{color:#64748b;font-size:13px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.stats b{display:block;font-size:30px;margin-top:10px}.panel{background:white;border:1px solid #e2e8f0;border-radius:16px;padding:24px;margin-bottom:24px}.columns{display:grid;grid-template-columns:340px 1fr;gap:24px}form{display:grid;gap:16px}label{font-size:13px;font-weight:600}input,textarea{box-sizing:border-box;display:block;width:100%;padding:11px;margin-top:7px;border:1px solid #cbd5e1;border-radius:8px;background:#f8fafc;color:#172033;font:inherit}button{border:0;border-radius:8px;padding:11px 15px;background:#4f46e5;color:white;font-weight:600;cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}.secondary{background:#eef2ff;color:#4338ca}.danger{background:#fee2e2;color:#991b1b;border:1px solid #fecaca}.danger:hover:not(:disabled){background:#fecaca}.badge{display:inline-block;border-radius:20px;padding:7px 10px;font-size:12px}.green{background:#dcfce7;color:#166534}.amber{background:#fef3c7;color:#92400e}.red{background:#fee2e2;color:#991b1b}.message{padding:14px;border-radius:10px;margin:12px 0;overflow-wrap:anywhere}.actions{justify-content:flex-start;margin:16px 0}.project{padding:16px 0;border-bottom:1px solid #e2e8f0}.brief,.page{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin:16px 0}.job{margin:10px 0}.content{max-height:320px;overflow:auto;white-space:pre-wrap;line-height:1.7}.article-content{white-space:pre-wrap;line-height:1.8;margin:20px 0;overflow-wrap:anywhere}.url{overflow-wrap:anywhere;max-width:350px;font-size:12px}details{margin:16px 0}summary{cursor:pointer;font-weight:600}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}th,td{border-bottom:1px solid #edf2f7;padding:12px 8px;font-size:13px}th{color:#64748b}article{padding:18px 0;border-bottom:1px solid #e2e8f0}@media(max-width:800px){.columns{grid-template-columns:1fr}.dashboard{padding:22px 12px}.panel{padding:18px}}@media(max-width:480px){.stats{grid-template-columns:1fr}}
  `}</style>
 </main>;
}
