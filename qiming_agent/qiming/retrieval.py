"""Small-corpus hybrid retrieval, adapted from 文枢 BM25/RRF architecture.
Default BM25 is genuinely lexical. Dense semantic retrieval is used ONLY when
an embedding endpoint is explicitly configured; it is never simulated as RAG.
"""
from __future__ import annotations
import hashlib, math, re
from collections import Counter

def tokenize(text:str)->list[str]:
    latin=re.findall(r'[a-z0-9]+',text.lower())
    chinese=re.findall(r'[\u4e00-\u9fff]+',text)
    # Offline deterministic segmentation; not a learned embedding model.
    return latin+[s[i:i+2] for s in chinese for i in range(max(1,len(s)-1))]

def chunks(doc:dict):
    result=[];parts=re.split(r'\n\s*\n',doc['body']);buffer=''
    for part in parts:
        if len(buffer)+len(part)>650 and buffer:
            result.append(buffer);buffer=''
        buffer+=part+'\n\n'
    if buffer:result.append(buffer)
    return [dict(id=f'{doc["id"]}:{i}',doc_id=doc['id'],title=doc['title'],text=t.strip(),
      source=doc['source'],version=doc['version'],department=doc['department'],
      topic=doc['topic'],sha256=doc['sha256']) for i,t in enumerate(result)]

def bm25(query:str,items:list[dict]):
    if not items:return []
    corpus=[Counter(tokenize(x['title']+' '+x['text'])) for x in items]
    terms=set(tokenize(query));N=len(corpus);avg=sum(sum(x.values()) for x in corpus)/N or 1
    df={t:sum(t in d for d in corpus) for t in terms};scores=[]
    for item,d in zip(items,corpus):
        length=sum(d.values());score=0.
        for t in terms:
            tf=d.get(t,0)
            if tf:score+=math.log(1+(N-df[t]+.5)/(df[t]+.5))*tf*2.5/(tf+1.5*(.25+.75*length/avg))
        if score>0:scores.append((item['id'],score))
    return sorted(scores,key=lambda x:(-x[1],x[0]))

def rrf(rankings:list[list[tuple[str,float]]],k=60):
    scores={}
    for ranking in rankings:
        for rank,(idx,_) in enumerate(ranking,1):scores[idx]=scores.get(idx,0)+1/(k+rank)
    return sorted(scores.items(),key=lambda x:(-x[1],x[0]))

def cosine(a,b):
    if len(a)!=len(b) or not a:raise ValueError('向量维度不一致')
    den=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    return sum(x*y for x,y in zip(a,b))/den if den else 0.

class Retriever:
    def __init__(self,embedding=None):self.embedding=embedding;self.cache={}
    def search(self,query,documents,top_k=4):
        # Callers supply already-authorized and active documents. Never retrieve then trim ACLs.
        items=[c for d in documents for c in chunks(d)]
        keyword=bm25(query,items);rankings=[keyword];mode='BM25（离线关键词）';warnings=[]
        if self.embedding and items:
            try:
                missing=[x for x in items if x['sha256']+x['id'] not in self.cache]
                if missing:
                    vectors=self.embedding.embed([x['text'] for x in missing])
                    if len(vectors)!=len(missing):raise ValueError('embedding数量不匹配')
                    for x,v in zip(missing,vectors):self.cache[x['sha256']+x['id']]=v
                q=self.embedding.embed([query])[0]
                dense=sorted([(x['id'],cosine(q,self.cache[x['sha256']+x['id']])) for x in items],key=lambda x:-x[1])
                rankings.append([x for x in dense if x[1]>.25]);mode='BM25 + 语义向量 + RRF'
            except Exception as exc:
                warnings.append('向量服务不可用，明确降级到BM25；未把该结果算作语义检索成功。')
        ranked=rrf(rankings) if len(rankings)>1 else keyword
        byid={x['id']:x for x in items}
        return [byid[i]|{'score':score} for i,score in ranked[:min(max(top_k,1),8)]],mode,warnings

def verify_evidence(evidence,documents):
    if not evidence:return False,['没有可用证据，不可标记为已验证答案']
    authoritative={d['id']:d for d in documents};issues=[]
    for e in evidence:
        d=authoritative.get(e['doc_id'])
        if not d:issues.append('引用无权限、已失效或不存在')
        elif e['sha256']!=d['sha256'] or e['text'] not in d['body']:issues.append('引用内容或版本与事实库不一致')
    return not issues,issues
