"""The taxonomy's pass-screen reader, run by the portal on its own pass image before the contributor downloads it.

At L-1 the taxonomy sends the uploaded pass screen to GPT-4o with a prompt per stage and copies the
verification_code it reads into the verdict record; the fingerprint linters then compare that code with the
fingerprint of what's in the task. A misread blocks work that really passed. So the portal draws the pass image
itself (web/common.js passImage) and reads it here with the same model and the same prompt; the contributor only
gets the image once it reads back exactly.

taxo_reader_prompts.json is copied word for word from taxonomy v365 (template 6ae027cbd703e0433df78ab0), the
transformer-jsonata-{e537ff9574bb injection, a83f3880c706 prompt, da9b7b4dc1d9 hardness, 6620221be911 oe,
d65f2b79831a rubric} "content" strings (their -rev reviewer copies are identical). If the taxonomy's reader prompt
changes, copy it here again.
"""

import base64
import json
import re
from pathlib import Path

import httpx

from . import config, proxy

PROMPTS = json.loads((Path(__file__).with_name("taxo_reader_prompts.json")).read_text())

_TEXT_FP = re.compile(r"^(\d+) chars — (.*)$", re.S)
# The stored rubric fingerprint (fingerprint.rubric): count, Σweight, weights list, then titles letters (or the
# older "chars") with first/last 40.
_RUBRIC_FP = re.compile(r"^(\d+) criteria, Σweight ([^,]+), weights (\[[^\]]*\]) · Titles (\d+) (chars|letters) — ?(.*)$", re.S)
SHOW = 4  # the taxonomy shows the first and last 4 characters (v317+)


def _cut(length, body):
    """body = the whole normalized text (<= 40 characters) or its first 40 + " … " + last 40, as stored."""
    head = body[:SHOW] if length > SHOW else body
    return head + (" ... " + body[-SHOW:] if length > 2 * SHOW else "")


def taxonomy_line(payload):
    """The verification code exactly as the taxonomy builds it since v360 (plain hyphen, three dots, "weight",
    a pipe) and as its reader is told to write it (the rubric's weights list left out). Hardness has no
    fingerprint: its code is the EVP code."""
    fp = payload.get("fingerprint")
    if not fp:
        return payload.get("code") or ""
    if fp.startswith("(empty"):
        return fp.replace("—", "-")
    rubric = _RUBRIC_FP.match(fp)
    if rubric:
        count, weight, _weights, letters, word, body = rubric.groups()
        return f"{count} criteria, weight {weight} | Titles {letters} {word} - {_cut(int(letters), body)}"
    text = _TEXT_FP.match(fp)
    if text:
        return f"{text.group(1)} chars - {_cut(int(text.group(1)), text.group(2))}"
    return fp


# The linters' $requireCheck: while it's false there, a read that drops the code's "check NNNNN" still passes in
# Outlier, so it passes here too. A read with the wrong check digits never does (the linters block it).
REQUIRE_CHECK = False


def _norm(code):
    """What the fingerprint linters ignore: runs of whitespace, the kind of dash, ellipsis vs three dots,
    and the case and spacing of the trailing "check NNNNN"."""
    code = re.sub(r"[–—−]", "-", (code or "").replace("…", "..."))
    code = re.sub(r",\s*weights\s*\[[^\]]*\]", "", code)  # a reader that kept the rubric weights list
    code = re.sub(r"\s*check\s*([0-9]{5})\s*$", r" check \1", code, flags=re.I)
    return re.sub(r"\s+", " ", code).strip()


def matches(read, expected):
    return _norm(read) == _norm(expected)


def _parse(text):
    """The reader's reply: a JSON object, often inside ```json fences (the taxonomy matches the first {...})."""
    found = re.search(r"\{[\s\S]*\}", text or "")
    try:
        return json.loads(found.group(0) if found else text)
    except (TypeError, ValueError):
        return {}


async def read(stage, png):
    """Send the image to the taxonomy's reader. Returns its parsed JSON reply ({} if it can't be parsed)."""
    if stage not in PROMPTS:
        raise ValueError(f"no reader prompt for {stage}")
    body = {
        "model": config.READER_MODEL, "temperature": 0, "max_tokens": 1000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPTS[stage]},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        ]}],
    }
    # The taxonomy's own endpoint first; the portal's LiteLLM endpoint if this server can't reach or use that one.
    urls = list(dict.fromkeys([config.READER_URL, f"{config.LITELLM_BASE_URL}/v1/chat/completions"]))
    # The backup key only after the main one answers "budget exceeded" (proxy.over_budget, 2026-11-01).
    keys = [config.READER_API_KEY] + ([config.LITELLM_FALLBACK_API_KEY] if config.LITELLM_FALLBACK_API_KEY else [])
    proj = config.ALLOWED_PROJECT_IDS[0] if config.ALLOWED_PROJECT_IDS else ""
    tag_parts = [f"projectId:{proj}" if proj else "", "useCase:eval-portal", f"stage:reader-{stage}"]
    tags_header = ",".join(p for p in tag_parts if p)
    error = None
    async with httpx.AsyncClient(timeout=60) as client:
        for url in urls:
            for key in keys:
                try:
                    resp = await client.post(url, json=body, headers={
                        "Authorization": f"Bearer {key}",
                        "User-Agent": "eval-portal",
                        "x-litellm-tags": tags_header,
                    })
                    if key != keys[-1] and proxy.over_budget(resp.status_code, resp.content):
                        continue
                    resp.raise_for_status()
                    return _parse(resp.json()["choices"][0]["message"]["content"])
                except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                    error = exc
                    break
    raise error


async def check(payload, png):
    """Read the portal's own pass image like the taxonomy would. ok = the code and the verdict read back exactly."""
    base = taxonomy_line(payload)
    expected = f"{base} check {payload['fingerprint_check']}" if payload.get("fingerprint_check") else base
    reply = await read(payload["stage"], png)
    code = str(reply.get("verification_code") or "")
    verdict = str(reply.get("verdict") or "")
    # An empty input's "(empty - ...)" line is often read as "no code"; the linters skip an empty injection anyway.
    code_ok = (matches(code, expected) or (not REQUIRE_CHECK and matches(code, base))
               or (base.startswith("(empty") and not code.strip()))
    return {"ok": code_ok and verdict == payload.get("verdict"),
            "read": code, "expected": expected, "verdict_read": verdict, "reason": str(reply.get("reason") or "")[:300]}
