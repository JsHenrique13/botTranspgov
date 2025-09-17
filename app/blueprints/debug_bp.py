from flask import Blueprint, jsonify, request
from ..utils import get_public_ip

debug_bp = Blueprint("debug", __name__)

def _parse_bool_param(v, default=None):
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "y")

@debug_bp.route("/myip", methods=["GET"])
def myip():
    """
    GET /debug/myip
    Query params:
      - use_proxy=0|1    -> force ignoring proxies (0) or respect env (1)
      - proxy_url=URL    -> force use of a specific proxy (overrides use_proxy)
    Returns JSON: { ip, via, raw } or error
    """
    use_proxy_q = request.args.get("use_proxy")
    proxy_url = request.args.get("proxy_url")

    # interpret param
    use_proxy = None
    if use_proxy_q is not None:
        use_proxy = _parse_bool_param(use_proxy_q, default=None)

    try:
        if proxy_url:
            res = get_public_ip(proxy_url=proxy_url)
        else:
            if use_proxy is None:
                res = get_public_ip()  # respect env by default
            elif use_proxy:
                res = get_public_ip(use_proxy=True)
            else:
                res = get_public_ip(use_proxy=False)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": "Falha ao obter IP público", "detail": str(e)}), 502
