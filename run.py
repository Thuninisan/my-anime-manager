import uvicorn

if __name__ == "__main__":
    uvicorn.run("my_anime_manager.api:app", host="0.0.0.0", port=8000, reload=True)
