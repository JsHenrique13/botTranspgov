from flask import Blueprint, jsonify, request
from ..utils import fetch_despesas_table
from datetime import datetime

status_bp = Blueprint("status", __name__)

def parse_date_ddmmyyyy(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    s2 = s.split()[0].replace(".", "/").replace("-", "/")
    try:
        return datetime.strptime(s2, "%d/%m/%Y").date()
    except Exception:
        return None

def _bool_param(val, default=False):
    if val is None:
        return default
    return str(val).lower() in ("1", "true", "yes", "y")

@status_bp.route("/atualidade", methods=["GET"])
def atualidade():
    """
    GET /status/atualidade
    Query params:
      - year=YYYY
      - ua=... (User-Agent)
      - humanized=0|1 (default 1)
    """
    year = request.args.get("year")
    ua = request.args.get("ua")
    humanized = _bool_param(request.args.get("humanized"), default=True)

    headers = None
    if ua:
        headers = {"User-Agent": ua}

    try:
        live = fetch_despesas_table(year=year, custom_headers=headers, humanized=humanized, ua=ua)
    except RuntimeError as re:
        detail = re.args[0] if re.args else str(re)
        return jsonify({"error": "Falha ao buscar ao vivo", "detail": detail}), 502
    except Exception as e:
        return jsonify({"error": "Falha ao buscar ao vivo", "detail": str(e)}), 502

    rows = live.get("rows") or []
    if not rows:
        return jsonify({"error": "Nenhuma linha encontrada nos dados ao vivo"}), 404

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
        "source": live.get("source"),
        "dtUltima_original": data_str,
        "dtUltima_iso": parsed.isoformat(),
        "alert": diff_days,
        "atualidade": atualidade
    })
