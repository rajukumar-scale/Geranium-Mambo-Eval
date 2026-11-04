"""Per-review Modal sandboxes, through Scale's modal-proxy (REVIEW_RUNNER=modal).

The same contract as boatrun, so the runner drives either: a review machine is a sandbox id, `ssh` runs a
shell command in it and returns (returncode, stdout), `scp_to` / `scp_from` copy a file in and out, and
`delete_box` ends it. Each review starts from MODAL_TEMPLATE_IMAGE, a filesystem snapshot holding Claude Code,
the Python tools and the universes at /work, built once by `build_template` (the Modal counterpart of
deploy/build_runner_template.sh + `boat snapshot`).

Differences from boat:
- modal-proxy has no streaming exec, so the runner polls the review's output file (`read_from`). The polling
  execs also reset Modal's idle clock.
- model calls go to the metering proxy (PROXY_PUBLIC_URL), as from a boat box: the sandbox holds only the run
  token, never the LiteLLM key, and metering, the backup key and cancel work unchanged.
- no machine reuse (boxpool is off for Modal): a sandbox starts in seconds and there is no daily start limit.
- modal-proxy authenticates every call with an s2sauth JWT, minted here from AWS credentials (an engineer's SSO
  role on a laptop, the service's IAM role when deployed). Every sandbox is attributed to MODAL_PROJECT_ID.
"""

import asyncio
import base64
import logging
import shlex
import time
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("portal.modal")

S2SAUTH_URL = "https://s2sauth.internal.scale.com"
STS_URL = "https://sts.us-west-2.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"
CHUNK = 6 * 1024 * 1024
_jwt = {"token": None, "at": 0.0}
_client = None


def _http():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=config.MODAL_PROXY_URL,
                                    timeout=httpx.Timeout(connect=30, read=900, write=900, pool=60))
    return _client


def _mint_jwt():
    """An s2sauth JWT for this process's AWS identity: sign an STS GetCallerIdentity and trade it in
    (what s2sauth_python_helper does). Tokens last 15 minutes; refreshed after 12."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import Session

    session = Session(profile=config.MODAL_AWS_PROFILE or None)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("no AWS credentials for s2sauth (log in with aws sso, or set MODAL_AWS_PROFILE)")
    request = AWSRequest(method="GET", url=STS_URL)
    SigV4Auth(creds.get_frozen_credentials(), "sts", "us-west-2").add_auth(request)
    resp = httpx.post(f"{S2SAUTH_URL}/jwt", json={"scopes": [], "stsRequest": {"headers": dict(request.headers), "url": STS_URL}},
                      timeout=20)
    resp.raise_for_status()
    return resp.json()["jwt"]


async def _headers():
    if not _jwt["token"] or time.time() - _jwt["at"] > 12 * 60:
        _jwt["token"] = await asyncio.to_thread(_mint_jwt)
        _jwt["at"] = time.time()
    return {"x-jwt-assertion": f"JWT {_jwt['token']}"}


async def api(method, path, *, json_body=None, params=None, content=None, timeout=300):
    resp = await _http().request(method, path, json=json_body, params=params, content=content,
                                 headers=await _headers(), timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"modal-proxy {method} {path} → {resp.status_code}: {resp.text[-300:]}")
    return resp.json() if resp.content else {}


def _create_body(image_id, timeout, tags, universe_id=None):
    body = {
        "image": config.MODAL_BASE_IMAGE,
        "snapshot_image_id": image_id,
        "tunnel_port": 8080,
        "timeout": timeout,
        "cpu": config.MODAL_CPU,
        "memory": config.MODAL_MEMORY_MB,
        "idle_timeout": 30 * 60,
        "project_id": config.MODAL_PROJECT_ID,
        "product": "atlas-eval",
        "tags": {"app": "atlas-eval", **tags},
    }
    if universe_id:
        body["directory_snapshots"] = [{"snapshot_image_id": universe_id, "mount_path": UNIVERSE_MOUNT}]
    return body


def active_snapshot():
    """(base image, universe snapshot) new review machines start from: the last pair `build_template` made,
    else MODAL_TEMPLATE_IMAGE as "<base>:<universe>". Two images because one 26 GB filesystem snapshot times
    out in modal-proxy, while a directory snapshot of the universes doesn't."""
    try:
        text = (config.DATA_DIR / "modal_template").read_text().strip()
    except OSError:
        text = ""
    base, _, universe = (text or config.MODAL_TEMPLATE_IMAGE).replace(":", " ").partition(" ")
    return base.strip(), universe.strip()


async def create_box(tags=None):
    base, universe = active_snapshot()
    if not base:
        raise RuntimeError("no Modal review image yet: build one with modalrun.build_template()")
    timeout = config.RUN_TIMEOUT_MIN * 60 + 1800
    created = await api("POST", "/sandbox", json_body=_create_body(base, timeout, tags or {}, universe or None),
                        timeout=600)
    return created["sandbox_id"]


async def ssh(box, command, timeout=300):
    """Run a shell command in the sandbox; (returncode, stdout). The boatrun.ssh contract."""
    try:
        out = await api("POST", "/sandbox-exec", json_body={"sandbox_id": box, "command": ["bash", "-lc", command]},
                        timeout=timeout)
    except (RuntimeError, httpx.HTTPError) as error:
        return -1, str(error)
    return out.get("exit_code", -1), (out.get("stdout") or "") + (out.get("stderr") or "")


async def put_bytes(box, remote, data):
    rc, out = await ssh(box, f"mkdir -p {shlex.quote(str(remote).rsplit('/', 1)[0] or '/')}")
    if rc != 0:
        raise RuntimeError(f"mkdir in {box} failed: {out[-300:]}")
    written = await api("POST", "/sandbox-write-file", params={"sandbox_id": box, "target_path": str(remote)},
                        content=data, timeout=900)
    if written.get("bytes_written") != len(data):
        raise RuntimeError(f"upload to {box} truncated: {written.get('bytes_written')} of {len(data)} bytes")


async def scp_to(box, local, remote):
    await put_bytes(box, remote, await asyncio.to_thread(Path(local).read_bytes))


async def scp_from(box, remote, local):
    """Read a file back in base64 chunks over exec. Fine for the review state (tens of MB); phase 2 moves to
    /sandbox-upload-file-to-url with a presigned S3 PUT."""
    rc, out = await ssh(box, f"stat -c %s {shlex.quote(remote)}")
    if rc != 0 or not out.strip().isdigit():
        raise RuntimeError(f"download from {box} failed: {out[-300:]}")
    size, parts = int(out.strip()), []
    for offset in range(0, size, CHUNK):
        rc, out = await ssh(box, f"tail -c +{offset + 1} {shlex.quote(remote)} | head -c {CHUNK} | base64 -w0", timeout=600)
        if rc != 0:
            raise RuntimeError(f"download from {box} failed at byte {offset}: {out[-300:]}")
        parts.append(base64.b64decode(out.strip()))
    data = b"".join(parts)
    if len(data) != size:
        raise RuntimeError(f"download from {box} truncated: {len(data)} of {size} bytes")
    await asyncio.to_thread(Path(local).write_bytes, data)


async def read_from(box, path, offset, limit=4 * 1024 * 1024):
    """The bytes of a growing file from `offset` (at most `limit`), for following a review's output."""
    rc, out = await ssh(box, f"tail -c +{offset + 1} {shlex.quote(path)} 2>/dev/null | head -c {limit} | base64 -w0")
    if rc != 0:
        return None
    return base64.b64decode(out.strip()) if out.strip() else b""


async def delete_box(box):
    try:
        await api("POST", f"/terminate-sandbox/{box}", timeout=120)
    except (RuntimeError, httpx.HTTPError) as error:
        log.warning("could not terminate sandbox %s: %s", box, str(error)[-300:])


async def sweep_orphans(keep=()):
    """modal-proxy has no list call, so leftovers can't be found here. Every review sandbox has a lifetime
    backstop (create_box's timeout) and the 30-minute idle timeout, so a lost one ends by itself."""
    return None


def kill(proc):
    """boatrun.kill's counterpart: Modal reviews have no local process to kill."""
    return None


UNIVERSE_MOUNT = "/universe"
SNAPSHOT_TIMEOUT = 30 * 60

BASE_SH = r"""#!/usr/bin/env bash
set -euo pipefail
exec > /tmp/base.log 2>&1
CLAUDE_VERSION=${CLAUDE_VERSION:-2.1.296}
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
apt-get update -q
apt-get install -y -q --no-install-recommends python3 python3-docx poppler-utils ripgrep unzip curl ca-certificates sudo tzdata procps
curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION"
cp -L "$HOME/.local/bin/claude" /usr/local/bin/claude && chmod 755 /usr/local/bin/claude && /usr/local/bin/claude --version
rm -rf /root/.local /root/.claude /root/.claude.json
id runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash runner
mkdir -p /work
for u in Normal SynthCut; do ln -sfn /universe/Universe_$u /work/Universe_$u; done
apt-get clean; rm -rf /var/lib/apt/lists/*
echo BASE_READY
"""

UNIVERSE_SH = r"""#!/usr/bin/env bash
set -euo pipefail
exec > /tmp/universe.log 2>&1
ZIP_URL=${ZIP_URL:-https://static.remotasks.com/uploads/6a5595596cf3031de47f0ea2}
UNIVERSES=${UNIVERSES:-"Normal SynthCut"}
export LANG=C.UTF-8 LC_ALL=C.UTF-8  # the universes have non-ASCII file names
mkdir -p /universe
for u in $UNIVERSES; do
  for name in HarmonyGames_GitHub HarmonyGames_Gmail HarmonyGames_Other_Services; do
    target="/universe/Universe_$u/${u}_$name"
    [ -d "$target" ] && continue
    zips="${u}_$name"
    [ "$u/$name" = "Normal/HarmonyGames_GitHub" ] && zips="${u}_${name}_part1of3 ${u}_${name}_part2of3 ${u}_${name}_part3of3"
    mkdir -p "/universe/Universe_$u"
    for z in $zips; do
      echo "downloading $z"
      curl -fL --retry 5 --retry-delay 5 -C - -s -o "/tmp/$z.zip" "$ZIP_URL/$z.zip"
      unzip -q -n "/tmp/$z.zip" -d "/universe/Universe_$u" || [ $? -eq 1 ]  # 1 = warnings only (a re-encoded name)
      rm -rf "/universe/Universe_$u/__MACOSX" "/tmp/$z.zip"
    done
    if [ ! -d "$target" ] && [ -d "/universe/Universe_$u/$name" ]; then mv "/universe/Universe_$u/$name" "$target"; fi
    [ -d "$target" ] || { echo "missing $target after unzip"; exit 1; }
  done
done
rm -rf /universe/__MACOSX; find /universe -name .DS_Store -delete
chown -R root:root /universe; chmod -R a+rX,go-w /universe
du -sh /universe/*
echo UNIVERSE_READY
"""


async def _run_script(box, name, script, env, max_minutes, log_fn):
    """Run a build script in the background (exec blocks until a command ends) and wait for its exit code."""
    await put_bytes(box, f"/tmp/{name}.sh", script.encode())
    await ssh(box, f"(env {env} setsid nohup bash /tmp/{name}.sh > /dev/null 2>&1 < /dev/null; "
                   f"echo $? > /tmp/{name}.exit) > /dev/null 2>&1 &")
    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        await asyncio.sleep(30)
        rc, out = await ssh(box, f"cat /tmp/{name}.exit 2>/dev/null; tail -n 2 /tmp/{name}.log 2>/dev/null")
        lines = out.strip().splitlines()
        if lines and lines[0].strip().lstrip("-").isdigit():
            if lines[0].strip() != "0":
                rc, tail = await ssh(box, f"tail -n 40 /tmp/{name}.log")
                raise RuntimeError(f"{name} build failed (exit {lines[0].strip()}): {tail[-1500:]}")
            rc, tail = await ssh(box, f"tail -n 6 /tmp/{name}.log")
            log_fn(f"{name} done: {tail.strip()[-400:]}")
            return
        log_fn(f"{name}: " + (lines[-1][:160] if lines else "starting"))
    raise RuntimeError(f"{name} build timed out")


async def ensure_universe(box, universes, log_fn=log.info):
    """Without a universe snapshot (modal-proxy's snapshot calls time out at 26 GB: they pass no timeout to Modal),
    download this review's universe into UNIVERSE_MOUNT when the sandbox starts, about 6-7 minutes for one."""
    if active_snapshot()[1]:
        return False
    await _run_script(box, "universe", UNIVERSE_SH, f"UNIVERSES={shlex.quote(universes)}", 30, log_fn)
    return True


async def build_base(log_fn=log.info):
    """Build and record only the base image (Claude Code + tools); reviews then download their universe at start."""
    body = _create_body(None, 1800, {"role": "template"})
    body.pop("snapshot_image_id")
    box = (await api("POST", "/sandbox", json_body=body, timeout=600))["sandbox_id"]
    try:
        await _run_script(box, "base", BASE_SH, "", 20, log_fn)
        base = (await api("POST", "/snapshot-sandbox-filesystem", json_body={"sandbox_id": box}, timeout=1800))["snapshot_image_id"]
        (config.DATA_DIR / "modal_template").write_text(base)
        log_fn(f"base image ready: {base}")
        return base
    finally:
        await delete_box(box)


async def build_template(universes="Normal SynthCut", log_fn=log.info, max_minutes=120):
    """Build the review image: the base as a filesystem snapshot, the universes as a directory snapshot.
    Records "<base> <universe>" as the active pair and returns it."""
    body = _create_body(None, max_minutes * 60 + 600, {"role": "template"})
    body.pop("snapshot_image_id")
    box = (await api("POST", "/sandbox", json_body=body, timeout=600))["sandbox_id"]
    log_fn(f"template sandbox {box} started")
    try:
        await _run_script(box, "base", BASE_SH, "", 20, log_fn)
        base = (await api("POST", "/snapshot-sandbox-filesystem", json_body={"sandbox_id": box, "timeout": SNAPSHOT_TIMEOUT},
                           timeout=SNAPSHOT_TIMEOUT + 120))["snapshot_image_id"]
        log_fn(f"base image: {base}")
        await _run_script(box, "universe", UNIVERSE_SH, f"UNIVERSES={shlex.quote(universes)}", max_minutes, log_fn)
        universe = (await api("POST", "/snapshot-directory", json_body={"sandbox_id": box, "directory_path": UNIVERSE_MOUNT,
                                                                             "timeout": SNAPSHOT_TIMEOUT},
                              timeout=SNAPSHOT_TIMEOUT + 120))["snapshot_image_id"]
        log_fn(f"universe snapshot: {universe}")
        (config.DATA_DIR / "modal_template").write_text(f"{base} {universe}")
        log_fn(f"review image ready: {base} {universe}")
        return base, universe
    finally:
        await delete_box(box)
