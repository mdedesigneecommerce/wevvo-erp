"""Blueprint do módulo Dashboard.

Sprint 03: fundação segura.
As rotas do Dashboard ainda permanecem no app.py.
"""

from flask import Blueprint

dashboard_bp = Blueprint("dashboard", __name__)

__all__ = ["dashboard_bp"]
