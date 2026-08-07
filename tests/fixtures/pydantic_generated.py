"""Generated Pydantic models from OKF schema contracts."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegistroConcept(BaseModel):
    """Generated Pydantic model for one OKF contract object."""

    model_config = ConfigDict(extra="allow")

    description: str = Field(default=None)
    id_externo: UUID
    title: str = Field(default=None)
    type: Literal["Registro"]
    valor: Decimal
