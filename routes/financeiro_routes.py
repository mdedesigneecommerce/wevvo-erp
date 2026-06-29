"""Blueprint do módulo Financeiro.

Sprint 03: fundação segura.
As rotas financeiras ainda permanecem no app.py.
"""

from flask import Blueprint

financeiro_bp = Blueprint("financeiro", __name__)

__all__ = ["financeiro_bp"]
