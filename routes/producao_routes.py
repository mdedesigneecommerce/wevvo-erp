"""Blueprint do módulo Produção/PCP.

Sprint 03: fundação segura.
As rotas de Produção e PCP ainda permanecem no app.py.
"""

from flask import Blueprint

producao_bp = Blueprint("producao", __name__)

__all__ = ["producao_bp"]
