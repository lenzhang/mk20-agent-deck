import json, os, struct, subprocess, sys, time, urllib.request, zlib
CLI = "/tmp/glmsmoke/build/oai_cli"; BASE = "https://open.bigmodel.cn/api/coding/paas/v4"
key = json.load(open(os.path.expanduser("~/.claude/settings-glm.json")))["env"]["ANTHROPIC_AUTH_TOKEN"].strip()
def bmp(path, w=256, h=128):
    rows = []
    for y in range(h):
        r = bytearray()
        for x in range(w):
            if 20 <= x < 100 and 30 <= y < 100: r += bytes((0, 0, 255))      # BGR red square left
            elif (x-190)**2 + (y-64)**2 < 35**2: r += bytes((255, 0, 0))   # blue circle right
            else: r += bytes((255, 255, 255))
        rows.append(bytes(r))
    data = b"".join(reversed(rows))
    hdr = b"BM" + struct.pack("<IHHI", 54 + len(data), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(data), 2835, 2835, 0, 0)
    open(path, "wb").write(hdr + data)
bmp("t.bmp"); subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70", "t.bmp", "--out", "t.jpg"], capture_output=True, check=True)
TOOLS = [{"type": "function", "function": {"name": "click", "description": "Click at normalized coordinates 0..1000",
          "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}}}]
cases = [
  ("text_plain", {"model": "glm-5.3", "system": "You are terse.", "user": "Reply with exactly: MK20 OK"}),
  ("text_tools", {"model": "glm-5.3", "system": "You control a mouse via tools.", "user": "Click the center of the screen (x=500,y=500). Use the click tool.", "tools": TOOLS}),
  ("image_plain", {"model": "glm-5.3-flash", "system": "You are terse.", "user": "Describe the shapes, their colors and which side each is on, in one sentence.", "jpeg_file": "t.jpg"}),
  ("image_tools", {"model": "glm-5.3-flash", "system": "You control a mouse via tools. Coordinates are normalized 0..1000.", "user": "Click the center of the red square.", "jpeg_file": "t.jpg", "tools": TOOLS}),
]
out = {"jpeg_bytes": os.path.getsize("t.jpg"), "image_px": "256x128", "cases": []}
for name, req in cases:
    req = dict(req, base_url=BASE, max_tokens=512, temperature=0)
    json.dump(req, open("req.json", "w"))
    b = subprocess.run([CLI, "request", "req.json"], capture_output=True, text=True)
    body = b.stdout.strip().encode()
    if b.returncode: out["cases"].append({"case": name, "build_error": b.stdout}); continue
    r = urllib.request.Request(BASE + "/chat/completions", data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(r, timeout=120) as resp: status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e: status, raw = e.code, e.read()
    ms = round((time.monotonic() - t0) * 1000)
    open("resp.json", "wb").write(raw)
    p = subprocess.run([CLI, "parse", str(status), "resp.json"], capture_output=True, text=True)
    try: j = json.loads(raw); keys = sorted(j.keys()); usage = j.get("usage"); msgkeys = sorted(j["choices"][0]["message"].keys()) if "choices" in j else None
    except Exception: keys = usage = msgkeys = None
    out["cases"].append({"case": name, "model": req["model"], "http": status, "ms": ms, "req_bytes": len(body), "top_keys": keys,
                         "message_keys": msgkeys, "usage": usage, "parsed": json.loads(p.stdout) if p.stdout.strip() else p.stderr[:300]})
s = json.dumps(out, ensure_ascii=False, indent=1)
assert key not in s
print(s)
