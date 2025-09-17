from flask import Flask, jsonify, request, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json

# Config
BASE_SITE = "https://www.generalsampaio.ce.gov.br"
DESPESAS_PATH = "/despesas.php"  # caminho inicial
USER_AGENT = "Mozilla/5.0 (compatible; ExecutivePC/1.0; +https://www.cne.org/)"

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///despesas.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Modelo simples para salvar o JSON cru e meta
class ExportRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    source_url = db.Column(db.String(1024))
    year = db.Column(db.String(16))
    raw_json = db.Column(db.Text)

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "source_url": self.source_url,
            "year": self.year,
            "raw_json": json.loads(self.raw_json) if self.raw_json else None
        }

# util: buscar url com headers básicos
def fetch_url(url, params=None, timeout=15):
    headers = {"User-Agent": USER_AGENT, "Referer": BASE_SITE}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text

# util: extrair href do botão "Opções para exportação" na página despesas.php
def find_relatorio_href(html, base=BASE_SITE):
    soup = BeautifulSoup(html, "html.parser")
    # procuramos um <a> cujo texto contenha "Opções para exportação" ou href contendo 'relatorio.php'
    a = soup.find("a", href=lambda h: h and "relatorio.php" in h)
    if a:
        return urljoin(base, a['href'])
    # fallback: procurar por texto
    a2 = soup.find(lambda tag: tag.name == "a" and "Opções para exportação" in tag.get_text())
    if a2 and a2.get("href"):
        return urljoin(base, a2['href'])
    return None

# util: converter <table id="despesas"> para lista de dicts
def table_to_json(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=lambda i: i and "despesas" in i.lower())
    if table is None:
        # tenta qualquer tabela visível como fallback
        table = soup.find("table")
        if table is None:
            return None

    # headers
    headers = []
    thead = table.find("thead")
    if thead:
        header_rows = thead.find_all("tr")
        # usar última linha do thead como cabeçalho se várias linhas
        if header_rows:
            last = header_rows[-1]
            headers = [th.get_text(strip=True) for th in last.find_all(["th", "td"])]
    if not headers:
        # tenta extrair do primeiro tr do tbody ou do table
        first_row = table.find("tr")
        if first_row:
            headers = [f"col_{i}" for i,_ in enumerate(first_row.find_all(["td","th"]))]

    # body rows
    rows = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        # pular linhas vazias ou linhas de cabeçalho repetidas
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        # extrair texto por célula
        values = [cell.get_text(" ", strip=True) for cell in cells]
        # alinhar com headers
        row = {}
        for i, v in enumerate(values):
            key = headers[i] if i < len(headers) else f"col_{i}"
            # manter string cru; conversões numéricas podem ser feitas depois
            row[key] = v
        rows.append(row)
    return rows

@app.route("/export/despesas", methods=["GET"])
def export_despesas():
    """
    Endpoint: /export/despesas
    Query params:
      - save=1  -> salva o JSON no sqlite
      - year=YYYY -> opcional, sobrescreve/força ano na URL do relatorio (se aplicável)
    """
    save = request.args.get("save", "0") in ("1", "true", "yes")
    year = request.args.get("year")

    try:
        # 1) buscar página principal despesas.php
        despesas_url = urljoin(BASE_SITE, DESPESAS_PATH)
        html = fetch_url(despesas_url)

        # 2) localizar link relatorio.php
        relatorio_url = find_relatorio_href(html, base=despesas_url)
        if not relatorio_url:
            return jsonify({"error": "Não encontrei o link relatorio.php na página de despesas."}), 404

        # se o user passou year, substituir/colar no query string
        if year:
            # simples: se "ANO=" estiver no link, trocar; senão adicionar parámetro
            if "ANO=" in relatorio_url:
                # trocar manualmente
                from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
                p = urlparse(relatorio_url)
                qs = parse_qs(p.query)
                qs['ANO'] = [year]
                new_q = urlencode({k: v[0] for k,v in qs.items()})
                relatorio_url = urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))
            else:
                # adicionar ano
                relatorio_url = relatorio_url + ("&" if "?" in relatorio_url else "?") + f"ANO={year}"

        # 3) buscar página relatorio.php
        rel_html = fetch_url(relatorio_url)

        # 4) procurar botão JSON (apenas para verificar) - não usamos o onclick, apenas confirmamos
        soup = BeautifulSoup(rel_html, "html.parser")
        json_btn = soup.find("a", attrs={"title": "JSON"})
        # se necessário, poderia inspecionar onclick para ver isso, mas geralmente o que queremos é a tabela.

        # 5) converter tabela para JSON
        rows = table_to_json(rel_html)
        if rows is None:
            return jsonify({"error": "Não encontrei tabela de despesas para exportar."}), 404

        result = {
            "source": relatorio_url,
            "rows_count": len(rows),
            "rows": rows
        }

        if save:
            record = ExportRecord(source_url=relatorio_url, year=year or "", raw_json=json.dumps(result, ensure_ascii=False))
            db.session.add(record)
            db.session.commit()
            result['saved_id'] = record.id

        return jsonify(result)

    except requests.HTTPError as e:
        return jsonify({"error": "Erro HTTP ao buscar páginas", "detail": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "Erro interno", "detail": str(e)}), 500

@app.route("/exports", methods=["GET"])
def list_exports():
    items = ExportRecord.query.order_by(ExportRecord.created_at.desc()).limit(50).all()
    return jsonify([i.to_dict() for i in items])

if __name__ == "__main__":
    # criar DB se não existir
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
