"""Local demo API. Token identities come from server configuration, not client role claims."""
from __future__ import annotations
import json,os,secrets
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,Depends,Header,HTTPException
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .engine import Engine,SKILLS
from .store import Store,Principal

class Question(BaseModel):
    question:str=Field(min_length=1,max_length=2000)
    companies:list[str]=Field(default_factory=list,max_length=4)
    year:int|None=Field(default=None,ge=2000,le=2100)
    skill:str|None=None
    use_model:bool=False
class Feedback(BaseModel):
    trace_id:str
    category:Literal['retrieval','intent','generation','knowledge_gap','data_quality']
    note:str=Field(max_length=2000)
class Doc(BaseModel):
    topic:str=Field(min_length=1,max_length=100)
    version:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=150)
    body:str=Field(min_length=1,max_length=100000)
    department:str='research'
    source:str=Field(default='用户新增/待审核',max_length=500)
    valid_from:str='2020-01-01'
    valid_until:str|None=None
class Decision(BaseModel):
    decision:Literal['accepted_for_development','rejected']

def create_app(db_path=None,tokens=None):
    app=FastAPI(title='企明｜照明产业链经营与财报 Agent',version='1.0.0')
    store=Store(db_path or os.getenv('QIMING_DB','workspace/qiming.db'))
    engine=Engine(store);app.state.engine=engine;app.state.store=store
    configured=os.getenv('QIMING_API_TOKENS','')
    if tokens is None:
        tokens=json.loads(configured) if configured else {
         'local-analyst':{'user_id':'analyst','departments':['research'],'role':'analyst'},
         'local-editor':{'user_id':'editor','departments':['research','procurement'],'role':'editor'},
         'local-reviewer':{'user_id':'reviewer','departments':['research','procurement'],'role':'editor'}}
    identities={k:Principal(v['user_id'],v.get('tenant','demo'),tuple(v['departments']),v['role']) for k,v in tokens.items()}
    def auth(authorization:str|None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):raise HTTPException(401,'需要Bearer token')
        supplied=authorization[7:]
        for token,p in identities.items():
            if secrets.compare_digest(supplied,token):return p
        raise HTTPException(401,'无效token')
    @app.exception_handler(ValueError)
    async def value_error(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,content={'detail':str(exc)})
    @app.exception_handler(PermissionError)
    async def permission_error(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403,content={'detail':str(exc)})
    @app.get('/api/health')
    def health():return {'status':'ok','local_demo':not bool(configured),'external_model_enabled':engine.gateway.chat_ready,'embedding_enabled':engine.gateway.embedding_ready}
    @app.get('/api/catalog')
    def catalog(p:Principal=Depends(auth)):
        return {'companies':engine.finance.sources,'skills':SKILLS,'user':{'id':p.user_id,'role':p.role,'departments':p.departments},'fact_count':len(engine.finance.facts)}
    @app.post('/api/ask')
    def ask(q:Question,p:Principal=Depends(auth)):return engine.ask(p,**q.model_dump())
    @app.get('/api/snapshot/{company}/{year}')
    def snapshot(company:str,year:int,p:Principal=Depends(auth)):return engine.finance.snapshot(company,year)
    @app.get('/api/export/{company}/{year}')
    def export(company:str,year:int,p:Principal=Depends(auth)):
        return Response('\ufeff'+engine.finance.csv([company],year),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="financial-{company}-{year}.csv"'})
    @app.get('/api/documents')
    def documents(p:Principal=Depends(auth)):return store.visible(p,include_pending=True)
    @app.post('/api/documents')
    def add_document(doc:Doc,p:Principal=Depends(auth)):return {'id':store.add_document(p,doc.model_dump()),'status':'pending_review'}
    @app.post('/api/documents/{did}/publish')
    def publish(did:str,p:Principal=Depends(auth)):return {'id':store.publish(p,did),'status':'active'}
    @app.get('/api/traces')
    def traces(p:Principal=Depends(auth)):return store.traces(p)
    @app.post('/api/feedback')
    def feedback(f:Feedback,p:Principal=Depends(auth)):return {'id':store.feedback(p,f.trace_id,f.category,f.note),'status':'pending_review'}
    @app.get('/api/proposals')
    def proposals(p:Principal=Depends(auth)):return store.proposals(p)
    @app.post('/api/proposals/{pid}/review')
    def review(pid:str,d:Decision,p:Principal=Depends(auth)):return store.review_proposal(p,pid,d.decision)
    static=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')
    @app.get('/')
    def home():return FileResponse(static/'index.html')
    return app
app=create_app()
