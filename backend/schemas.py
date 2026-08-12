from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

# Interaction Schemas
class InteractionBase(BaseModel):
    role: str
    content: str

class InteractionCreate(InteractionBase):
    project_id: int

class Interaction(InteractionBase):
    id: int
    project_id: int
    created_at: datetime

    class Config:
        from_attributes = True

# Instance Schemas (formerly ERPProfile)
class InstanceBase(BaseModel):
    erp_type: str
    url: str
    db_name: Optional[str] = None
    username: str

class InstanceCreate(InstanceBase):
    password: str
    project_id: int

class Instance(InstanceBase):
    id: int
    project_id: int
    created_at: datetime

    class Config:
        from_attributes = True

# Project Schemas
class ProjectBase(BaseModel):
    name: str
    workspace_path: Optional[str] = None

class ProjectCreate(ProjectBase):
    organization_id: int

class Project(ProjectBase):
    id: int
    organization_id: int
    created_at: datetime
    instances: List[Instance] = []

    class Config:
        from_attributes = True

# Organization Schemas
class OrganizationBase(BaseModel):
    name: str

class OrganizationCreate(OrganizationBase):
    pass

class Organization(OrganizationBase):
    id: int
    created_at: datetime
    projects: List[Project] = []

    class Config:
        from_attributes = True

class DetectRequest(BaseModel):
    url: str
    erp_type: str

class SettingBase(BaseModel):
    key: str
    value: str

class SettingCreate(SettingBase):
    pass

class Setting(SettingBase):
    updated_at: datetime

    class Config:
        from_attributes = True
