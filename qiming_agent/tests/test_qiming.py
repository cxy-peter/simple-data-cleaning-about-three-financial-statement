import copy,json
from decimal import Decimal as D
from pathlib import Path
import httpx,pytest
from fastapi.testclient import TestClient
from qiming.finance import Finance,ratio,growth
from qiming.ingest import number,find_header,import_rows,extract_pages
from qiming.store import Store,Principal
from qiming.engine import Engine,SKILLS
from qiming.retrieval import bm25,rrf,verify_evidence,Retriever
from qiming.model import ModelGateway
from qiming.download import validate_url,download_report
from qiming.app import create_app

DATA=Path(__file__).parents[1]/'qiming'/'data'
@pytest.fixture
def store():
    s=Store();yield s;s.close()
@pytest.fixture
def engine(store):return Engine(store)
@pytest.fixture
def analyst():return Principal('a')
@pytest.fixture
def editor():return Principal('e','demo',('research','procurement'),'editor')
@pytest.fixture
def reviewer():return Principal('r','demo',('research','procurement'),'editor')

@pytest.mark.parametrize('v,u,expected',[('1,234.50','元','1234.50'),('(2.5)','万元','-25000'),('—','元',None),('0','元','0'),('３.５','亿元','350000000')])
def test_number(v,u,expected):assert number(v,u)==(None if expected is None else D(expected))
@pytest.mark.parametrize('v,u',[('20%','元'),('abc','元'),('NaN','元'),('10','美元')])
def test_number_reject(v,u):
    with pytest.raises(ValueError):number(v,u)
def test_legacy_header_bug_fixed():assert find_header([['说明'],['日期'],['产品名称']])==2
def test_no_silent_first_header():
    with pytest.raises(ValueError):find_header([['不是表头']])
def test_duplicate_label_not_first_match():
    with pytest.raises(ValueError):import_rows([{'项目名称':'营业收入','2024':'10'},{'项目名称':'营业收入','2024':'12'}],'x',[2024],'is','元','test.csv')
def test_missing_not_zero():
    rows=import_rows([],'x',[2024],'is','元','test.csv');assert all(r['value'] is None for r in rows)
def test_consolidated_section_and_wrapped_label():
    pages=['合并资产负债表\n单位：元\n负债合计\n10.00\n11.00\n母公司资产负债表\n负债合计\n999.00\n999.00',
     '合并现金流量表\n单位：元\n经营活动产生的现金流\n量净额\n12.00\n9.00\n母公司现金流量表']
    facts,_=extract_pages(pages,'x','公司',2024,'x.pdf','x')
    assert next(r for r in facts if r['metric']=='liabilities' and r['year']==2024)['value']=='10.00'
    assert next(r for r in facts if r['metric']=='cfo' and r['year']==2024)['value']=='12.00'
    assert all(not f['verified'] for f in facts)
def test_substring_liability_not_accepted():
    f,_=extract_pages(['合并资产负债表\n单位：元\n流动负债合计\n8.00\n7.00\n负债合计\n10.00\n11.00\n母公司资产负债表'],'x','公司',2024,'x','x')
    assert next(x for x in f if x['metric']=='liabilities')['value']=='10.00'
@pytest.mark.parametrize('company,year',[('603195',2024),('603195',2023),('301362',2024),('301362',2023),('002429',2024),('002429',2023),('688368',2023),('688368',2022)])
def test_three_statement_reconciliation(company,year):
    assert all(x['status']=='pass' for x in Finance(DATA).reconcile(company,year))
def test_profit_not_parent_profit():
    v=Finance(DATA).values('688368',2023);assert v['profit']==D('-79172100.57') and v['parent_profit']==D('-91260032.42')
def test_negative_profit_ratio():assert Finance(DATA).snapshot('688368',2023)['derived']['经营现金流/合并净利润'] is None
def test_cash_is_not_cash_equivalents():
    v=Finance(DATA).values('603195',2024);assert v['cash']!=v['ending_cash']
@pytest.mark.parametrize('prev',[D(0),D(-10),None])
def test_yoy_nonpositive_base(prev):assert growth(D(20),prev) is None
def test_unknown_year():
    with pytest.raises(ValueError):Finance(DATA).snapshot('688368',2024)
def test_comparability_warning():assert '不生成综合排名' in Finance(DATA).compare(['603195','301362'],2024)['warning']
def test_source_fields():
    fs=Finance(DATA).facts
    assert len(fs)==168 and all(f['page']>0 and f['source_file'] and f['verified'] for f in fs)
def test_csv_source_and_exact_numeric():
    text=Finance(DATA).csv(['603195'],2024);assert '16830541086.13' in text and 'PDF页码' in text

def document(**extra):return {'title':'新方法','topic':'test','version':1,'body':'关于现金流的复核说明','source':'测试','department':'research'}|extra
def test_editor_required(store,analyst):
    with pytest.raises(PermissionError):store.add_document(analyst,document())
def test_other_department_rejected(store):
    with pytest.raises(PermissionError):store.add_document(Principal('e','demo',('research',),'editor'),document(department='procurement'))
def test_self_review_blocked(store,editor):
    d=store.add_document(editor,document())
    with pytest.raises(PermissionError):store.publish(editor,d)
def test_publish_replaces_old_atomically(store,editor,reviewer,analyst):
    old=store.add_document(editor,document(id='old'),status='active')
    new=store.add_document(editor,document(id='new',version=2,body='新版本信息'))
    assert [x['id'] for x in store.visible(analyst)]==['old']
    store.publish(reviewer,new)
    assert [x['id'] for x in store.visible(analyst)]==['new']
def test_expired_new_cannot_archive_old(store,editor,reviewer,analyst):
    store.add_document(editor,document(id='old'),status='active')
    n=store.add_document(editor,document(version=2,valid_until='2020-01-02'))
    with pytest.raises(ValueError):store.publish(reviewer,n)
    assert store.visible(analyst)[0]['id']=='old'
def test_downgrade_blocked(store,editor,reviewer):
    store.add_document(editor,document(version=3),status='active')
    d=store.add_document(editor,document(version=2))
    with pytest.raises(ValueError):store.publish(reviewer,d)
def test_tenant_isolation(store,editor):
    store.add_document(editor,document(),status='active')
    assert store.visible(Principal('other','tenantB'))==[]
def test_pending_hidden(store,editor,analyst):
    store.add_document(editor,document());assert not store.visible(analyst)
def test_expired_hidden(store,editor,analyst):
    store.add_document(editor,document(valid_until='2020-01-02'),status='active');assert not store.visible(analyst)
def test_evidence_none_fails_closed():assert verify_evidence([],[])[0] is False
def test_unknown_citation_fails():assert not verify_evidence([{'doc_id':'fake'}],[])[0]
def test_changed_source_fails():
    assert not verify_evidence([{'doc_id':'x','text':'改写的假内容','sha256':'a'}],[{'id':'x','body':'原文','sha256':'a'}])[0]
def test_rrf_accumulates():
    assert rrf([[('a',100),('b',90)],[('b',.9),('c',.8)]])[0][0]=='b'
def test_empty_retrieval():assert Retriever().search('天气',[])[0]==[]

def test_task_no_year_clarifies(engine,analyst):assert engine.ask(analyst,'公牛现金流如何')['status']=='needs_clarification'
def test_missing_year_refuses(engine,analyst):assert engine.ask(analyst,'晶丰明源2024年收入多少')['status']=='insufficient_data'
def test_unsupported_skill(engine,analyst):
    with pytest.raises(ValueError):engine.ask(analyst,'现金流',skill='send_money')
def test_cashflow_task(engine,analyst):
    r=engine.ask(analyst,'公牛2024年现金流分析')
    assert r['status']=='completed' and '37.30亿元' in r['answer'] and '原因尚不能' in r['answer']
    assert r['model_draft'] is None and r['plan']['skill']=='cashflow'
def test_compare_no_wrong_year(engine,analyst):assert engine.ask(analyst,'比较公牛和晶丰明源2024年')['status']=='insufficient_data'
def test_knowledge_excludes_procurement(engine,analyst):
    r=engine.ask(analyst,'供应商资料复核新版营业执照质量资质')
    assert not any(e['department']=='procurement' for e in r['evidence'])
def test_knowledge_editor_sees_new_not_old(engine,editor):
    r=engine.ask(editor,'供应商资料复核新版营业执照质量资质')
    assert any(e['doc_id']=='procurement-v2' for e in r['evidence'])
    assert not any(e['doc_id']=='procurement-v1' for e in r['evidence'])
def test_feedback_no_auto_mutation(engine,analyst,reviewer):
    trace=engine.ask(analyst,'公牛2024年现金流分析')['trace_id'];before=copy.deepcopy(SKILLS)
    pid=engine.store.feedback(analyst,trace,'data_quality','需要核对')
    engine.store.review_proposal(reviewer,pid,'accepted_for_development')
    assert before==SKILLS and engine.store.proposals(reviewer)[0]['status']=='accepted_for_development'
def test_trace_private(engine,analyst):
    engine.ask(analyst,'现金流和利润区别');assert not engine.store.traces(Principal('b'))
def test_external_disabled_degrades(engine,analyst):
    r=engine.ask(analyst,'公牛2024年现金流',use_model=True);assert r['model_draft'] is None and r['warnings']

def gateway_reply(payload):
    return ModelGateway('http://127.0.0.1:9000/v1','test','model','embed',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=payload)))
def test_model_valid_mock_structure():
    g=gateway_reply({'choices':[{'message':{'content':json.dumps({'text':'现金流变化仍需结合附注核查。','citations':['x']})}}]})
    assert g.explain('q',[{'id':'x','text':'原文'}],'结果')['citations']==['x']
def test_model_fake_citation_rejected():
    g=gateway_reply({'choices':[{'message':{'content':json.dumps({'text':'答案','citations':['notexist']})}}]})
    with pytest.raises(ValueError):g.explain('q',[{'id':'x','text':'原文'}],'结果')
def test_model_numbers_rejected():
    g=gateway_reply({'choices':[{'message':{'content':json.dumps({'text':'利润增加99亿元','citations':['x']})}}]})
    with pytest.raises(ValueError):g.explain('q',[{'id':'x','text':'原文'}],'结果')
def test_embedding_mock():assert gateway_reply({'data':[{'index':0,'embedding':[1,0]}]}).embed(['abc'])==[[1,0]]
def test_embedding_invalid_mock():
    with pytest.raises(ValueError):gateway_reply({'data':[{'index':0,'embedding':[]}]}).embed(['abc'])
def test_embedding_failure_visible():
    class Broken:
        def embed(self,x):raise TimeoutError
    d={'id':'d','title':'现金流','body':'现金流资料','source':'s','version':1,'department':'research','topic':'cash','sha256':'h'}
    e,mode,w=Retriever(Broken()).search('现金流',[d]);assert e and w and '离线' in mode
@pytest.mark.parametrize('url',['http://static.cninfo.com.cn/a.pdf','https://evil.example/a.pdf','https://static.cninfo.com.cn.evil/a.pdf','https://u:p@static.cninfo.com.cn/a.pdf','https://static.cninfo.com.cn:8443/a.pdf'])
def test_download_allowlist(url):
    with pytest.raises(ValueError):validate_url(url)
def test_download_mock(tmp_path):
    out=tmp_path/'report.pdf'
    r=download_report('https://static.cninfo.com.cn/test.pdf',out,transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'%PDF-1.7\nTEST')))
    assert out.exists() and r['status']=='downloaded_not_reviewed'
def test_download_no_redirect(tmp_path):
    with pytest.raises(httpx.HTTPStatusError):download_report('https://static.cninfo.com.cn/test.pdf',tmp_path/'a.pdf',transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'https://evil.example'})))
def test_api_auth_and_workflow(tmp_path):
    client=TestClient(create_app(tmp_path/'db.sqlite'))
    assert client.get('/api/catalog').status_code==401
    headers={'Authorization':'Bearer local-analyst'}
    assert client.get('/api/catalog',headers=headers).json()['fact_count']==168
    r=client.post('/api/ask',headers=headers,json={'question':'公牛2024年三表核对'})
    assert r.status_code==200 and r.json()['status']=='completed'
    assert client.post('/api/documents',headers=headers,json=document()).status_code==403
    assert client.get('/').status_code==200

def test_year_conflict(engine,analyst):
    with pytest.raises(ValueError):engine.ask(analyst,'公牛2023年三表',year=2024)
def test_cannot_overwrite_finance_with_text(store,editor):
    with pytest.raises(PermissionError):store.add_document(editor,document(topic='report:603195:2024:is'))
