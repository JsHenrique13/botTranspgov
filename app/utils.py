import os
import socket
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse, urlencode
from datetime import datetime
import random
import time
import json
from contextlib import contextmanager

BASE_SITE = "https://www.generalsampaio.ce.gov.br"
DESPESAS_PATH = "/despesas.php"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; ExportBot/1.0; +https://example.org/bot)"

# lista simples de User-Agents para rotacionar (adicione mais se quiser)
_USER_AGENTS = [
    # Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Safari / Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/114.0.1823.67",
    # Mobile (opcional)
    "Mozilla/5.0 (Linux; Android 13; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.5735.196 Mobile Safari/537.36",
    DEFAULT_USER_AGENT
]

@contextmanager
def _temp_env(var: str, value):
    """Context manager para setar temporariamente variável de ambiente."""
    old = os.environ.get(var)
    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old
            

def _create_session(respect_proxy: bool = False, retries: int = 3, backoff: float = 0.3, custom_headers: dict = None, cookies: dict = None):
    sess = requests.Session()
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Referer": BASE_SITE, "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"}
    if custom_headers:
        headers.update(custom_headers)
    sess.headers.update(headers)
    if cookies:
        sess.cookies.update(cookies)
    sess.trust_env = bool(respect_proxy)
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess

def _tcp_probe(host, port=443, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def fetch_url(url, params=None, timeout=15, custom_headers=None, cookies=None):
    """
    Função original: tenta conexões diretas, PROXY_URL e por fim respeita env proxy.
    Mantida para usos programáticos.
    """
    attempts = []
    parsed = urlparse(url)
    host = parsed.hostname or "www.generalsampaio.ce.gov.br"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    probe_ok = _tcp_probe(host, port=port, timeout=3)
    attempts.append({"step": "tcp_probe", "host": host, "port": port, "ok": probe_ok})

    # 1) tentativa direta com headers/cookies
    try:
        session = _create_session(respect_proxy=False, custom_headers=custom_headers, cookies=cookies)
        resp = session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        attempts.append({"step": "direct_request", "trust_env": False, "status_code": resp.status_code})
        return resp.text
    except Exception as e:
        attempts.append({"step": "direct_request_failed", "trust_env": False, "error": repr(e)})

    # 2) se houver PROXY_URL, usar explicitamente
    proxy_url = os.environ.get("PROXY_URL")
    if proxy_url:
        try:
            session = _create_session(respect_proxy=False, custom_headers=custom_headers, cookies=cookies)
            session.proxies.update({"http": proxy_url, "https": proxy_url})
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            attempts.append({"step": "proxy_request", "proxy_url": proxy_url, "status_code": resp.status_code})
            return resp.text
        except Exception as e:
            attempts.append({"step": "proxy_request_failed", "proxy_url": proxy_url, "error": repr(e)})

    # 3) tentar respeitar env proxy
    try:
        session = _create_session(respect_proxy=True, custom_headers=custom_headers, cookies=cookies)
        resp = session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        attempts.append({"step": "env_proxy_request", "trust_env": True, "status_code": resp.status_code})
        return resp.text
    except Exception as e:
        attempts.append({"step": "env_proxy_request_failed", "trust_env": True, "error": repr(e)})

    raise RuntimeError({"message": "All fetch attempts failed", "url": url, "attempts": attempts})

# ---------------------------
# NOVAS FUNÇÕES (humanizadas + IP)
# ---------------------------

def _random_user_agent():
    return random.choice(_USER_AGENTS)

def _build_human_headers(referrer=None, ua=None, extra=None):
    hdr = {
        "User-Agent": ua or _random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        "Referer": referrer or BASE_SITE,
        "Connection": "keep-alive",
    }
    if extra:
        hdr.update(extra)
    return hdr

def _looks_blocked(html: str, status_code: int = None) -> bool:
    """Heurística simples para detectar bloqueio: status codes e textos comuns."""
    if status_code in (403, 429):
        return True
    if not html:
        return False
    lowered = html.lower()
    blockers = [
        "access forbidden", "access denied", "request blocked", "blocked by", "you have been blocked",
        "captcha", "verify you are a human", "temporarily blocked", "rate limit", "too many requests",
        "403 forbidden", "error 403", "x-squid-error", "access forbidden by rule"
    ]
    return any(b in lowered for b in blockers)

def humanized_fetch(url, max_attempts: int = 5, min_delay: float = 1.0, max_delay: float = 3.0,
                    rotate_proxies: bool = True, proxy_list_env: str = "PROXY_URLS",
                    ua: str = None, referrer: str = None, cookies: dict = None, timeout: int = 15):
    """
    Tentativa 'humana' de buscar a URL:
      - randomiza UA e headers
      - espera delays com jitter entre tentativas
      - backoff exponencial se detectar bloqueio
      - pode rotacionar proxies se PROXY_URLS estiver setado (env: urls separadas por vírgula)
    Retorna html ou levanta RuntimeError com informações de tentativas.
    """
    attempts_info = []
    proxy_urls = []
    env_proxies = os.environ.get(proxy_list_env)
    if env_proxies:
        proxy_urls = [p.strip() for p in env_proxies.split(",") if p.strip()]

    for attempt in range(1, max_attempts + 1):
        # montar headers humanos
        headers = _build_human_headers(referrer=referrer, ua=ua)
        # small random wait before request to mimic human think time
        pre_wait = random.uniform(0.2, 1.2)
        time.sleep(pre_wait)

        # escolher proxy (opcional)
        chosen_proxy = None
        if rotate_proxies and proxy_urls:
            chosen_proxy = random.choice(proxy_urls)

        try:
            if chosen_proxy:
                # usar PROXY_URL temporariamente para compatibilidade com fetch_url logic
                with _temp_env("PROXY_URL", chosen_proxy):
                    html = fetch_url(url, params=None, timeout=timeout, custom_headers=headers, cookies=cookies)
            else:
                # tentar direta ou conforme env (fetch_url já tenta structured attempts)
                html = fetch_url(url, params=None, timeout=timeout, custom_headers=headers, cookies=cookies)

            # verifica se HTML parece bloqueado (captchas / mensagens)
            blocked = _looks_blocked(html, status_code=None)
            attempts_info.append({"attempt": attempt, "proxy": chosen_proxy, "blocked_html": blocked, "status": "ok", "len": len(html or "")})
            if blocked:
                # backoff e tentar trocar UA/proxy
                wait = min_delay * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
                time.sleep(wait)
                continue
            return html

        except RuntimeError as re:
            detail = re.args[0] if re.args else str(re)
            attempts_info.append({"attempt": attempt, "proxy": chosen_proxy, "error": detail})
            # se a falha for claramente de rede, backoff e retry
        except requests.HTTPError as he:
            status = getattr(he.response, "status_code", None)
            blocked_flag = status in (403, 429)
            attempts_info.append({"attempt": attempt, "proxy": chosen_proxy, "http_error": str(he), "status": status})
            if blocked_flag:
                # caso 403/429, espera exponencial maior
                wait = (2 ** attempt) + random.uniform(0, 2.0)
                time.sleep(wait)
                continue
        except Exception as e:
            attempts_info.append({"attempt": attempt, "proxy": chosen_proxy, "error": repr(e)})
        # backoff entre tentativas (exponencial leve + jitter)
        wait = min_delay * (2 ** (attempt - 1)) + random.uniform(0, max_delay)
        time.sleep(wait)

    # todas as tentativas falharam
    raise RuntimeError({"message": "humanized_fetch failed", "url": url, "attempts": attempts_info})

def get_public_ip(use_proxy: bool = None, proxy_url: str = None, timeout: int = 8) -> dict:
    """
    Retorna o IP público que a requisição exibirá para a internet.
    - use_proxy:
       * True -> força usar proxy_url (se fornecido) ou env PROXY_URL
       * False -> ignora variáveis de proxy (trust_env=False)
       * None -> respeita variáveis de ambiente (default)
    Retorna dict: {"ip": "x.x.x.x", "via": "direct"|"proxy", "raw": <response>}
    """
    svc = "https://api.ipify.org?format=json"
    # escolher comportamento de proxy
    if proxy_url:
        # usa proxy explicitly
        session = requests.Session()
        session.trust_env = False
        session.proxies.update({"http": proxy_url, "https": proxy_url})
        try:
            r = session.get(svc, timeout=timeout)
            r.raise_for_status()
            return {"ip": r.json().get("ip"), "via": "proxy", "raw": r.text}
        except Exception as e:
            raise RuntimeError({"message": "public_ip check failed (proxy)", "error": repr(e)})
    else:
        # decidir trust_env conforme use_proxy
        if use_proxy is True:
            # usa PROXY_URL do env se existir
            proxy_env = os.environ.get("PROXY_URL")
            if proxy_env:
                return get_public_ip(use_proxy=None, proxy_url=proxy_env, timeout=timeout)
            # se não houver proxy, cai para direct
        if use_proxy is False:
            session = requests.Session()
            session.trust_env = False
            try:
                r = session.get(svc, timeout=timeout)
                r.raise_for_status()
                return {"ip": r.json().get("ip"), "via": "direct", "raw": r.text}
            except Exception as e:
                raise RuntimeError({"message": "public_ip check failed (direct)", "error": repr(e)})
        # default: respect env proxy
        session = requests.Session()
        session.trust_env = True
        try:
            r = session.get(svc, timeout=timeout)
            r.raise_for_status()
            # determinar se saiu via proxy olhando variáveis de ambiente
            via = "proxy" if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") else "direct"
            return {"ip": r.json().get("ip"), "via": via, "raw": r.text}
        except Exception as e:
            raise RuntimeError({"message": "public_ip check failed (env)", "error": repr(e)})

# ---------------------------
# modificar fetch_despesas_table para aceitar humanized=True
# ---------------------------

def find_relatorio_href(html, base=BASE_SITE):
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a", href=lambda h: h and "relatorio.php" in h)
    if a:
        return urljoin(base, a['href'])
    a2 = soup.find(lambda tag: tag.name == "a" and "Opções para exportação" in tag.get_text())
    if a2 and a2.get("href"):
        return urljoin(base, a2['href'])
    return None

def table_to_json(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=lambda i: i and "despesas" in i.lower())
    if table is None:
        table = soup.find("table")
        if table is None:
            return None
    headers = []
    thead = table.find("thead")
    if thead:
        header_rows = thead.find_all("tr")
        if header_rows:
            last = header_rows[-1]
            headers = [th.get_text(strip=True) for th in last.find_all(["th", "td"])]
    if not headers:
        first_row = table.find("tr")
        if first_row:
            headers = [f"col_{i}" for i,_ in enumerate(first_row.find_all(["td","th"]))]
    rows = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        row = {}
        for i, v in enumerate(values):
            key = headers[i] if i < len(headers) else f"col_{i}"
            row[key] = v
        rows.append(row)
    return rows

def fetch_despesas_table(year=None, custom_headers=None, cookies=None, humanized: bool = False, ua: str = None, timeout: int = 15):
    """
    Se humanized=True, usa humanized_fetch para reduzir chance de bloqueio.
    Opcionalmente passe ua (string) para forçar User-Agent.
    """
    despesas_url = urljoin(BASE_SITE, DESPESAS_PATH)
    if humanized:
        html = humanized_fetch(despesas_url, ua=ua, referrer=BASE_SITE, cookies=cookies, timeout=timeout)
    else:
        html = fetch_url(despesas_url, custom_headers=custom_headers, cookies=cookies, timeout=timeout)

    relatorio_url = find_relatorio_href(html, base=despesas_url)
    if not relatorio_url:
        raise RuntimeError("Não encontrou relatorio.php na página de despesas")
    if year:
        p = urlparse(relatorio_url)
        qs = parse_qs(p.query)
        qs['ANO'] = [year]
        new_q = urlencode({k: v[0] for k, v in qs.items()})
        relatorio_url = urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))

    # buscar relatório (usar humanized também)
    if humanized:
        rel_html = humanized_fetch(relatorio_url, ua=ua, referrer=despesas_url, cookies=cookies, timeout=timeout)
    else:
        rel_html = fetch_url(relatorio_url, custom_headers=custom_headers, cookies=cookies, timeout=timeout)

    rows = table_to_json(rel_html)
    if rows is None:
        raise RuntimeError("Não encontrou tabela de despesas na página de relatório")
    return {"source": relatorio_url, "rows_count": len(rows), "rows": rows}
