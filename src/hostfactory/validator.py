"""Schema validator for hf templates file"""

import logging
from typing import List

from pydantic import BaseModel
from pydantic import Field
from pydantic import FilePath

logger = logging.getLogger(__name__)


class Attributes(BaseModel):
    """HF Template Attributes"""

    nram: List[str]
    ncpus: List[str]
    ncores: List[str] | None = None
    machine_type: List[str] = Field(alias="type")


class HFTemplate(BaseModel):
    """HF Template"""

    template_id: str = Field(alias="templateId")
    max_number: int = Field(alias="maxNumber")
    attributes: Attributes
    pod_spec: FilePath = Field(alias="podSpec")


class HFTemplates(BaseModel):
    """HF Templates"""

    templates: List[HFTemplate]


def validate(data: dict) -> HFTemplates:
    """Validate the data"""
    return HFTemplates(**data)
