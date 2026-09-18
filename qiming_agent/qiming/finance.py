"""Deterministic financial tools. LLMs never calculate or rewrite the fact ledger."""
from __future__ import annotations
import csv, io, json, statistics
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

NAMES={'cash':'货币资金','receivables':'应收账款','inventory':'存货','current_assets':'流动资产合计',
 'assets':'资产总计','current_liabilities':'流动负债合计','liabilities':'负债合计','equity':'所有者权益合计',
 'revenue':'营业收入','cost':'营业成本','selling_expense':'销售费用','rd':'研发费用',
 'profit':'合并净利润','parent_profit':'归母净利润','cfo':'经营活动现金流量净额',
 'cfi':'投资活动现金流量净额','cff':'筹资活动现金流量净额','fx':'汇率变动影响',
 'net_cash_change':'现金净增加额','opening_cash':'期初现金及现金等价物','ending_cash':'期末现金及现金等价物'}
D=Decimal

def fmt(value: Decimal|None, unit: str='亿元') -> str:
    if value is None:return '未取得/不适用'
    divisor=D('100000000') if unit=='亿元' else D(1)
    return f'{(value/divisor).quantize(D("0.01"),rounding=ROUND_HALF_UP):,.2f}{unit}'

def ratio(numerator: Decimal|None, denominator: Decimal|None) -> Decimal|None:
    return None if numerator is None or denominator is None or denominator<=0 else numerator/denominator

def growth(current: Decimal|None, previous: Decimal|None) -> Decimal|None:
    # Negative/zero bases make the ordinary YoY percentage misleading.
    return None if current is None or previous is None or previous<=0 else (current-previous)/previous

class Finance:
    def __init__(self,data_dir:Path):
        self.sources=json.loads((data_dir/'sources.json').read_text(encoding='utf-8'))
        self.companies={s['company']:s for s in self.sources}
        sources={s['source_id']:s for s in self.sources}
        with (data_dir/'financial_facts.csv').open(encoding='utf-8',newline='') as fh:
            raw=list(csv.DictReader(fh))
        self.facts=[]
        for row in raw:
            source=sources[row['source_id']]
            self.facts.append(row | dict(year=int(row['year']),page=int(row['page']),
                name=source['name'],report_year=source['year'],source_file=source['file'],
                currency='CNY',unit='元',scope='consolidated',verified=True))
        self.validate_ledger()

    def validate_ledger(self):
        seen=set()
        for f in self.facts:
            key=(f['company'],f['year'],f['metric'])
            if key in seen: raise ValueError(f'冲突事实，需选择报告版本：{key}')
            seen.add(key)
            if f['scope']!='consolidated' or f['currency']!='CNY' or f['unit']!='元':
                raise ValueError('种子账本仅允许CNY元、合并口径')
            if f['value'] is not None and not D(f['value']).is_finite():raise ValueError('非有限值')

    def rows(self,company:str,year:int):
        if company not in self.companies:raise ValueError('未纳入样本的公司')
        found=[f for f in self.facts if f['company']==company and f['year']==year and f.get('verified')]
        if not found:raise ValueError(f'{self.companies[company]["name"]}未取得{year}年已核对报表；不能沿用其他年份')
        return found

    def values(self,company,year):return {f['metric']:D(f['value']) if f['value'] is not None else None for f in self.rows(company,year)}

    def reconcile(self,company,year):
        v=self.values(company,year);out=[]
        for name,left,terms in [('资产=负债+权益','assets',['liabilities','equity']),
          ('现金变动=经营+投资+筹资+汇率','net_cash_change',['cfo','cfi','cff','fx']),
          ('期末现金=期初+净变动','ending_cash',['opening_cash','net_cash_change'])]:
            needed=[left]+terms
            if any(v.get(k) is None for k in needed):out.append({'name':name,'status':'missing','difference':None});continue
            diff=v[left]-sum((v[k] for k in terms),D(0))
            out.append({'name':name,'status':'pass' if abs(diff)<=D('0.02') else 'fail','difference':str(diff)})
        return out

    def snapshot(self,company,year):
        rows=self.rows(company,year);v=self.values(company,year)
        derived={
          '毛利率':ratio(None if v.get('revenue') is None or v.get('cost') is None else v['revenue']-v['cost'],v.get('revenue')),
          '合并净利率':ratio(v.get('profit'),v.get('revenue')),
          '资产负债率':ratio(v.get('liabilities'),v.get('assets')),
          '研发费用率':ratio(v.get('rd'),v.get('revenue')),
          '经营现金流/合并净利润':ratio(v.get('cfo'),v.get('profit')),
          '流动比率':ratio(v.get('current_assets'),v.get('current_liabilities'))}
        try:prev=self.values(company,year-1)
        except ValueError:prev={}
        yoy={k:growth(v.get(k),prev.get(k)) for k in ['revenue','profit','cfo','receivables','inventory']}
        checks=self.reconcile(company,year)
        warnings=['历史公开披露样本，不代表最新经营情况；不是投资、授信或采购批准。']
        if v.get('profit') is not None and v['profit']<=0:warnings.append('合并净利润非正，现金流/利润比不展示；亏损同比不使用通常增长率。')
        if not prev:warnings.append('缺少同口径上期数据，不计算同比。')
        if any(c['status']!='pass' for c in checks):warnings.append('存在缺失或勾稽异常，暂停确定性结论并请人工复核。')
        return {'company':company,'name':self.companies[company]['name'],'segment':self.companies[company]['segment'],
          'year':year,'facts':rows,'derived':{k:str(x) if x is not None else None for k,x in derived.items()},
          'yoy':{k:str(x) if x is not None else None for k,x in yoy.items()},'checks':checks,'warnings':warnings}

    def compare(self,companies:list[str],year:int):
        if len(set(companies))<2:raise ValueError('对照至少选择两家公司')
        records=[self.snapshot(c,year) for c in dict.fromkeys(companies)]
        return {'records':records,'warning':'产业链观察样本，不是同质可比池；不生成综合排名或行业分位。'}

    def csv(self,companies:list[str],year:int):
        buf=io.StringIO();w=csv.writer(buf)
        w.writerow(['公司代码','公司','年度','报表','指标','数值','单位','口径','来源文件','PDF页码'])
        for c in companies:
            for f in self.rows(c,year):w.writerow([f['company'],f['name'],f['year'],f['statement'],NAMES[f['metric']],f['value'],'元','合并',f['source_file'],f['page']])
        return buf.getvalue()

    def brief(self,companies:list[str],year:int,skill:str)->str:
        parts=['# 企明｜经营观察简报（待人工复核）',f'分析期：{year}年。基于已加载报告，不是实时经营数据。']
        if len(companies)>1:parts.append('注意：公司分属照明产业链不同环节，仅并列展示，不作直接同业排名。')
        for c in companies:
            s=self.snapshot(c,year);v=self.values(c,year)
            parts += [f'\n## {s["name"]}｜{s["segment"]}', '|项目|金额|来源|','|---|---:|---|']
            metrics=['revenue','profit','parent_profit','cfo','assets','liabilities']
            if skill=='working_capital':metrics+=['receivables','inventory','current_assets','current_liabilities']
            for k in metrics:
                f=next((f for f in s['facts'] if f['metric']==k),None)
                if f:parts.append(f'|{NAMES[k]}|{fmt(v.get(k))}|{f["source_id"]} PDF第{f["page"]}页|')
            parts.append('\n程序计算（可根据源值复算）：')
            for label,val in s['derived'].items():
                text='不适用/未取得' if val is None else (f'{D(val):.2f}倍' if label in {'流动比率','经营现金流/合并净利润'} else f'{D(val)*100:.2f}%')
                parts.append(f'- {label}：{text}')
            for k,x in s['yoy'].items():
                if x is not None:parts.append(f'- {NAMES[k]}同比：{D(x)*100:.2f}%')
            if s['yoy']['revenue'] is not None and s['yoy']['cfo'] is not None and D(s['yoy']['revenue'])>0>D(s['yoy']['cfo']):
                parts.append('观察：营业收入增长而经营净现金流下降。原因尚不能仅凭三表判定，应回查应收、存货及现金流附注。')
            parts.append('勾稽检查：'+'；'.join(f'{x["name"]}={x["status"]}' for x in s['checks']))
            parts.extend(s['warnings'])
        parts.append('\n## 待人工确认\n核对报告是否最新、重述口径、业务可比性与附注；不得据此自动调整供应商额度、授信或付款。')
        return '\n'.join(parts)
