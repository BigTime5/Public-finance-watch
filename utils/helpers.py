import time, re, ssl, hashlib
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
import requests, urllib3
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Lazy import config (avoids circular import at patch time) ─────────────────
def _cfg(name, default):
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception:
        return default

def _get_logger():
    try:
        from utils.logger import get_logger as _gl
        return _gl("helpers")
    except Exception:
        import logging
        return logging.getLogger("helpers")

class _NoVerifyAdapter(HTTPAdapter):
    """Replaces the entire SSL connection pool with CERT_NONE context."""
    def init_poolmanager(self, num_pools, maxsize, block=False, **kw):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(
            num_pools=num_pools, maxsize=maxsize, block=block, ssl_context=ctx)
    def proxy_manager_for(self, proxy, **kw):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kw["ssl_context"] = ctx
        return super().proxy_manager_for(proxy, **kw)

_NO_VERIFY_HOSTS: set = {"www.knbs.or.ke","knbs.or.ke","cob.go.ke","www.cob.go.ke"}
_session_normal = None
_session_no_verify = None

def _get_session(no_verify=False):
    global _session_normal, _session_no_verify
    HEADERS = _cfg("HEADERS", {"User-Agent": "Mozilla/5.0"})
    if no_verify:
        if _session_no_verify is None:
            s = requests.Session(); s.headers.update(HEADERS)
            s.mount("https://", _NoVerifyAdapter()); globals()["_session_no_verify"] = s
        return _session_no_verify
    else:
        if _session_normal is None:
            s = requests.Session(); s.headers.update(HEADERS)
            globals()["_session_normal"] = s
        return _session_normal

def get_session():
    return _get_session(no_verify=False)

def _needs_no_verify(url):
    return urlparse(url).netloc.lower() in _NO_VERIFY_HOSTS

def _mark_no_verify(url):
    host = urlparse(url).netloc.lower()
    if host not in _NO_VERIFY_HOSTS:
        _NO_VERIFY_HOSTS.add(host)
        _get_logger().warning("SSL bypass enabled for '%s'", host)

def safe_get(url, *, session=None, timeout=None, extra_headers=None,
             retries=None, backoff=None, delay=None):
    log     = _get_logger()
    timeout  = timeout  or _cfg("REQUEST_TIMEOUT", 60)
    retries  = retries  or _cfg("MAX_RETRIES", 4)
    backoff  = backoff  or _cfg("RETRY_BACKOFF", 2.0)
    delay    = delay    or _cfg("POLITE_DELAY", 1.5)
    HEADERS  = _cfg("HEADERS", {"User-Agent": "Mozilla/5.0"})
    hdrs     = {**HEADERS, **(extra_headers or {})}
    ssl_done = False
    last_exc = None

    for attempt in range(1, retries + 2):
        sess = session or _get_session(no_verify=_needs_no_verify(url))
        try:
            time.sleep(delay)
            r = sess.get(url, headers=hdrs, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (403,404):
                log.warning("HTTP %s %s", e.response.status_code, url); return None
            last_exc = e
        except requests.exceptions.SSLError as e:
            if not ssl_done and not _needs_no_verify(url):
                ssl_done = True; _mark_no_verify(url); continue
            last_exc = e
        except requests.exceptions.RequestException as e:
            last_exc = e
        if attempt <= retries:
            w = backoff**attempt
            log.warning("Attempt %d/%d failed %s — retry %.1fs: %s",
                        attempt, retries, url, w, last_exc)
            time.sleep(w)
    log.error("All attempts failed %s: %s", url, last_exc); return None

def download_file(url, dest_dir, filename=None, *, session=None,
                  timeout=None, skip_if_exists=True):
    log       = _get_logger()
    timeout   = timeout or _cfg("DOWNLOAD_TIMEOUT", 300)
    POLITE    = _cfg("POLITE_DELAY", 1.5)
    CHUNK     = _cfg("CHUNK_SIZE", 65536)
    HEADERS   = _cfg("HEADERS", {"User-Agent": "Mozilla/5.0"})
    MAX_R     = _cfg("MAX_RETRIES", 4)
    BACKOFF   = _cfg("RETRY_BACKOFF", 2.0)
    dest_dir  = Path(dest_dir); dest_dir.mkdir(parents=True, exist_ok=True)
    filename  = filename or _safe_filename(url)
    dest      = dest_dir / filename
    if skip_if_exists and dest.exists() and dest.stat().st_size > 0:
        log.info("Skip (exists): %s", dest.name); return dest
    ssl_done  = False
    for attempt in range(1, MAX_R + 2):
        sess = session or _get_session(no_verify=_needs_no_verify(url))
        try:
            time.sleep(POLITE)
            with sess.get(url, stream=True, timeout=timeout, headers=HEADERS) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length",0)); dl = 0
                with open(dest,"wb") as fh:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        if chunk: fh.write(chunk); dl+=len(chunk)
                if total and dl < total*0.95:
                    raise IOError(f"Incomplete: {dl}/{total}")
            log.info("Downloaded %-50s → %s (%.1fKB)",
                     url[-50:], dest.name, dest.stat().st_size/1024)
            return dest
        except requests.exceptions.SSLError as e:
            if not ssl_done and not _needs_no_verify(url):
                ssl_done=True; _mark_no_verify(url)
                if dest.exists(): dest.unlink(missing_ok=True)
                log.info("Retry download with SSL bypass: %s", dest.name); continue
            w=BACKOFF**min(attempt,4); dest.unlink(missing_ok=True) if dest.exists() else None
            log.warning("SSL bypass still failing %.1fs: %s", w, e); time.sleep(w)
        except Exception as e:
            w=BACKOFF**min(attempt,4); dest.unlink(missing_ok=True) if dest.exists() else None
            log.warning("Download attempt %d failed (%s) retry %.1fs", attempt, e, w)
            time.sleep(w)
    log.error("Failed to download %s", url); return None

def parse_html(html, base_url=""):
    return BeautifulSoup(html, "lxml")

def extract_pdf_links(html, base_url, *, require_text=None):
    soup=parse_html(html,base_url); out=[]
    for a in soup.find_all("a",href=True):
        h=a["href"].strip()
        if not h.lower().endswith(".pdf"): continue
        t=a.get_text(strip=True)
        if require_text and require_text.lower() not in t.lower(): continue
        out.append({"url":urljoin(base_url,h),"text":t})
    return out

def extract_file_links(html, base_url,
                       extensions=(".pdf",".xlsx",".xls",".csv")):
    soup=parse_html(html,base_url); out=[]
    for a in soup.find_all("a",href=True):
        h=a["href"].strip(); pl=urlparse(h).path.lower()
        for ext in extensions:
            if pl.endswith(ext):
                out.append({"url":urljoin(base_url,h),
                            "text":a.get_text(strip=True),"ext":ext}); break
    return out

def _safe_filename(url, max_len=120):
    name=Path(urlparse(url).path).name
    name=re.sub(r"[?&#].*$","",name)
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]',"_",name).strip(". ")
    if not name or len(name)>max_len:
        m=re.search(r'\.(pdf|xlsx?|csv|tar\.gz|jsonl\.gz)$',url,re.I)
        name=hashlib.sha1(url.encode()).hexdigest()[:12]+(m.group(0) if m else ".bin")
    return name

def slugify(text):
    text=text.lower().strip()
    text=re.sub(r'[^\w\s-]','',text)
    return re.sub(r'[\s_]+','-',text)
