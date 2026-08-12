from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import asyncio
import models, schemas
from database import engine, get_db, SessionLocal
from agent import OdooClient, PiERPClient, ERPImplementationAgent
from security import encrypt_password, decrypt_password
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="ERP Agentic Implementation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Welcome to PI ERP Implementation Agent API"}

@app.get("/settings/", response_model=list[schemas.Setting])
def read_settings(db: Session = Depends(get_db)):
    return db.query(models.Setting).all()

@app.post("/settings/")
def update_settings(settings: list[schemas.SettingCreate], db: Session = Depends(get_db)):
    for setting in settings:
        db_setting = db.query(models.Setting).filter(models.Setting.key == setting.key).first()
        if db_setting:
            db_setting.value = setting.value
        else:
            new_setting = models.Setting(key=setting.key, value=setting.value)
            db.add(new_setting)
    db.commit()
    return {"message": "Settings updated"}

# --- Organizations ---
@app.post("/organizations/", response_model=schemas.Organization)
def create_organization(org: schemas.OrganizationCreate, db: Session = Depends(get_db)):
    db_org = models.Organization(**org.model_dump())
    db.add(db_org)
    db.commit()
    db.refresh(db_org)
    return db_org

@app.get("/organizations/", response_model=list[schemas.Organization])
def read_organizations(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Organization).offset(skip).limit(limit).all()

# --- Projects ---
@app.post("/projects/", response_model=schemas.Project)
def create_project(project: schemas.ProjectCreate, db: Session = Depends(get_db)):
    db_proj = models.Project(**project.model_dump())
    db.add(db_proj)
    db.commit()
    db.refresh(db_proj)
    return db_proj

@app.get("/projects/", response_model=list[schemas.Project])
def read_projects(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Project).offset(skip).limit(limit).all()

@app.get("/projects/{project_id}", response_model=schemas.Project)
def read_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@app.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Delete related records
    db.query(models.Interaction).filter(models.Interaction.project_id == project_id).delete()
    db.query(models.Instance).filter(models.Instance.project_id == project_id).delete()
    
    db.delete(project)
    db.commit()
    return {"status": "success", "message": "Project deleted successfully"}

# --- Instances (formerly profiles) ---
@app.post("/instances/detect")
async def detect_instance(req: schemas.DetectRequest):
    req.url = req.url.rstrip("/")
    if req.erp_type == "odoo":
        try:
            import xmlrpc.client
            def get_dbs():
                common = xmlrpc.client.ServerProxy(f'{req.url}/xmlrpc/2/common')
                # Try to list dbs via db.list instead of common?
                # Actually, /xmlrpc/2/db is the correct endpoint for list
                db_proxy = xmlrpc.client.ServerProxy(f'{req.url}/xmlrpc/2/db')
                return db_proxy.list()
                
            dbs = await asyncio.wait_for(asyncio.to_thread(get_dbs), timeout=5.0)
            return {"databases": dbs, "suggested_username": "admin"}
        except Exception as e:
            return {"databases": [], "suggested_username": "admin", "error": str(e)}
    
    return {"databases": [], "suggested_username": "admin"}

@app.post("/instances/", response_model=schemas.Instance)
def create_instance(instance: schemas.InstanceCreate, db: Session = Depends(get_db)):
    data = instance.model_dump()
    data["password"] = encrypt_password(data["password"])
    db_inst = models.Instance(**data)
    db.add(db_inst)
    db.commit()
    db.refresh(db_inst)
    return db_inst

@app.get("/projects/{project_id}/instances/", response_model=list[schemas.Instance])
def read_project_instances(project_id: int, db: Session = Depends(get_db)):
    return db.query(models.Instance).filter(models.Instance.project_id == project_id).all()

@app.post("/instances/{instance_id}/test-connection")
async def test_connection(instance_id: int, db: Session = Depends(get_db)):
    instance = db.query(models.Instance).filter(models.Instance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    try:
        pwd = decrypt_password(instance.password)
        if instance.erp_type == "odoo":
            client = await asyncio.to_thread(OdooClient, instance.url, instance.db_name, instance.username, pwd)
            return {"status": "success", "message": f"Successfully connected to Odoo at {instance.url}"}
        elif instance.erp_type == "pi_erp":
            client = PiERPClient(instance.url, instance.username, pwd)
            return {"status": "success", "message": f"Successfully connected to Pi ERP at {instance.url}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")
    
    raise HTTPException(status_code=400, detail="Unknown ERP type")

# --- Chat Endpoint ---
class ChatRequest(schemas.BaseModel):
    message: str
    resume_action: str | None = None

@app.post("/projects/{project_id}/chat")
async def chat_with_agent(project_id: int, req: ChatRequest, db: Session = Depends(get_db)):
    # Save user message
    user_interaction = models.Interaction(project_id=project_id, role="user", content=req.message)
    db.add(user_interaction)
    db.commit()

    # Get chat history (truncate to last 20 messages)
    interactions = db.query(models.Interaction).filter(
        models.Interaction.project_id == project_id,
        models.Interaction.id < user_interaction.id # Exclude the one we just added to pass as history
    ).order_by(models.Interaction.created_at.desc()).limit(20).all()
    interactions.reverse()

    # Find project's connected instance
    instance = db.query(models.Instance).filter(models.Instance.project_id == project_id).first()
    if not instance:
        raise HTTPException(status_code=400, detail="No connected ERP instance for this project.")

    try:
        pwd = decrypt_password(instance.password)
        if instance.erp_type == "odoo":
            client = await asyncio.to_thread(OdooClient, instance.url.strip(), instance.db_name.strip(), instance.username.strip(), pwd.strip())
        else:
            client = PiERPClient(instance.url, instance.username, pwd)
        
        # Fetch OpenRouter settings from DB
        openrouter_key_setting = db.query(models.Setting).filter(models.Setting.key == "openrouter_api_key").first()
        llm_model_setting = db.query(models.Setting).filter(models.Setting.key == "llm_model_name").first()
        
        api_key = openrouter_key_setting.value if openrouter_key_setting else None
        llm_model = llm_model_setting.value if llm_model_setting else "gpt-4o-mini"
        base_url = "https://openrouter.ai/api/v1" if openrouter_key_setting else None
        
        agent = ERPImplementationAgent(
            instance.erp_type, 
            client, 
            workspace_path=instance.project.workspace_path if instance.project else f"workspaces/project_{project_id}",
            llm_model=llm_model, 
            api_key=api_key, 
            base_url=base_url
        )
        
        async def event_generator():
            full_response = ""
            try:
                # Use project_id as thread_id for now
                async for chunk in agent.astream(req.message, thread_id=str(project_id), interactions=interactions, resume_action=req.resume_action):
                    full_response += chunk
                    yield chunk
            except Exception as e:
                error_msg = f"\n\n[Agent Error]: {str(e)}"
                full_response += error_msg
                yield error_msg
            finally:
                # Save agent response when stream finishes
                with SessionLocal() as new_db:
                    agent_interaction = models.Interaction(project_id=project_id, role="agent", content=full_response)
                    new_db.add(agent_interaction)
                    new_db.commit()

        return StreamingResponse(event_generator(), media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent setup error: {str(e)}")

@app.get("/projects/{project_id}/chat")
def get_chat_history(project_id: int, db: Session = Depends(get_db)):
    interactions = db.query(models.Interaction).filter(models.Interaction.project_id == project_id).order_by(models.Interaction.created_at).all()
    return interactions
