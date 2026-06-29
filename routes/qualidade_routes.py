"""Blueprint do módulo Qualidade.

Sprint 03: fundação segura.
As rotas de Qualidade ainda permanecem no app.py.
"""

from flask import Blueprint

qualidade_bp = Blueprint("qualidade", __name__)

__all__ = ["qualidade_bp"]
