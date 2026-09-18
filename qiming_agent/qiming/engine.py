"""Bounded workflow: Intent -> Rewrite -> Skill tools -> Retrieval -> Answer -> Verify.
This is not an autonomous ReAct agent or a production MCP server.
"""
from __future__ import annotations
import json,re,time,uuid
from datetime import date
from pathlib import Path
from .finance import Finance
from .model import ModelGateway
from .retrieval import Retriever,verify_evidence
from .store import Principal,Store

SKILLS={
 'statement_review':{'name':'三表核对','triggers':['三表','三大报表','核对','资产负债'], 'tools':['snapshot','reconcile','brief'],'top_k':4,'queries':['三大报表 单位 合并 口径'],'version':1},
 'cashflow':{'name':'利润与现金流观察','triggers':['现金流','现金','回款'], 'tools':['snapshot','brief'],'top_k':5,'queries':['经营现金流 合并净利润 原因 待核实'],'version':1},
 'working_capital':{'name':'应收与存货观察','triggers':['应收','存货','营运资金'], 'tools':['snapshot','brief'],'top_k':5,'queries':['应收账款 存货 周转 可比口径'],'version':1},
 'compare':{'name':'产业链样本对照','triggers':['比较','对比','对照'], 'tools':['compare','brief'],'top_k':4,'queries':['产业链 可比性 同年度 合并'],'version':1},
 'meeting_brief':{'name':'经营会议简报','triggers':['简报','周报','报告','会议'], 'tools':['snapshot','brief'],'top_k':5,'queries':['经营分析 报告 来源 待核实'],'version':1},
 'evidence_qa':{'name':'知识与依据问答','triggers':[], 'tools':['retrieve'],'top_k':4,'queries':[],'version':1}}
ALIASES={'公牛':'603195','民爆':'301362','兆驰':'002429','晶丰':'688368','bull':'603195','agc':'301362','amtc':'002429','bps':'688368'}

class Engine:
    def __init__(self,store:Store,data_dir:Path|None=None,gateway=None):
        self.store=store;self.data_dir=data_dir or Path(__file__).parent/'data';self.finance=Finance(self.data_dir)
        self.gateway=gateway or ModelGateway();self.retriever=Retriever(self.gateway if self.gateway.embedding_ready else None)
        if not self.store.db.execute('SELECT 1 FROM documents LIMIT 1').fetchone():
            seed=Principal('seed-editor','demo',('research','procurement'),'editor')
            for d in json.loads((self.data_dir/'knowledge.json').read_text(encoding='utf-8')):
                self.store.add_document(seed,d,status=d.get('status','active'))
            from .finance import NAMES
            for c,source in self.finance.companies.items():
                for year in [source['year'],source['year']-1]:
                    for st in ['bs','is','cf']:
                        fs=[f for f in self.finance.rows(c,year) if f['statement']==st]
                        body='\n\n'.join(f"{NAMES[f['metric']]}：{f['value']}元。合并口径，数值年度{year}；报告年度{source['year']}，PDF第{f['page']}页。" for f in fs)
                        d=dict(id=f'fin-{c}-{year}-{st}',title=f"{source['name']}{year}年"+{'bs':'资产负债表','is':'利润表','cf':'现金流量表'}[st],
                            body=body,source=source['file']+'；SHA256见sources.json',department='research',version=1,topic=f'report:{c}:{year}:{st}')
                        self.store.add_document(seed,d,status='active')

    def plan(self,question,companies=None,year=None,skill=None):
        explicit=list(dict.fromkeys(companies or []));q=question.lower()
        if not explicit:
            explicit=list(dict.fromkeys([code for alias,code in ALIASES.items() if alias in q]+[code for code in self.finance.companies if code in q]))
        ys={int(x) for x in re.findall(r'(?<!\d)(20\d{2})(?!\d)',q)}
        if len(ys)>1 and year is None:raise ValueError('一次任务选择一个分析年度；同比由程序读取上一年')
        if year is not None and ys and year not in ys:raise ValueError('问题年度与所选年度冲突，请确认')
        year=year or (next(iter(ys)) if ys else None)
        if skill and skill not in SKILLS:raise ValueError('Skill不在白名单中')
        if not skill:
            skill=next((k for k,v in SKILLS.items() if any(t in q for t in v['triggers'])),'evidence_qa')
            if len(explicit)>1:skill='compare'
            elif explicit and skill=='evidence_qa' and any(t in q for t in ['收入','利润','财务','多少','研发']):skill='statement_review'
        return {'companies':explicit,'year':year,'skill':skill}

    def ask(self,p:Principal,question:str,companies=None,year=None,skill=None,use_model=False,as_of=None):
        start=time.perf_counter();tid=uuid.uuid4().hex;steps=[]
        if not question.strip() or len(question)>2000:raise ValueError('问题长度须为1至2000字')
        plan=self.plan(question,companies,year,skill);sk=SKILLS[plan['skill']]
        steps.append({'node':'Intent','output':plan});warnings=[];records=[];tool_text=''
        query=question+' '+' '.join(sk['queries'])
        steps.append({'node':'Rewrite','expanded_query':query,'top_k':sk['top_k']})
        status='completed'
        if plan['skill']!='evidence_qa' and (not plan['companies'] or not plan['year']):
            status='needs_clarification';tool_text='请明确公司和分析年度；不默认把不同年份数据当作最新。'
        if status=='completed' and plan['skill']!='evidence_qa':
            try:
                records=(self.finance.compare(plan['companies'],plan['year'])['records']
                         if plan['skill']=='compare' else [self.finance.snapshot(c,plan['year']) for c in plan['companies']])
                if any(ch['status']!='pass' for r in records for ch in r['checks']):
                    status='needs_review';tool_text='三表存在缺失或勾稽不平，请先核对原始数据。'
                else:tool_text=self.finance.brief(plan['companies'],plan['year'],plan['skill'])
            except ValueError as exc:status='insufficient_data';tool_text=str(exc)
        steps.append({'node':'Skill tools','name':sk['name'],'version':sk['version'],'tools':sk['tools'],
                      'status':status,'companies':plan['companies'],'year':plan['year']})
        docs=self.store.visible(p,as_of)
        # Numeric public reports are retrieved only for explicitly chosen company/year.
        docs=[d for d in docs if not d['topic'].startswith('report:') or
              any(d['topic'].startswith(f'report:{c}:{plan["year"]}:') for c in plan['companies'])]
        evidence,mode,notes=self.retriever.search(query,docs,sk['top_k']);warnings+=notes
        # Re-read authoritative state before returning or sending evidence to a model.
        valid,issues=verify_evidence(evidence,self.store.visible(p,as_of))
        steps.append({'node':'Retrieval','mode':mode,'evidence_ids':[e['id'] for e in evidence]})
        llm=None
        if status=='completed' and plan['skill']=='evidence_qa':
            if valid:tool_text='相关依据（原文摘录，不是模型生成）：\n\n'+'\n\n'.join(f'[{e["id"]}] {e["title"]}\n{e["text"]}\n来源：{e["source"]}' for e in evidence)
            else:status='insufficient_evidence';tool_text='没有当前有效且有权限的相关依据，请补充材料或交由负责人确认。'
        if use_model and status=='completed' and valid:
            try:llm=self.gateway.explain(question,evidence,tool_text)
            except Exception as exc:warnings.append('生成服务未配置、不可用或输出未通过校验；返回程序结果与原文，不伪装成大模型回答。')
        if use_model and not valid:warnings.append('无有效证据，禁止调用生成模型补答案。')
        steps.extend([{'node':'Answer','mode':'模型草稿+程序事实' if llm else '离线证据/程序结果'},
          {'node':'Verify','source_valid':valid,'issues':issues,'semantic_verification':'模型段落仍需人工复核' if llm else '程序计算/逐字原文；不声称通用语义验证'}])
        result={'trace_id':tid,'status':status,'question':question,'plan':plan,'answer':tool_text,
         'model_draft':llm,'evidence':evidence if valid else [],'records':records,'warnings':warnings,
         'retrieval_mode':mode,'steps':steps,'elapsed_ms':round((time.perf_counter()-start)*1000,2),
         'boundary':'个人本地原型；工具不发送邮件、不付款、不改ERP，不自动发布知识或Skill。'}
        self.store.save_trace(p,result)
        return result
