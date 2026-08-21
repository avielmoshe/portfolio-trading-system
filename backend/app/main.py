from fastapi import FastAPI

app = FastAPI(title="Portfolio Trading System")

@app.get("/")
def read_root():
    return {"status": "System is running", "db_connected": "Pending..."}