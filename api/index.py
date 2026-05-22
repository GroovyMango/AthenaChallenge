from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import requests
import sys

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# This is the UI widget that Athena will inject into the chat
HTML_WIDGET = """<!DOCTYPE html>
<html lang="en">
<head>
  <style>
    body { font-family: system-ui, sans-serif; padding: 16px; background: #f9f9f9; }
    .card { background: white; border-radius: 8px; padding: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #eee; }
    th { color: #666; font-weight: 600; }
    button { padding: 8px 16px; margin-right: 8px; cursor: pointer; border: none; border-radius: 6px; background: #111bf5; color: white; font-weight: 600; }
    button.outline { background: #fff; color: #111bf5; border: 1px solid #111bf5; }
  </style>
</head>
<body>
  <div class="card">
    <div style="margin-bottom: 16px;">
      <button id="sort-btn">Sort by Magnitude</button>
      <button id="filter-btn" class="outline">Filter: Shallow (&lt;70km)</button>
    </div>
    <table id="eq-table">
      <thead><tr><th>Location</th><th>Magnitude</th><th>Depth (km)</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <script type="module">
    // Athena injects the tool output here
    let originalData = window.openai?.toolOutput?.earthquakes || [];
    let currentData = [...originalData];
    let showShallowOnly = false;
    let isSorted = false;

    const tbody = document.querySelector("#eq-table tbody");
    const sortBtn = document.querySelector("#sort-btn");
    const filterBtn = document.querySelector("#filter-btn");

    function render() {
      tbody.innerHTML = "";
      currentData.forEach(eq => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${eq.place}</td><td><strong>${eq.magnitude}</strong></td><td>${eq.depth_km}</td>`;
        tbody.appendChild(tr);
      });
    }

    sortBtn.addEventListener("click", () => {
      isSorted = !isSorted;
      if (isSorted) {
        currentData.sort((a, b) => b.magnitude - a.magnitude);
        sortBtn.textContent = "Unsort";
      } else {
        currentData = showShallowOnly ? originalData.filter(eq => eq.depth_km < 70) : [...originalData];
        sortBtn.textContent = "Sort by Magnitude";
      }
      render();
    });

    filterBtn.addEventListener("click", () => {
      showShallowOnly = !showShallowOnly;
      if (showShallowOnly) {
        currentData = currentData.filter(eq => eq.depth_km < 70);
        filterBtn.textContent = "Show All Depths";
      } else {
        currentData = [...originalData];
        if (isSorted) currentData.sort((a, b) => b.magnitude - a.magnitude);
        filterBtn.textContent = "Filter: Shallow (&lt;70km)";
      }
      render();
    });

    window.addEventListener("openai:set_globals", (e) => {
      const globals = e.detail?.globals;
      if (globals?.toolOutput?.earthquakes) {
        originalData = globals.toolOutput.earthquakes;
        currentData = [...originalData];
        if (isSorted) currentData.sort((a, b) => b.magnitude - a.magnitude);
        if (showShallowOnly) currentData = currentData.filter(eq => eq.depth_km < 70);
        render();
      }
    });

    render();
  </script>
</body>
</html>"""

@app.get("/")
async def health_check():
    return {"status": "Online"}

@app.post("/mcp")
@app.post("/")
async def mcp_router(request: Request):
    try:
        body = await request.json()
    except Exception:
        print("[MCP] Failed to parse request body", flush=True)
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}

    method = body.get("method")
    request_id = body.get("id")
    print(f"[MCP] method={method} id={request_id}", flush=True)

    if request_id is None and method and method.startswith("notifications/"):
        return Response(status_code=204)

    # 1. Initialization (Now includes "resources" capability)
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "EarthquakeExplorer", "version": "1.0.0"}
            }
        }

    # 2. Tell Athena we have a skybridge UI file available
    elif method == "resources/list":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "resources": [{
                    "uri": "ui://widget/earthquakes.html",
                    "name": "Earthquakes Widget",
                    "mimeType": "text/html+skybridge"
                }]
            }
        }

    # 3. Athena reads the UI file
    elif method == "resources/read":
        uri = body.get("params", {}).get("uri")
        if uri == "ui://widget/earthquakes.html":
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "contents": [{
                        "uri": uri, 
                        "mimeType": "text/html+skybridge", 
                        "text": HTML_WIDGET,
                        "_meta": {
                            "openai/widgetPrefersBorder": True
                        }
                    }]
                }
            }

    # 4. Define the Tool (Now includes _meta to link it to the UI)
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "tools": [{
                    "name": "get_recent_earthquakes",
                    "description": "Fetches recent earthquakes based on time range.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "start_date": { "type": "string", "description": "YYYY-MM-DD" },
                            "end_date": { "type": "string", "description": "YYYY-MM-DD" },
                            "min_magnitude": { "type": "number" }
                        },
                        "required": ["start_date", "end_date", "min_magnitude"]
                    },
                    "_meta": {
                        "openai/outputTemplate": "ui://widget/earthquakes.html",
                        "openai/toolInvocation/invoking": "Fetching earthquakes",
                        "openai/toolInvocation/invoked": "Fetched earthquakes"
                    }
                }]
            }
        }

    # 5. Fetch Data and Pass to UI via structuredContent
    elif method == "tools/call":
        args = body.get("params", {}).get("arguments", {})
        if body.get("params", {}).get("name") == "get_recent_earthquakes":
            start = args.get("start_date", "2024-01-01")
            end = args.get("end_date", "2024-01-07")
            min_mag = args.get("min_magnitude", 3.0)
            
            api_url = f"https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime={start}&endtime={end}&minmagnitude={min_mag}&limit=50"
            
            try:
                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    earthquakes = [{
                        "place": f["properties"]["place"],
                        "magnitude": f["properties"]["mag"],
                        "depth_km": f["geometry"]["coordinates"][2]
                    } for f in data.get("features", [])]
                    output_text = "Found earthquakes. Rendering widget."
                else:
                    earthquakes = []
                    output_text = "API Error"
            except Exception:
                earthquakes = []
                output_text = "Connection failure"

            # structuredContent is the key! This is what gets passed to window.openai.toolOutput
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": output_text}],
                    "structuredContent": {"earthquakes": earthquakes}
                }
            }

    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}