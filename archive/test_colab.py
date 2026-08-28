import requests, os

URL = os.environ.get("OLLAMA_URL", "https://tribunal-ask-restructuring-epa.trycloudflare.com")

print(f"Testing connection to: {URL}")

# 1. Check if ngrok tunnel is alive
try:
    r = requests.get(URL, headers={"ngrok-skip-browser-warning": "true"}, timeout=10)
    print(f"OK Tunnel reachable — status {r.status_code}")
except Exception as e:
    print(f"FAIL Tunnel unreachable: {e}")
    exit()

# 2. Check Ollama is running
try:
    r = requests.get(f"{URL}/api/tags", headers={"ngrok-skip-browser-warning": "true"}, timeout=10)
    print(f"Status: {r.status_code}")
    print(f"Body: {r.text[:500]}")
    models = [m["name"] for m in r.json().get("models", [])]
    print(f"OK Ollama running — models available: {models}")
    if "qwen2.5:14b" not in models:
        print("WARN  qwen2.5:14b not found — is the pull complete?")
except Exception as e:
    print(f"FAIL Ollama not responding: {e}")
    exit()

# 3. Test actual inference
print("Testing inference (may take 30s)...")
try:
    r = requests.post(
        f"{URL}/api/generate",
        headers={"ngrok-skip-browser-warning": "true"},
        json={"model": "qwen2.5:14b", "prompt": "Reply with only: OK", "stream": False, "options": {"num_predict": 5}},
        timeout=60
    )
    response = r.json().get("response", "").strip()
    print(f"OK Inference works — model replied: '{response}'")
except Exception as e:
    print(f"FAIL Inference failed: {e}")
