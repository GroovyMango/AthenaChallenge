from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import requests
import sys

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

@app.get("/")
async def health_check():
    return {"status": "USGS Earthquake MCP Online. Route AI traffic to /mcp"}

@app.post("/mcp")
@app.post("/")
async def mcp_router(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        print(f"JSON Parse Error: {e}", file=sys.stderr)
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}

    method = body.get("method")
    request_id = body.get("id")

    if request_id is None and method and method.startswith("notifications/"):
        return Response(status_code=204)

    # 1. Initialization Handshake
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "EarthquakeExplorer", "version": "1.0.0"}
            }
        }

    # 2. Define the Tool
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "get_recent_earthquakes",
                        "description": "Fetches recent earthquakes based on time range and minimum magnitude.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "start_date": { "type": "string", "description": "Start date in YYYY-MM-DD format" },
                                "end_date": { "type": "string", "description": "End date in YYYY-MM-DD format" },
                                "min_magnitude": { "type": "number", "description": "Minimum earthquake magnitude (e.g., 4.5)" }
                            },
                            "required": ["start_date", "end_date", "min_magnitude"]
                        }
                    }
                ]
            }
        }

    # 3. Execute the Tool
    elif method == "tools/call":
        args = body.get("params", {}).get("arguments", {})
        if body.get("params", {}).get("name") == "get_recent_earthquakes":
            start = args.get("start_date", "2024-01-01")
            end = args.get("end_date", "2024-01-07")
            min_mag = args.get("min_magnitude", 3.0)
            
            # Limit to 50 results to keep the payload clean and fast
            api_url = f"https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime={start}&endtime={end}&minmagnitude={min_mag}&limit=50"
            
            try:
                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    # Clean the data: extract only place, magnitude, time, and depth
                    earthquakes = [
                        {
                            "place": f["properties"]["place"],
                            "magnitude": f["properties"]["mag"],
                            "depth_km": f["geometry"]["coordinates"][2]
                        }
                        for f in data.get("features", [])
                    ]
                    output_text = str(earthquakes)
                else:
                    output_text = f"API Error: {response.status_code}"
            except Exception as error:
                output_text = f"Connection failure: {str(error)}"

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": { "content": [{"type": "text", "text": output_text}] }
            }

    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method '{method}' not found"}}