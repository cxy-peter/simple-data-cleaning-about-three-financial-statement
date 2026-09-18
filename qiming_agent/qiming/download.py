"""Explicit, allowlisted public-report download; never an LLM-controlled web crawler."""
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
import httpx

HOSTS={'static.cninfo.com.cn','www.sse.com.cn','www.szse.cn','disc.static.szse.cn'}
def validate_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or p.port not in {None,443}:raise ValueError('仅允许已列出的交易所/披露平台HTTPS地址，不跟随跳转')
    if not p.path.lower().endswith('.pdf'):raise ValueError('只下载明确的PDF报告')
    return p

def download_report(url:str,destination:Path,expected_sha256:str|None=None,transport=None):
    validate_url(url)
    if destination.exists():raise FileExistsError('不覆盖已有报告，请使用新文件名')
    tmp=destination.with_suffix(destination.suffix+'.part');destination.parent.mkdir(parents=True,exist_ok=True)
    h=hashlib.sha256();total=0
    try:
        with httpx.Client(timeout=30,follow_redirects=False,transport=transport) as client:
            with client.stream('GET',url,headers={'User-Agent':'QimingReportResearch/1.0'}) as r:
                r.raise_for_status()
                with tmp.open('xb') as fh:
                    for block in r.iter_bytes():
                        total+=len(block)
                        if total>60_000_000:raise ValueError('报告超过60MB')
                        h.update(block);fh.write(block)
        if not tmp.read_bytes().startswith(b'%PDF-'):raise ValueError('返回内容不是PDF')
        if expected_sha256 and h.hexdigest()!=expected_sha256:raise ValueError('SHA256与预期不符')
        tmp.replace(destination)
    except Exception:
        tmp.unlink(missing_ok=True);raise
    return {'path':str(destination),'bytes':total,'sha256':h.hexdigest(),'source_url':url,'status':'downloaded_not_reviewed'}
