from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import requests
import json
import logging
import time  # <-- New import for tracking request time
import re
app = FastAPI()

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load configuration from external file
with open("config.json", "r") as f:
    CONFIG = json.load(f)

# Logging setup
logging.basicConfig(level=logging.INFO)
success_log = logging.getLogger('success')
error_log = logging.getLogger('error')

fh_success = logging.FileHandler('successful_requests.log')
fh_error = logging.FileHandler('error_requests.log')

formatter = logging.Formatter('%(asctime)s - %(message)s')
fh_success.setFormatter(formatter)
fh_error.setFormatter(formatter)

success_log.addHandler(fh_success)
error_log.addHandler(fh_error)

class CallModel(BaseModel):
    project_name: str
    model_name: str
    prompt: str
    api_token: str

# In-memory store for rate limiting
LAST_REQUEST_TIMESTAMP = {}

@app.post("/callmodel")
async def call_model(data: CallModel):
    model_config = CONFIG.get(data.project_name, {}).get(data.model_name)
    if not model_config:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Rate limiting
    current_time = time.time()
    last_request_time = LAST_REQUEST_TIMESTAMP.get(data.model_name, 0)
    rate_limit = model_config.get("rate_limit", 1)
    
    if current_time - last_request_time < 1/rate_limit:
        # Too soon since the last request
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    LAST_REQUEST_TIMESTAMP[data.model_name] = current_time

    headers = model_config.get("headers", {}).copy()
    params = model_config.get("params", {}).copy()

    # Replace placeholders in headers
    for key, value in headers.items():
        headers[key] = value.replace("$API_TOKEN", data.api_token)

    # Replace placeholders in params
    for key, value in params.items():
        if isinstance(value, str):
            params[key] = value.replace("$PROMPT", data.prompt)
        elif isinstance(value, list) and "$PROMPT" in str(value):
            # If '$PROMPT' is detected in a list, replace it with the appropriate structured format
            new_messages = []
            for item in value:
                if item == "$PROMPT":
                    new_messages.append({"role": "user", "content": data.prompt})
                else:
                    new_messages.append(item)
            params[key] = new_messages

    try:
        response = requests.post(model_config["endpoint"], headers=headers, json=params)
        success_log.info(f"Successful request for model {data.model_name}: {response.json()}")
        return response.json()
    except Exception as e:
        error_log.error(f"Error for model {data.model_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/listmodels")
async def list_models():
    return CONFIG

# New analytics endpoint
@app.get("/analytics")
async def analytics():
    analytics_data = {
        "model_usage": {},
        "average_time": {}
    }

    with open('successful_requests.log', 'r') as f:
        lines = f.readlines()
        times = {}
        for line in lines:
            match = re.search(r"(\d+-\d+-\d+ \d+:\d+:\d+,\d+) - Successful request for model ([\w.-]+) took (\d+\.\d+)", line)
            if match:
                model_name = match.group(2)
                request_time = float(match.group(3))

                if model_name in times:
                    times[model_name].append(request_time)
                else:
                    times[model_name] = [request_time]

                if model_name in analytics_data["model_usage"]:
                    analytics_data["model_usage"][model_name] += 1
                else:
                    analytics_data["model_usage"][model_name] = 1

        for model, time_list in times.items():
            analytics_data["average_time"][model] = sum(time_list) / len(time_list)

    return analytics_data  # Use built-in JSONResponse conversion of FastAPI

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

