"""SQLAlchemy models. Importing this package registers every table on ``Base``."""

from ducan.models.actions import Action, ActionStatus, ActionType, AuditLog
from ducan.models.catalog import PriceHistory, Product, Supplier
from ducan.models.inventory import MovementKind, StockLevel, StockMovement
from ducan.models.sales import PaymentMethod, Sale, SalesImport
from ducan.models.settings import Setting

__all__ = [
    "Action",
    "ActionStatus",
    "ActionType",
    "AuditLog",
    "MovementKind",
    "PaymentMethod",
    "PriceHistory",
    "Product",
    "Sale",
    "SalesImport",
    "Setting",
    "StockLevel",
    "StockMovement",
    "Supplier",
]
