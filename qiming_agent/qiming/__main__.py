from __future__ import annotations
import argparse,json,sys
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description='企明｜照明产业链经营与财报 Agent')
 sub=p.add_subparsers(dest='cmd',required=True)
 serve=sub.add_parser('serve');serve.add_argument('--port',type=int,default=8765)
 ask=sub.add_parser('ask');ask.add_argument('question');ask.add_argument('--out');ask.add_argument('--model',action='store_true')
 ingest=sub.add_parser('ingest');ingest.add_argument('pdf',type=Path);ingest.add_argument('--company',required=True);ingest.add_argument('--name',required=True);ingest.add_argument('--year',type=int,required=True);ingest.add_argument('--out',type=Path,required=True)
 dl=sub.add_parser('download');dl.add_argument('url');dl.add_argument('--out',type=Path,required=True);dl.add_argument('--sha256')
 checks=sub.add_parser('check-data')
 norm=sub.add_parser('normalize-csv');norm.add_argument('csv',type=Path);norm.add_argument('--company',required=True);norm.add_argument('--years',type=int,nargs='+',required=True);norm.add_argument('--statement',choices=['bs','is','cf'],required=True);norm.add_argument('--unit',choices=['元','万元','亿元'],default='元');norm.add_argument('--out',type=Path,required=True)
 args=p.parse_args()
 try:
  if args.cmd=='serve':
   import uvicorn;uvicorn.run('qiming.app:app',host='127.0.0.1',port=args.port);return
  if args.cmd=='ingest':
   from .ingest import extract_pdf
   result=extract_pdf(args.pdf,args.company,args.name,args.year)
  elif args.cmd=='download':
   from .download import download_report
   print(json.dumps(download_report(args.url,args.out,args.sha256),ensure_ascii=False,indent=2));return
  elif args.cmd=='normalize-csv':
   from .ingest import normalize_csv
   result={'facts':normalize_csv(args.csv,args.company,args.years,args.statement,args.unit),'status':'pending_review'}
  else:
   from .engine import Engine
   from .store import Store,Principal
   engine=Engine(Store())
   if args.cmd=='ask':
    result=engine.ask(Principal('cli'),args.question,use_model=args.model)
    print(result['answer'])
   else:
    result=[dict(company=c,year=y,checks=engine.finance.reconcile(c,y)) for c in engine.finance.companies for y in sorted({f['year'] for f in engine.finance.facts if f['company']==c})]
    print(json.dumps(result,ensure_ascii=False,indent=2));return
  if args.out:
   path=Path(args.out)
   if path.exists():raise FileExistsError('输出已存在，请更换名称以保留前次结果')
   path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
  elif args.cmd!='ask':print(json.dumps(result,ensure_ascii=False,indent=2))
 except Exception as exc:p.exit(1,f'{type(exc).__name__}: {exc}\n')
if __name__=='__main__':main()
