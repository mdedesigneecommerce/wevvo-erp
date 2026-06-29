"""Blueprint para futuras APIs consolidadas do Wevvo ERP/PDV.

Sprint 03: fundação segura.
As APIs existentes ainda permanecem no app.py.
"""

from flask import Blueprint

api_bp = Blueprint("api", __name__)

__all__ = ["api_bp"]
