"""Auditable, review-first adapters evolved from the user's annual-report notebooks.
The PDF adapter is a candidate extractor, NOT an arbitrary-layout financial parser.
"""
from __future__ import annotations
import csv, hashlib, re, unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

LABELS = {
 'bs': {'cash':['货币资金'], 'receivables':['应收账款'], 'inventory':['存货'],
 'current_assets':['流动资产合计'], 'assets':['资产总计'],
 'current_liabilities':['流动负债合计'], 'liabilities':['负债合计'],
 'equity':['所有者权益（或股东权益）合计','所有者权益合计']},
 'is': {'revenue':['其中：营业收入','营业收入'], 'cost':['其中：营业成本','营业成本'],
 'selling_expense':['销售费用'], 'rd':['研发费用'],
 'profit':['五、净利润','四、净利润'],
 'parent_profit':['1.归属于母公司股东的净利润','归属于母公司所有者的净利润']},
 'cf': {'cfo':['经营活动产生的现金流量净额'], 'cfi':['投资活动产生的现金流量净额'],
 'cff':['筹资活动产生的现金流量净额'], 'fx':['四、汇率变动对现金及现金等价物的影响'],
 'net_cash_change':['五、现金及现金等价物净增加额'],
 'opening_cash':['加：期初现金及现金等价物余额'], 'ending_cash':['六、期末现金及现金等价物余额']}}
HEADS={'bs':('合并资产负债表','母公司资产负债表'),
       'is':('合并利润表','母公司利润表'), 'cf':('合并现金流量表','母公司现金流量表')}
MONEY = re.compile(r'(?<![\d.])[−-]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d)')

def number(value: Any, unit: str = '元') -> Decimal | None:
    """Preserve missing, negative numbers and explicit unit; never coerce missing to zero."""
    if value is None or str(value).strip() in {'','--','—','-','N/A','不适用'}: return None
    text=unicodedata.normalize('NFKC',str(value)).strip().replace(',','').replace('−','-')
    if text.startswith('(') and text.endswith(')'): text='-'+text[1:-1]
    if text.endswith('%'): raise ValueError('百分数不能作为金额自动导入')
    if unit not in {'元','万元','亿元'}: raise ValueError('无法确认金额单位')
    try: result=Decimal(text)
    except InvalidOperation as exc: raise ValueError(f'非标准数字：{text}') from exc
    if not result.is_finite(): raise ValueError('非有限数字')
    return result * {'元':Decimal(1),'万元':Decimal(10000),'亿元':Decimal(100000000)}[unit]

def label_pattern(label: str) -> str:
    # A label may wrap over multiple PDF lines; a line boundary prevents substring collisions.
    return r'(?m)^\s*' + r'\s*'.join(re.escape(c) for c in label)

def extract_pages(pages: list[str], company: str, name: str, year: int,
                  source_file: str, source_id: str) -> tuple[list[dict],list[str]]:
    """Extract two annual columns within consolidated sections. Always requires review.
    Demonstrated layouts use current/prior year left-to-right and currency CNY yuan.
    Unknown unit or missing labels block publication rather than guess.
    """
    facts=[]; issues=[]
    for statement,(start,end) in HEADS.items():
        active=False; section=[]
        for page_no,text in enumerate(pages,1):
            if not active:
                pos=text.find(start)
                if pos<0: continue
                active=True; text=text[pos+len(start):]
            endpos=text.find(end)
            section.append((page_no,text[:endpos] if endpos>=0 else text))
            if endpos>=0: break
        if not section:
            issues.append(f'{statement}:找不到合并报表边界'); continue
        intro=''.join(t for _,t in section)[:800]
        unit_unknown=not re.search(r'单位\s*[:：]\s*元',intro)
        if unit_unknown: issues.append(f'{statement}:无法确认元单位（禁止自动发布）')
        for metric, aliases in LABELS[statement].items():
            candidates=[]
            for page_no,text in section:
                for alias in aliases:
                    for match in re.finditer(label_pattern(alias),text):
                        after=text[match.end():match.end()+150]
                        # Financial values must follow this label before any different monetary row.
                        nums=list(MONEY.finditer(after))
                        if len(nums)<2: continue
                        prefix=after[:nums[0].start()]
                        if len(prefix)>85: continue
                        # Only note references and suffix explanations may occur before amounts.
                        forbidden=['合计','营业收入','营业成本','流动资产','流动负债','股东权益','现金流']
                        if any(x in prefix for x in forbidden): continue
                        raw=text[match.start():match.end()+nums[1].end()].strip()
                        candidates.append((page_no,[m.group() for m in nums[:2]],raw))
            # Alias equivalence is deduplicated; distinct values need a human decision.
            unique={(p,tuple(v)):raw for p,v,raw in candidates}
            if len(unique)!=1:
                issues.append(f'{statement}.{metric}:候选数{len(unique)}，需人工核对');continue
            (p,vals),raw=next(iter(unique.items()))
            for offset,value in enumerate(vals):
                facts.append(dict(company=company,name=name,year=year-offset,statement=statement,
                    metric=metric,value=str(number(value)),currency='CNY',unit='元',scope='consolidated',
                    report_year=year,source_id=source_id,source_file=source_file,page=p,
                    raw_line=raw,verified=False,unit_confirmed=not unit_unknown))
    return facts,issues

def extract_pdf(path: Path, company: str, name: str, year: int) -> dict:
    import fitz  # Optional only when parsing an original PDF.
    if path.stat().st_size>60_000_000: raise ValueError('单文件超过60MB')
    if not path.read_bytes()[:5]==b'%PDF-': raise ValueError('不是完整PDF；禁止把下载残片当作年报')
    with fitz.open(path) as doc:
        if len(doc)>1000: raise ValueError('超过1000页；请拆分或明确范围')
        pages=[p.get_text() for p in doc]
    facts,issues=extract_pages(pages,company,name,year,path.name,f'{company}-{year}')
    return dict(facts=facts,issues=issues,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                status='pending_review',note='请核对公司、年度、左右列、合并口径、单位及源页后发布；本工具不做OCR。')

def import_rows(rows: list[dict], company: str, years: list[int], statement: str,
                unit: str, source: str) -> list[dict]:
    """Parameterized replacement for the legacy fixed-year / values[0] spreadsheet loop.
    Input is a list of row mappings, so CSV, table readers and API adapters can reuse it.
    """
    if statement not in LABELS: raise ValueError('未知报表类型')
    result=[]
    for metric,aliases in LABELS[statement].items():
        matches=[r for r in rows if str(r.get('项目名称','')).strip() in aliases]
        if len(matches)>1: raise ValueError(f'{metric}:同名行冲突，不取第一行')
        for year in years:
            value=number(matches[0].get(str(year)) if matches else None,unit)
            result.append(dict(company=company,year=year,statement=statement,metric=metric,
                               value=str(value) if value is not None else None,unit='元',
                               currency='CNY',scope='consolidated',source_file=source,verified=False))
    return result

def find_header(rows: list[list], allowed=('项目名称','项目名称(单位:元)','序号','产品名称')) -> int:
    """Retain the wealth script's header scan, fix `x == '序号' or '产品名称'` always-true bug."""
    for index,row in enumerate(rows):
        if row and str(row[0]).strip() in allowed:return index
    raise ValueError('未找到明确的表头，禁止默认为第一行')

def normalize_csv(path:Path,company:str,years:list[int],statement:str,unit:str):
    with path.open(encoding='utf-8-sig',newline='') as fh: raw=list(csv.reader(fh))
    header=find_header(raw);names=raw[header];data=[dict(zip(names,r)) for r in raw[header+1:]]
    for d in data:
        if '项目名称' not in d:d['项目名称']=d.get('项目名称(单位:元)')
        for y in years:
            if str(y) not in d:d[str(y)]=d.get(f'{y}-12-31')
    return import_rows(data,company,years,statement,unit,path.name)
