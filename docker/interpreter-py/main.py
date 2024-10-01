from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import sys

app = FastAPI()


class CodeExecution(BaseModel):
    code: str


@app.post("/execute")
async def execute_code(code_execution: CodeExecution):
    try:
        result = subprocess.run(
            [sys.executable, "-c", code_execution.code],
            capture_output=True, text=True, timeout=30
            )
        return {
            "stdout"    : result.stdout,
            "stderr"    : result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Execution timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)