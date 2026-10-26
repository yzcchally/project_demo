"""SilverGuide application entry point. Legacy demo URLs remain compatible."""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from gmail_demo import app

app.title = "SilverGuide Agent API"
app.version = "2.0.0"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
