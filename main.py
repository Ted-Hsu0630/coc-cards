import os

import uvicorn

from app_factory import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "3848")),
        reload=bool(os.environ.get("DEV")),
    )
