"""Whole-runbook Chroma retrieval. Scores are distances, not truth probabilities."""
import hashlib
import json
import threading
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def load_catalog(directory):
    catalog={}
    for file in sorted(Path(directory).glob('*.md')):
        text=file.read_text(encoding='utf-8')
        catalog[file.stem]={'runbook_id':file.stem,'title':text.splitlines()[0].lstrip('# '),
            'file':'data/runbooks/'+file.name,'uri':'runbook://incident-response/'+file.stem,
            'content_hash':digest(text),'text':text}
    return catalog


class RunbookRetriever:
    def __init__(self,directory,index_path,embedder,max_distance=.65,secondary_margin=.035):
        self.directory=Path(directory);self.index_path=Path(index_path);self.embedder=embedder
        self.max_distance=max_distance;self.secondary_margin=secondary_margin
        self.catalog={};self.collection=None;self.lock=threading.RLock()
        self.stats={'document_tokens':0,'query_tokens':0,'embedding_requests':0,'cache_hits':0}

    def _embed(self,texts,kind):
        cache=self.index_path/'embedding_cache';cache.mkdir(parents=True,exist_ok=True)
        vectors=[None]*len(texts);missing=[]
        for i,text in enumerate(texts):
            file=cache/(digest(self.embedder.model+'\n'+text)+'.json')
            if file.exists():
                vectors[i]=json.loads(file.read_text());self.stats['cache_hits']+=1
            else:missing.append((i,text,file))
        if missing:
            generated,usage=self.embedder.embed([text for _,text,_ in missing])
            if len(generated)!=len(missing):raise ValueError('embedding_count_mismatch')
            self.stats[kind+'_tokens']+=usage.get('tokens',0)
            self.stats['embedding_requests']+=usage.get('requests',1)
            for (i,_,file),vector in zip(missing,generated):
                vectors[i]=vector;temporary=file.with_suffix('.tmp')
                temporary.write_text(json.dumps(vector));temporary.replace(file)
        return vectors

    def prepare(self):
        with self.lock:
            self.catalog=load_catalog(self.directory)
            if not self.catalog:raise ValueError('catalog_unavailable')
            self.index_path.mkdir(parents=True,exist_ok=True)
            client=chromadb.PersistentClient(path=str(self.index_path/'chroma'),
                                            settings=Settings(anonymized_telemetry=False))
            # Explicit vectors avoid Chroma's default model download or another provider.
            self.collection=client.get_or_create_collection(
                name='runbooks-'+digest(self.embedder.model)[:16],embedding_function=None,
                configuration={'hnsw':{'space':'cosine'}})
            existing=self.collection.get(include=['metadatas'])
            old=dict(zip(existing['ids'],existing['metadatas']))
            changed=[r for key,r in self.catalog.items() if (old.get(key) or {}).get('content_hash')!=r['content_hash']]
            if changed:
                vectors=self._embed([r['text'] for r in changed],'document')
                self.collection.upsert(ids=[r['runbook_id'] for r in changed],embeddings=vectors,
                    documents=[r['text'] for r in changed],
                    metadatas=[{k:v for k,v in r.items() if k!='text'} for r in changed])
            manifest={'index_version':1,'model':self.embedder.model,'distance':'cosine',
                'documents':{key:r['content_hash'] for key,r in self.catalog.items()}}
            (self.index_path/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            return {'documents':len(self.catalog),'updated_documents':len(changed),**self.stats}

    def search(self,question,monitoring_results,max_results=2,retry=False):
        started=time.perf_counter()
        with self.lock:
            before=dict(self.stats)
            try:
                if self.collection is None:self.prepare()
                queries=[]
                for row in monitoring_results:
                    alerts='; '.join(row.get('alerts',[]))
                    # Separate service alerts so a CPU phrase in the shared question
                    # cannot dominate another service's memory symptoms.
                    text=question+'\n관측 알림: '+alerts if retry else (alerts or question)
                    queries.append((row['system_name'],text))
                queries.append(('',question))
                vectors=self._embed([text for _,text in queries],'query')
                result=self.collection.query(query_embeddings=vectors,n_results=len(self.catalog),
                    ids=list(self.catalog),include=['metadatas','distances'])
                primary=[];secondary=[];candidates=[]
                for i,(target,text) in enumerate(queries):
                    rows=[]
                    for key,meta,distance in zip(result['ids'][i],result['metadatas'][i],result['distances'][i]):
                        if key not in self.catalog or meta['content_hash']!=self.catalog[key]['content_hash']:
                            continue
                        row={**meta,'distance':float(distance),'targets':[target] if target else []}
                        rows.append(row);candidates.append({**row,'query':text})
                    accepted=[r for r in rows if r['distance']<=self.max_distance]
                    if accepted:
                        (primary if target or len(queries)==1 else secondary).append(accepted[0])
                        secondary.extend(r for r in accepted[1:] if r['distance']-accepted[0]['distance']<=self.secondary_margin)
                hits={}
                for row in sorted(primary,key=lambda r:r['distance'])+sorted(secondary,key=lambda r:r['distance']):
                    key=row['runbook_id']
                    if key in hits:
                        hits[key]['targets']=sorted(set(hits[key]['targets']+row['targets']))
                    elif len(hits)<min(max(0,max_results),2):hits[key]=dict(row)
                selected=list(hits.values())
                covered={t for row in selected for t in row['targets']}
                return {'status':'ok' if selected else 'not_found','hits':selected,'candidates':candidates,
                    'uncovered_targets':[t for t,_ in queries if t and t not in covered],
                    'seconds':time.perf_counter()-started,
                    'usage':{key:self.stats[key]-before[key] for key in self.stats}}
            except Exception as exc:
                return {'status':'catalog_unavailable' if not self.catalog else 'search_error',
                    'hits':[],'error_type':type(exc).__name__,'seconds':time.perf_counter()-started,
                    'usage':{key:self.stats[key]-before[key] for key in self.stats}}
