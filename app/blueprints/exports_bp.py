from flask import Blueprint, jsonify, request
from ..utils import fetch_despesas_table
from .. import db
from ..models import ExportRecord
import json

exports_bp = Blueprint("exports", __name__)

def _bool_param(val, default=False):
    if val is None:
        return default
    return str(val).lower() in ("1", "true", "yes", "y")

@exports_bp.route("/despesas", methods=["GET"])
def export_despesas():
    """
    GET /export/despesas
    Query params:
      - save=1       -> salvar no DB
      - year=YYYY    -> ano para forçar no relatorio
      - ua=...       -> user-agent custom (opcional)
      - humanized=0|1 -> por padrão 1 (humanized). Set 0 para comportamento original.
      - ua param and others passed to fetch_despesas_table
    """
    save = _bool_param(request.args.get("save"), default=False)
    year = request.args.get("year")
    ua = request.args.get("ua")
    humanized = _bool_param(request.args.get("humanized"), default=True)

    headers = None
    if ua:
        headers = {"User-Agent": ua}

    try:
        result = fetch_despesas_table(year=year, custom_headers=headers, humanized=humanized, ua=ua)
        if save:
            record = ExportRecord(
                source_url=result.get("source"),
                year=year or "",
                raw_json=json.dumps(result, ensure_ascii=False)
            )
            db.session.add(record)
            db.session.commit()
            result['saved_id'] = record.id
        return jsonify(result)
    except RuntimeError as re:
        detail = re.args[0] if re.args else str(re)
        return jsonify({"error": "Falha ao buscar/exportar despesas", "detail": detail}), 502
    except Exception as e:
        return jsonify({"error": "Falha ao buscar/exportar despesas", "detail": str(e)}), 500

@exports_bp.route("/list_saved", methods=["GET"])
def list_saved():
    items = ExportRecord.query.order_by(ExportRecord.created_at.desc()).limit(50).all()
    return jsonify([i.to_dict() for i in items])

@exports_bp.route("/import", methods=["POST"])
def import_export():
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": "Corpo inválido ou não-JSON", "detail": str(e)}), 400

    if not payload or 'rows' not in payload:
        return jsonify({"error": "JSON deve conter 'rows'"}), 400

    rec = ExportRecord(
        source_url=payload.get("source") or "",
        year="",
        raw_json=json.dumps(payload, ensure_ascii=False)
    )
    db.session.add(rec)
    db.session.commit()
    return jsonify({"saved_id": rec.id}), 201
