"""Blueprint do módulo Estoque.

Sprint 03: fundação segura.
As rotas de Estoque ainda permanecem no app.py.
"""

from flask import Blueprint

estoque_bp = Blueprint("estoque", __name__)

__all__ = ["estoque_bp"]
