import sys
sys.path.insert(0, ".")
import uvicorn
if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8801)
