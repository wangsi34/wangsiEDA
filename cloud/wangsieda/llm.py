"""OpenAI-compatible chat client. The credential is read on this host at call time; it never enters logs, events or the UI."""
import json, os, re, time, urllib.request
from . import config as C, store


def _creds():
    """优先读环境变量 WANGSI_LLM_API_KEY / WANGSI_LLM_BASE_URL；否则读 llm_env_file（KEY=VALUE 格式）。"""
    key, base = os.environ.get("WANGSI_LLM_API_KEY"), os.environ.get("WANGSI_LLM_BASE_URL")
    if not (key and base) and C.MODEL_ENV.exists():
        kv = dict(l.strip().split("=", 1) for l in open(C.MODEL_ENV, encoding="utf-8") if "=" in l)
        key, base = kv.get("WANGSI_LLM_API_KEY"), kv.get("WANGSI_LLM_BASE_URL")
    if not (key and base):
        raise RuntimeError("未配置大模型密钥：请设置 WANGSI_LLM_API_KEY 和 WANGSI_LLM_BASE_URL")
    return key, base.rstrip("/")


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


_SSL = None


def ssl_context():
    """Default TLS context; if Python ships without CA certificates (python.org builds on macOS before
    "Install Certificates.command"), fall back to certifi or the system bundle so HTTPS still verifies."""
    global _SSL
    if _SSL is None:
        import ssl
        ctx = ssl.create_default_context()
        if not ctx.get_ca_certs() and not os.environ.get("SSL_CERT_FILE"):
            cands = []
            try:
                import certifi
                cands.append(certifi.where())
            except ImportError:
                pass
            cands += ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]
            for f in cands:
                if os.path.exists(f):
                    ctx.load_verify_locations(cafile=f)
                    break
        _SSL = ctx
    return _SSL


def _post(base, key, body):
    """POST /chat/completions. If the provider rejects JSON mode (HTTP 400), retry once without it; replies are parsed anyway."""
    import urllib.error
    for drop_json_mode in (False, True):
        b = {k: v for k, v in body.items() if not (drop_json_mode and k == "response_format")}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(b).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=int(C.CFG.get("llm_timeout_s", 300)), context=ssl_context()) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and not drop_json_mode and "response_format" in body:
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                raise RuntimeError("TLS certificate check failed: this Python has no CA certificates. On macOS run "
                                   "'/Applications/Python 3.*/Install Certificates.command', or 'pip3 install certifi'") from None
            raise


class Budget(Exception):
    pass


def job_calls(job_id):
    return store.one("SELECT count(*) n, coalesce(sum(prompt_tokens+completion_tokens),0) t FROM llm_calls WHERE job_id=? AND origin='wangsiEDA'",
                     (job_id,))


def ask_json(ctx, agent, messages, purpose, max_calls=12, max_tokens=6000, attempts=2):
    """One logical call = up to `attempts` HTTP requests (an empty/unparseable reply is retried once). Budget is per job."""
    for attempt in range(1, attempts + 1):
        used = job_calls(ctx.job_id)
        if used["n"] >= max_calls:
            ctx.emit("guard", agent="guard", rule="budget.llm_calls", verdict="deny", detail=f"{used['n']}/{max_calls}")
            raise Budget(f"LLM call budget exhausted ({used['n']}/{max_calls})")
        ctx.emit("llm_call", agent=agent, purpose=purpose, attempt=attempt, n_messages=len(messages),
                 prompt_preview=messages[-1]["content"][:400])
        t0 = time.time()
        content, usage, ok, err, parsed = "", {}, False, None, None
        try:
            key, base = _creds()
            # llm_extra: provider-specific fields merged into the request, e.g. DeepSeek thinking mode
            # {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}; llm_max_tokens / llm_timeout_s override the defaults.
            req_body = {"model": C.MODEL, "messages": messages, "temperature": 0,
                        "max_tokens": int(C.CFG.get("llm_max_tokens", max_tokens)), "response_format": {"type": "json_object"}}
            req_body.update(C.CFG.get("llm_extra", {}))
            payload = _post(base, key, req_body)
            content = payload["choices"][0]["message"].get("content") or ""
            usage = payload.get("usage") or {}
            parsed = extract_json(content)
            ok = True
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
        lat = int((time.time() - t0) * 1000)
        store.x("""INSERT INTO llm_calls(job_id, ts, agent, model, purpose, prompt_tokens, completion_tokens, latency_ms, ok, request_json,
                   response_text, origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ctx.job_id, time.time(), agent, C.MODEL, purpose, usage.get("prompt_tokens"), usage.get("completion_tokens"), lat,
                 int(ok), json.dumps(messages, ensure_ascii=False)[:60000], (content or err or "")[:30000], "wangsiEDA"))
        ctx.emit("llm_result", agent=agent, purpose=purpose, ok=ok, latency_ms=lat, error=err,
                 tokens=(usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
                 parsed=parsed if ok else None)
        if ok:
            return parsed
    return None


def ping():
    """One tiny request to check model, key and network before a long run. Returns (ok, message)."""
    try:
        key, base = _creds()
        body = {"model": C.MODEL, "messages": [{"role": "user", "content": 'Reply with the JSON object {"ok": true}.'}],
                "max_tokens": int(C.CFG.get("llm_max_tokens", 2000)), "response_format": {"type": "json_object"}}
        body.update(C.CFG.get("llm_extra", {}))
        t0 = time.time()
        payload = _post(base, key, body)
        content = payload["choices"][0]["message"].get("content") or ""
        return True, f"model {C.MODEL} answered in {time.time() - t0:.1f}s: {content[:80]!r}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
