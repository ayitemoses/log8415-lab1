#!/usr/bin/env python3
import json, os, sys, subprocess, pathlib

KEY_PATH = os.getenv("AWS_KEY_PATH")
if not KEY_PATH:
    sys.exit("Missing AWS_KEY_PATH")

APP_SRC = pathlib.Path("app").resolve()
SSH_USER = "ubuntu"

SSH_BASE = [
    "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "ConnectTimeout=20",
    "-o", "ConnectionAttempts=10",
]

def ssh(host, cmd):
    remote = f"bash -lc '{cmd}'"
    return subprocess.run(
        SSH_BASE + ["-i", KEY_PATH, f"{SSH_USER}@{host}", remote],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

def scp_dir(host, local_path, remote_home="~"):
    return subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=no", "-i", KEY_PATH, "-r", local_path, f"{SSH_USER}@{host}:{remote_home}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

with open("artifacts/instances.json") as f:
    instances = json.load(f)

def deploy_one(host: str, cluster: str):
    print(f"🚀 {host} ({cluster}) as {SSH_USER}")

    setup_cmds = [
        "sudo apt-get update -y",
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip curl",
        "mkdir -p ~/app && rm -rf ~/app/*",
    ]
    for c in setup_cmds:
        print(f"[{host}] $ {c}")
        r = ssh(host, c)
        if r.returncode != 0:
            print(r.stdout); sys.exit(f"[{host}] Failed: {c}")

    print(f"[{host}] Copying app/ …")
    r = scp_dir(host, str(APP_SRC))
    if r.returncode != 0:
        print(r.stdout); sys.exit(f"[{host}] SCP failed")

    for c in [
        "python3 -m pip install --upgrade pip",
        "python3 -m pip install fastapi 'uvicorn[standard]'",
    ]:
        print(f"[{host}] $ {c}")
        r = ssh(host, c)
        if r.returncode != 0:
            print(r.stdout); sys.exit(f"[{host}] Failed: {c}")

    ssh(host, "pkill -f 'uvicorn .*main:app' || true")

    start_cmd = (
        "cd ~/app && "
        f"setsid env CLUSTER_NAME={cluster} "
        "python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 "
        "</dev/null >/tmp/uvicorn.log 2>&1 & echo $! > /tmp/uvicorn.pid"
    )
    print(f"[{host}] $ {start_cmd}")
    r = ssh(host, start_cmd)
    if r.returncode != 0:
        print(r.stdout); sys.exit(f"[{host}] Failed to start uvicorn")

    ready_cmd = (
        f"for i in $(seq 1 30); do "
        f"  code=$(curl -s -o /dev/null -w %{{http_code}} http://127.0.0.1:8000/{cluster}); "
        f"  [ \"$code\" = 200 ] && echo READY && exit 0; "
        f"  sleep 1; "
        f"done; echo NOT_READY; tail -n 120 /tmp/uvicorn.log; exit 1"
    )
    print(f"[{host}] Waiting for app to become ready …")
    r = ssh(host, ready_cmd)
    print(r.stdout, end="")
    if r.returncode != 0:
        sys.exit(f"[{host}] App did not become ready")

for inst in instances:
    ip = inst.get("public_ip")
    cluster = inst.get("cluster", "")
    if ip and cluster:
        deploy_one(ip, cluster)

print("✅ Deployment complete!")
