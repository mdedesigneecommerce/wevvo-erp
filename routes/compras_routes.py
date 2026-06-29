"""Blueprint do módulo Compras.

Sprint 03: fundação segura.
As rotas de Compras ainda permanecem no app.py.
"""

from flask import Blueprint

compras_bp = Blueprint("compras", __name__)

__all__ = ["compras_bp"]
