# app/blueprints/browser_bp.py
from flask import Blueprint, request, jsonify
from ..models import ExportRecord
from .. import db
import json
import requests
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

browser_bp = Blueprint("browser", __name__)

def _extract_table_from_page(page):
    js = """
    () => {
      function text(n){ return n? n.innerText.trim().replace(/\\u00A0/g,' ') : ''; }
      const table = document.querySelector('#despesas') || document.querySelector('table');
      if(!table) return {error: 'table_not_found'};
      let headers = [];
      const thead = table.querySelector('thead');
      if(thead){
        const hr = thead.querySelectorAll('tr');
        if(hr.length) {
          const last = hr[hr.length-1];
          headers = Array.from(last.querySelectorAll('th,td')).map(n => text(n));
        }
      }
      if(!headers.length){
        const first = table.querySelector('tr');
        if(first){
          headers = Array.from(first.querySelectorAll('th,td')).map((_,i)=> 'col_' + i);
        }
      }
      const tbody = table.querySelector('tbody') || table;
      const rows = [];
      for(const tr of tbody.querySelectorAll('tr')){
        const cells = Array.from(tr.querySelectorAll('td,th'));
        if(cells.length === 0) continue;
        const vals = cells.map(c => text(c));
        const row = {};
        for(let i=0;i<vals.length;i++){
          const key = headers[i] || ('col_'+i);
          row[key] = vals[i];
        }
        rows.push(row);
      }
      return {rows: rows, headers: headers};
    }
    """
    return page.evaluate(js)

def fetch_with_browser(start_url: str, timeout: int = 30, headful: bool = False, user_agent: str = None):
    """
    Abre o site com playwright (chromium), segue relatorio.php se existir e extrai a tabela.
    Retorna dict { source, rows_count, rows, headers } ou lança Exception.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        context = browser.new_context(
            user_agent=user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.set_default_navigation_timeout(timeout * 1000)
        # navegar para página inicial
        page.goto(start_url, wait_until="domcontentloaded")
        # tentar localizar link relatorio.php e navegar
        rel_a = page.query_selector('a[href*="relatorio.php"]')
        if rel_a:
            href = rel_a.get_attribute("href")
            rel_full = urljoin(start_url, href)
            page.goto(rel_full, wait_until="domcontentloaded")
            source = rel_full
        else:
            # tenta manter a mesma URL se não encontrou relatorio
            source = start_url

        # esperar tabela aparecer
        try:
            page.wait_for_selector('#despesas, table', timeout=timeout * 1000)
        except PlaywrightTimeoutError:
            # ainda assim tentar extrair (pode retornar table_not_found)
            pass

        # extrair
        result = _extract_table_from_page(page)
        browser.close()

    if result.get("error"):
        raise RuntimeError("Não encontrou tabela (browser). Error: " + str(result.get("error")))
    return {"source": source, "rows_count": len(result.get("rows", [])), "rows": result.get("rows"), "headers": result.get("headers")}

@browser_bp.route("/fetch_browser", methods=["GET", "POST"])
def fetch_browser_route():
    """
    Endpoint: /export/fetch_browser
    Params (query or JSON body):
      - url: URL inicial (default https://www.generalsampaio.ce.gov.br/despesas.php)
      - timeout: tempo (segundos, default 30)
      - headful: 1|0 exibir UI (debug)
      - ua: User-Agent (opcional)
      - save: 1|0 salvar no DB
      - post_url: URL para postar o JSON resultante (opcional)
    Retorna JSON com resultado e opcional saved_id.
    """
    if request.is_json:
        params = request.get_json()
    else:
        params = request.args.to_dict()

    url = params.get("url") or "https://www.generalsampaio.ce.gov.br/despesas.php"
    try:
        timeout = int(params.get("timeout", 30))
    except Exception:
        timeout = 30
    headful = str(params.get("headful", "0")).lower() in ("1", "true", "yes", "y")
    ua = params.get("ua")
    save = str(params.get("save", "0")).lower() in ("1", "true", "yes", "y")
    post_url = params.get("post_url")

    try:
        result = fetch_with_browser(start_url=url, timeout=timeout, headful=headful, user_agent=ua)
    except Exception as e:
        return jsonify({"error": "Falha no browser fetch", "detail": str(e)}), 502

    # opcional: salvar no DB
    if save:
        try:
            rec = ExportRecord(source_url=result.get("source"), year="", raw_json=json.dumps(result, ensure_ascii=False))
            db.session.add(rec)
            db.session.commit()
            result['saved_id'] = rec.id
        except Exception as e:
            # não interromper se o save falhar; apenas incluir detalhe
            result['saved_id_error'] = str(e)

    # opcional: postar para endpoint
    if post_url:
        try:
            r = requests.post(post_url, json=result, timeout=20)
            result['post_status'] = r.status_code
            try:
                result['post_response'] = r.json()
            except Exception:
                result['post_response_text'] = r.text[:1000]
        except Exception as e:
            result['post_error'] = str(e)

    return jsonify(result)



from datetime import datetime
import re

def parse_date_ddmmyyyy(date_str):
    """
    Tenta converter uma string no formato dd/mm/yyyy para objeto date.
    """
    try:
        match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", date_str)
        if match:
            day, month, year = map(int, match.groups())
            return datetime(year, month, day).date()
    except Exception:
        pass
    return None

@browser_bp.route("/check_freshness", methods=["GET"])
def check_freshness():
    """
    Endpoint: /export/check_freshness
    Verifica a atualidade do primeiro registro da tabela extraída.
    """
    url = request.args.get("url") or "https://www.generalsampaio.ce.gov.br/despesas.php"
    timeout = int(request.args.get("timeout", 30))

    try:
        result = fetch_with_browser(start_url=url, timeout=timeout)
    except Exception as e:
        return jsonify({"error": "Falha ao buscar dados", "detail": str(e)}), 502

    rows = result.get("rows", [])
    if not rows:
        return jsonify({"error": "Nenhum registro encontrado"}), 404

    first = rows[0]
    data_str = None

    if "Data" in first:
        data_str = first["Data"]
    else:
        for k, v in first.items():
            if k and "data" in k.lower():
                data_str = v
                break

    if not data_str:
        return jsonify({"error": "Não encontrei campo 'Data' no primeiro registro", "first_row": first}), 422

    parsed = parse_date_ddmmyyyy(data_str)
    if not parsed:
        return jsonify({"error": "Formato de data desconhecido", "data_raw": data_str}), 422

    today = datetime.utcnow().date()
    diff_days = (today - parsed).days
    if diff_days < 0:
        diff_days = 0

    atualidade = 1 if diff_days <= 30 else 0

    return jsonify({
        "lst": first,
        "dtUltima": parsed.isoformat(),
        "alert": diff_days,
        "atualidade": atualidade
    })