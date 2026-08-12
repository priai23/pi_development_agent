from database import SessionLocal
import models

with SessionLocal() as db:
    orgs = db.query(models.Organization).all()
    for org in orgs:
        org.monthly_budget_usd = 100.0
    
    projects = db.query(models.Project).all()
    for proj in projects:
        proj.monthly_budget_usd = 100.0
        
    db.commit()
    print("Budget set successfully for all organizations and projects!")
