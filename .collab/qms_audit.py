#!/usr/bin/env python3
"""Append-only collaboration audit for the IQC QMS project.
Uses only the Python standard library."""
from __future__ import annotations
import argparse, contextlib, datetime as dt, hashlib, json, os, re, secrets, sqlite3, subprocess, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
EVENTS = ROOT / "events"
RECORDS = ROOT / "records"
HANDOFFS = ROOT / "handoffs"
INBOX = ROOT / "inbox"
OUTBOX = ROOT / "outbox"
RELATIONS_DIR = ROOT / "relations"
CONFLICTS_DIR = ROOT / "conflicts"
DB_PATH = RUNTIME / "audit.sqlite3"
LOCK_PATH = RUNTIME / ".lock"
SENSITIVE_ASSIGN_RE = re.compile(r"(?i)\b(secret|token|password|passwd|api[ _-]?key|cookie|authorization)\b\s*(=|:|\bis\b)\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
AUTH_BEARER_RE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[^\s,;]+")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
HEADER_VALUE_RE = re.compile(r"(?i)\b(x-api-key|cookie|authorization)\b\s+(?!is\b)[^\s,;]+")


def now(): return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def sha(value): return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
def redact(value):
    if isinstance(value, dict): return {str(k): "[REDACTED]" if re.search(r"(?i)(secret|token|password|passwd|api[_-]?key|cookie|authorization)", str(k)) else redact(v) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    if isinstance(value, str):
        # Protect a complete Authorization Bearer pair while generic patterns run.
        # Otherwise generic assignment redaction would redact only "Bearer" a second time.
        marker = "__QMS_AUTH_BEARER_REDACTED__"
        value = AUTH_BEARER_RE.sub(marker, value)
        value = SENSITIVE_ASSIGN_RE.sub(lambda m: f"{m.group(1)} {m.group(2)} [REDACTED]", value)
        value = BEARER_RE.sub("Bearer [REDACTED]", value)
        value = HEADER_VALUE_RE.sub(lambda m: f"{m.group(1)} [REDACTED]", value)
        return value.replace(marker, "Authorization: Bearer [REDACTED]")
    return value

@contextlib.contextmanager
def lock():
    RUNTIME.mkdir(parents=True, exist_ok=True); EVENTS.mkdir(parents=True, exist_ok=True); RECORDS.mkdir(parents=True, exist_ok=True); HANDOFFS.mkdir(parents=True, exist_ok=True); INBOX.mkdir(parents=True, exist_ok=True); OUTBOX.mkdir(parents=True, exist_ok=True); RELATIONS_DIR.mkdir(parents=True, exist_ok=True); CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)
    started=time.monotonic()
    while True:
        try:
            fd=os.open(str(LOCK_PATH), os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd); break
        except FileExistsError:
            if time.monotonic()-started > 10: raise RuntimeError("감사 기록 잠금을 10초 안에 얻지 못했습니다.")
            time.sleep(.05)
    try: yield
    finally:
        try: LOCK_PATH.unlink()
        except FileNotFoundError: pass

def retry_pending_mirror(con):
    """Append only DB events missing from an otherwise exact JSONL prefix.
    A divergent mirror is never overwritten here; use verify then manual repair.
    """
    db_events = rows(con)
    disk = []
    for path in sorted(EVENTS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip(): disk.append(json.loads(line))
    if canonical(disk) != canonical(db_events[:len(disk)]):
        return False
    for item in db_events[len(disk):]: append_mirror(item)
    return len(disk) != len(db_events)

def protocol_rows(con):
    out=[]
    # SQLite rowid is insertion order for this append-only table; timestamps are second-resolution by design.
    for r in con.execute("SELECT * FROM protocol_records ORDER BY rowid"):
        out.append({"record_id":r["record_id"],"record_type":r["record_type"],"project_id":r["project_id"],"created_at":r["created_at"],"updated_at":r["updated_at"],"created_by":r["created_by"],"knowledge_state":r["knowledge_state"],"status":r["status"],"risk_level":r["risk_level"],"record_version":r["record_version"],"protocol_version":r["protocol_version"],"payload":json.loads(r["payload_json"]),"record_hash":r["record_hash"]})
    return out

def retry_pending_record_mirror(con):
    db_items=protocol_rows(con); disk=[]
    for path in sorted(RECORDS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip(): disk.append(json.loads(line))
    # Never overwrite a non-prefix representation: it may be concurrent work or tampering.
    if canonical(disk)!=canonical(db_items[:len(disk)]): return False
    for item in db_items[len(disk):]: append_record_mirror(item)
    return len(disk)!=len(db_items)

def connect():
    RUNTIME.mkdir(parents=True, exist_ok=True); EVENTS.mkdir(parents=True, exist_ok=True); RECORDS.mkdir(parents=True, exist_ok=True); HANDOFFS.mkdir(parents=True, exist_ok=True); INBOX.mkdir(parents=True, exist_ok=True); OUTBOX.mkdir(parents=True, exist_ok=True); RELATIONS_DIR.mkdir(parents=True, exist_ok=True); CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory=sqlite3.Row
    con.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, actor TEXT NOT NULL, scope TEXT, baseline TEXT, status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, actor TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id), status TEXT NOT NULL, summary TEXT NOT NULL, data_json TEXT NOT NULL, prev_hash TEXT, event_hash TEXT NOT NULL UNIQUE);
    CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit_events are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit_events are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS tasks_no_delete BEFORE DELETE ON tasks BEGIN SELECT RAISE(ABORT, 'tasks are retained'); END;
    CREATE TABLE IF NOT EXISTS protocol_records (
      record_id TEXT PRIMARY KEY, record_type TEXT NOT NULL, project_id TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL,
      knowledge_state TEXT NOT NULL, status TEXT NOT NULL, risk_level TEXT NOT NULL,
      record_version INTEGER NOT NULL, protocol_version TEXT NOT NULL, payload_json TEXT NOT NULL,
      record_hash TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS protocol_relations (
      relation_id TEXT PRIMARY KEY, from_record_id TEXT NOT NULL REFERENCES protocol_records(record_id),
      to_record_id TEXT NOT NULL REFERENCES protocol_records(record_id), relation_type TEXT NOT NULL,
      created_at TEXT NOT NULL, created_by TEXT NOT NULL, relation_hash TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS protocol_conflicts (
      conflict_id TEXT PRIMARY KEY, target_record_id TEXT, incoming_base_version INTEGER,
      current_version INTEGER, reason TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN'
    );
    CREATE TABLE IF NOT EXISTS protocol_migrations (migration_key TEXT PRIMARY KEY, applied_at TEXT NOT NULL, details_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS handoff_acceptances (acceptance_id TEXT PRIMARY KEY, handoff_id TEXT NOT NULL REFERENCES protocol_records(record_id), task_id INTEGER, previous_owner TEXT NOT NULL, new_owner TEXT NOT NULL, accepted_at TEXT NOT NULL, basis TEXT NOT NULL, verification TEXT NOT NULL, acceptance_hash TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS envelope_receipts (message_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, receiver TEXT NOT NULL, status TEXT NOT NULL, request_record_id TEXT, receipt_hash TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS protocol_adapters (adapter_id TEXT PRIMARY KEY, source_version TEXT NOT NULL, target_version TEXT NOT NULL, status TEXT NOT NULL, scope TEXT NOT NULL, evidence_ref TEXT NOT NULL, registered_at TEXT NOT NULL, registered_by TEXT NOT NULL, adapter_hash TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS workflow_runs (run_id TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id), runner TEXT NOT NULL, token_hash TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workflow_authorization_usage (authorization_record TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, task_id INTEGER NOT NULL REFERENCES tasks(id), actor TEXT NOT NULL, scope_hash TEXT NOT NULL, request_hash TEXT NOT NULL, consumed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS git_commit_preflights (preflight_id TEXT PRIMARY KEY, approval_record TEXT NOT NULL REFERENCES protocol_records(record_id), actor TEXT NOT NULL, manifest_hash TEXT NOT NULL, tree_hash TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS git_commit_authorization_usage (approval_record TEXT PRIMARY KEY REFERENCES protocol_records(record_id), preflight_id TEXT NOT NULL UNIQUE REFERENCES git_commit_preflights(preflight_id), actor TEXT NOT NULL, commit_hash TEXT NOT NULL UNIQUE, manifest_hash TEXT NOT NULL, tree_hash TEXT NOT NULL, consumed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deploy_authorization_usage (approval_record TEXT PRIMARY KEY REFERENCES protocol_records(record_id), actor TEXT NOT NULL, remote TEXT NOT NULL, commit_hash TEXT NOT NULL, consumed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workflow_authorizer_secrets (version TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, configured_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS workflow_authorizer_secrets_no_update BEFORE UPDATE ON workflow_authorizer_secrets BEGIN SELECT RAISE(ABORT, 'workflow authorizer secret hash is immutable'); END;
    CREATE TRIGGER IF NOT EXISTS workflow_authorizer_secrets_no_delete BEFORE DELETE ON workflow_authorizer_secrets BEGIN SELECT RAISE(ABORT, 'workflow authorizer secret hash is retained'); END;
    CREATE TRIGGER IF NOT EXISTS workflow_runs_no_update BEFORE UPDATE ON workflow_runs BEGIN SELECT RAISE(ABORT, 'workflow_runs are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS workflow_runs_no_delete BEFORE DELETE ON workflow_runs BEGIN SELECT RAISE(ABORT, 'workflow_runs are retained'); END;
    CREATE TRIGGER IF NOT EXISTS git_commit_preflights_no_update BEFORE UPDATE ON git_commit_preflights BEGIN SELECT RAISE(ABORT, 'git commit preflights are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS git_commit_preflights_no_delete BEFORE DELETE ON git_commit_preflights BEGIN SELECT RAISE(ABORT, 'git commit preflights are retained'); END;
    CREATE TRIGGER IF NOT EXISTS git_commit_authorization_usage_no_update BEFORE UPDATE ON git_commit_authorization_usage BEGIN SELECT RAISE(ABORT, 'git commit authorization usage is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS git_commit_authorization_usage_no_delete BEFORE DELETE ON git_commit_authorization_usage BEGIN SELECT RAISE(ABORT, 'git commit authorization usage is retained'); END;
    CREATE TRIGGER IF NOT EXISTS deploy_authorization_usage_no_update BEFORE UPDATE ON deploy_authorization_usage BEGIN SELECT RAISE(ABORT, 'deploy authorization usage is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS deploy_authorization_usage_no_delete BEFORE DELETE ON deploy_authorization_usage BEGIN SELECT RAISE(ABORT, 'deploy authorization usage is retained'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_records_no_update BEFORE UPDATE ON protocol_records BEGIN SELECT RAISE(ABORT, 'protocol_records are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_records_no_delete BEFORE DELETE ON protocol_records BEGIN SELECT RAISE(ABORT, 'protocol_records are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_relations_no_update BEFORE UPDATE ON protocol_relations BEGIN SELECT RAISE(ABORT, 'protocol_relations are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_relations_no_delete BEFORE DELETE ON protocol_relations BEGIN SELECT RAISE(ABORT, 'protocol_relations are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_conflicts_no_update BEFORE UPDATE ON protocol_conflicts BEGIN SELECT RAISE(ABORT, 'protocol_conflicts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_conflicts_no_delete BEFORE DELETE ON protocol_conflicts BEGIN SELECT RAISE(ABORT, 'protocol_conflicts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS handoff_acceptances_no_update BEFORE UPDATE ON handoff_acceptances BEGIN SELECT RAISE(ABORT, 'handoff_acceptances are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS handoff_acceptances_no_delete BEFORE DELETE ON handoff_acceptances BEGIN SELECT RAISE(ABORT, 'handoff_acceptances are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS envelope_receipts_no_update BEFORE UPDATE ON envelope_receipts BEGIN SELECT RAISE(ABORT, 'envelope_receipts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS envelope_receipts_no_delete BEFORE DELETE ON envelope_receipts BEGIN SELECT RAISE(ABORT, 'envelope_receipts are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_adapters_no_update BEFORE UPDATE ON protocol_adapters BEGIN SELECT RAISE(ABORT, 'protocol_adapters are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS protocol_adapters_no_delete BEFORE DELETE ON protocol_adapters BEGIN SELECT RAISE(ABORT, 'protocol_adapters are append-only'); END;
    """)
    con.commit(); retry_pending_mirror(con); retry_pending_record_mirror(con)
    for table,folder in (("protocol_relations",RELATIONS_DIR),("protocol_conflicts",CONFLICTS_DIR)):
        db=[dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]; disk=[]
        for p in sorted(folder.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip(): disk.append(json.loads(line))
        if canonical(disk)==canonical(db[:len(disk)]):
            for item in db[len(disk):]:
                with (folder/f"{item['created_at'][:7]}.jsonl").open("a",encoding="utf-8",newline="\n") as f: f.write(canonical(item)+"\n")
    return con

def mirror_path(occurred_at): return EVENTS / f"{occurred_at[:7]}.jsonl"
def append_mirror(event):
    path=mirror_path(event["occurred_at"]); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f: f.write(canonical(event)+"\n")

def append_record_mirror(record):
    path = RECORDS / f"{record['created_at'][:7]}.jsonl"; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f: f.write(canonical(record)+"\n")

RECORD_TYPES={"PROJECT","RULE","AGENT","PERMISSION_PROFILE","TASK","DECISION","EVIDENCE","AI_INTERPRETATION","STATE","HANDOFF","REQUEST","REVIEW","CHANGE","ISSUE","RELATION"}
KNOWLEDGE={"FACT","CONFIRMED_DECISION","USER_INTENT","INFERENCE","HYPOTHESIS","UNKNOWN","CONFLICT"}
RISKS={"NONE","LOW","MEDIUM","HIGH","CRITICAL"}
RELATIONS={"based_on","supports","contradicts","supersedes","superseded_by","caused_by","result_of","verified_by","handoff_to","affects","related_to"}
PERMISSION_ORDER={"READ":0,"ANALYZE":1,"VERIFY":2,"PROPOSE":3,"EXECUTE":4}
OP_REGISTRY={
 "READ":("READ","NONE",False), "SEARCH_ARCHIVE":("READ","NONE",False), "ANALYZE_ISSUE":("ANALYZE","LOW",False),
 "RUN_TEST":("VERIFY","LOW",False), "CREATE_PROPOSAL":("PROPOSE","LOW",False),
 "UPDATE_STATE":("EXECUTE","MEDIUM",True), "CHANGE_RULE":("EXECUTE","HIGH",True),
 "DB_DATA_CHANGE":("EXECUTE","HIGH",True), "GIT_COMMIT":("EXECUTE","MEDIUM",True), "DEPLOY":("EXECUTE","HIGH",True),
}
PROJECT_ID="iqc-app"

def record(con, record_type, actor, payload, *, knowledge_state="FACT", status="ACTIVE", risk_level="NONE", project_id=PROJECT_ID):
    if record_type not in RECORD_TYPES: raise ValueError(f"지원하지 않는 Record Type: {record_type}")
    if knowledge_state not in KNOWLEDGE: raise ValueError(f"지원하지 않는 knowledge_state: {knowledge_state}")
    if risk_level not in RISKS: raise ValueError(f"지원하지 않는 risk_level: {risk_level}")
    required={"PROJECT":{"name","purpose"},"AGENT":{"agent_name","role","model","model_version"},"PERMISSION_PROFILE":{"agent_id","operation","permission_level","scope"},"TASK":{"title","owner","scope"},"DECISION":{"decision","decided_by","scope"},"EVIDENCE":{"source_type","content","verification_status"},"STATE":{"scope_type","scope_ref","summary"},"REQUEST":{"request_type","from_agent","to_agent","scope"},"AI_INTERPRETATION":{"interpretation","derived_from","scope"},"REVIEW":{"review_type","reviewer","review_target","verification"},"CHANGE":{"change_type","target_record_id","reason"},"ISSUE":{"title","description","next_action"},"RELATION":{"relation_id","from_record_id","to_record_id","relation_type"}}
    missing=required.get(record_type,set())-set(payload)
    if missing: raise ValueError(f"{record_type} 필수 필드 누락: {', '.join(sorted(missing))}")
    payload=redact(payload); stamp=now(); rid="rec-"+uuid.uuid4().hex
    item={"record_id":rid,"record_type":record_type,"project_id":project_id,"created_at":stamp,"updated_at":stamp,"created_by":actor,"knowledge_state":knowledge_state,"status":status,"risk_level":risk_level,"record_version":1,"protocol_version":"1.0.0","payload":payload}
    item["record_hash"]=sha(item)
    con.execute("INSERT INTO protocol_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(rid,record_type,project_id,stamp,stamp,actor,knowledge_state,status,risk_level,1,"1.0.0",canonical(payload),item["record_hash"]))
    con.commit(); append_record_mirror(item); return item

def relation(con, actor, from_id, to_id, relation_type):
    if relation_type not in RELATIONS: raise ValueError("정의되지 않은 relation_type입니다. TBD-0009 항목은 임의로 추가하지 않습니다.")
    for x in (from_id,to_id):
        if not con.execute("SELECT 1 FROM protocol_records WHERE record_id=?",(x,)).fetchone(): raise ValueError(f"Record를 찾지 못했습니다: {x}")
    item={"relation_id":"rel-"+uuid.uuid4().hex,"from_record_id":from_id,"to_record_id":to_id,"relation_type":relation_type,"created_at":now(),"created_by":actor}
    item["relation_hash"]=sha(item); con.execute("INSERT INTO protocol_relations VALUES(?,?,?,?,?,?,?)",tuple(item.values())); con.commit()
    with (RELATIONS_DIR/f"{item['created_at'][:7]}.jsonl").open("a",encoding="utf-8",newline="\n") as f: f.write(canonical(item)+"\n")
    # Every edge also has a first-class immutable RELATION Record. record() does not create edges, so this is recursion-safe.
    envelope=record(con,"RELATION",actor,{"relation_id":item["relation_id"],"from_record_id":from_id,"to_record_id":to_id,"relation_type":relation_type},knowledge_state="FACT",status="ACTIVE",risk_level="NONE")
    item["relation_record_id"]=envelope["record_id"]
    return item

def actor_permission(con, actor, operation):
    rows=con.execute("SELECT payload_json FROM protocol_records WHERE record_type='PERMISSION_PROFILE' AND status='ACTIVE' ORDER BY created_at DESC",()).fetchall()
    needed=OP_REGISTRY[operation][0]
    for row in rows:
        p=json.loads(row[0])
        if p.get("agent_id")==actor and p.get("operation")==operation:
            return p.get("permission_level","READ"), bool(p.get("approval_required",False))
    # The local default never elevates Codex beyond P-1/P-3. User retains execution under explicit approval.
    if actor=="user": return "EXECUTE", OP_REGISTRY[operation][2]
    if actor in ("claude","codex"): return "READ", True
    return "READ", True

def git_repo_root():
    """Return the repository containing .collab; tests may place the CLI at repo root."""
    for candidate in (ROOT.parent, ROOT):
        if (candidate / ".git").exists():
            return candidate
    probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], text=True, capture_output=True)
    if probe.returncode == 0:
        return Path(probe.stdout.strip())
    raise ValueError("Git 저장소 루트를 찾지 못했습니다.")


def staged_manifest():
    """Hash the exact staged raw diff, including paths, modes and staged blob IDs."""
    repo = git_repo_root()
    raw = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--raw", "--no-abbrev", "--root", "-z"], capture_output=True)
    if raw.returncode:
        raise ValueError("staged manifest를 계산하지 못했습니다.")
    names = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only", "--root", "-z"], capture_output=True)
    if names.returncode:
        raise ValueError("staged 파일 목록을 읽지 못했습니다.")
    paths = [part.decode("utf-8", "surrogateescape") for part in names.stdout.split(b"\0") if part]
    if not raw.stdout or not paths:
        raise ValueError("staged 변경이 없어 commit 승인 Record를 만들 수 없습니다.")
    tree = subprocess.run(["git", "-C", str(repo), "write-tree"], text=True, capture_output=True)
    if tree.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", tree.stdout.strip()):
        raise ValueError("staged tree hash를 계산하지 못했습니다.")
    return {
        "algorithm": "git-diff-cached-raw-z-sha256-v1",
        "hash": hashlib.sha256(raw.stdout).hexdigest(),
        "tree_hash": tree.stdout.strip(),
        "paths": paths,
        "entry_count": len(paths),
    }


def git_commit_authorization(con, record_id, actor, manifest_hash):
    row = con.execute("SELECT created_by,knowledge_state,status,payload_json FROM protocol_records WHERE record_id=? AND record_type='DECISION'", (record_id,)).fetchone()
    if not row:
        return False, "approval Record를 찾지 못했습니다."
    payload = json.loads(row[3])
    used = con.execute("SELECT 1 FROM git_commit_authorization_usage WHERE approval_record=?", (record_id,)).fetchone()
    valid = (
        row[0] == "user" and row[1] == "CONFIRMED_DECISION" and row[2] == "ACTIVE"
        and payload.get("decision_type") == "GIT_COMMIT_AUTHORIZATION"
        and payload.get("operation") == "GIT_COMMIT"
        and payload.get("authorized_actor") == actor
        and payload.get("manifest_algorithm") == "git-diff-cached-raw-z-sha256-v1"
        and payload.get("manifest_hash") == manifest_hash
        and payload.get("approval_state") == "APPROVED"
        and payload.get("direct_user_instruction") is True
        and payload.get("one_time_use") is True
        and not used
    )
    if not valid:
        return False, "actor·승인 상태·1회 사용 여부 또는 staged manifest가 승인 Record와 일치하지 않습니다."
    return True, "approved"


def deploy_authorization(con, record_id, actor, remote, commit_hash):
    """DEPLOY용 git_commit_authorization 대응물.

    GIT_COMMIT과 달리 배포는 "커밋이 로컬에 만들어졌는가"처럼 성공을 사후에 확실히
    관찰할 로컬 훅이 없다(post-commit에 대응하는 post-push는 없음, 원격 push 성공
    여부는 네트워크에 달려있다) — 그래서 이 승인은 두 단계(preflight 기록→사후 소비)
    가 아니라 pre-push 시점에 바로 소비한다(preflight() 안에서). push 자체가
    네트워크 오류 등으로 실패해도 이미 소비된 것으로 남는다 — 재시도하려면
    deploy-authorize를 다시 호출해야 한다. 이건 알려진 한계이지 버그가 아니다."""
    if not record_id or not remote or not commit_hash:
        return False, "approval record·remote·commit_hash가 모두 필요합니다."
    row = con.execute("SELECT created_by,knowledge_state,status,payload_json FROM protocol_records WHERE record_id=? AND record_type='DECISION'", (record_id,)).fetchone()
    if not row:
        return False, "approval Record를 찾지 못했습니다."
    payload = json.loads(row[3])
    used = con.execute("SELECT 1 FROM deploy_authorization_usage WHERE approval_record=?", (record_id,)).fetchone()
    valid = (
        row[0] == "user" and row[1] == "CONFIRMED_DECISION" and row[2] == "ACTIVE"
        and payload.get("decision_type") == "DEPLOY_AUTHORIZATION"
        and payload.get("operation") == "DEPLOY"
        and payload.get("authorized_actor") == actor
        and payload.get("remote") == remote
        and payload.get("commit_hash") == commit_hash
        and payload.get("approval_state") == "APPROVED"
        and payload.get("direct_user_instruction") is True
        and payload.get("one_time_use") is True
        and not used
    )
    if not valid:
        return False, "actor·remote·commit_hash·승인 상태 또는 1회 사용 여부가 승인 Record와 일치하지 않습니다."
    return True, "approved"


def preflight(con, actor, operation, *, approval="MISSING", scope="", task_id=None, approval_record=None, staged_manifest_hash=None, deploy_commit_hash=None, deploy_remote=None):
    if operation not in OP_REGISTRY: raise ValueError("Operation Registry에 없는 행위입니다. TBD-0006을 임의로 채우지 않습니다.")
    need,risk,policy=OP_REGISTRY[operation]; have,profile_approval=actor_permission(con,actor,operation)
    allowed=PERMISSION_ORDER.get(have,-1)>=PERMISSION_ORDER[need]
    approval_needed=policy or profile_approval or risk in ("HIGH","CRITICAL")
    approved=approval=="APPROVED"
    decision_ok=True; manifest=None; reason="checks passed"
    if operation=="GIT_COMMIT":
        try:
            manifest=staged_manifest()
            manifest_ok=bool(staged_manifest_hash) and secrets.compare_digest(staged_manifest_hash, manifest["hash"])
            decision_ok, decision_reason = git_commit_authorization(con, approval_record, actor, manifest["hash"])
            decision_ok = decision_ok and manifest_ok
            allowed = decision_ok
            reason = "checks passed" if decision_ok else ("hook manifest differs from current staged index" if not manifest_ok else decision_reason)
        except ValueError as exc:
            decision_ok=False; allowed=False; reason=str(exc)
    elif operation=="DEPLOY" and deploy_commit_hash:
        # GIT_COMMIT과 달리 배포 성공을 사후에 확실히 관찰할 로컬 훅이 없어(post-push
        # 없음), 두 단계로 안 나누고 이 preflight 통과 시점에 바로 소비한다(아래).
        try:
            decision_ok, decision_reason = deploy_authorization(con, approval_record, actor, deploy_remote, deploy_commit_hash)
            allowed = decision_ok
            reason = "checks passed" if decision_ok else decision_reason
        except ValueError as exc:
            decision_ok=False; allowed=False; reason=str(exc)
    result="ALLOWED" if allowed and (not approval_needed or approved) and decision_ok else "BLOCKED"
    event_data={"operation":operation,"required_permission":need,"granted_permission":have,"risk_level":risk,"approval_status":approval,"approval_record":approval_record,"scope":scope,"reason":reason if result=="BLOCKED" else "checks passed"}
    if manifest:
        event_data.update({"staged_manifest_hash":manifest["hash"],"staged_tree_hash":manifest["tree_hash"],"staged_paths":manifest["paths"]})
    if operation=="DEPLOY" and deploy_commit_hash:
        event_data.update({"deploy_commit_hash":deploy_commit_hash,"deploy_remote":deploy_remote})
    event(con,"OPERATION_PREFLIGHT",actor,f"{operation}: {result}",task_id,result,event_data)
    if result=="ALLOWED" and operation=="GIT_COMMIT":
        preflight_id="git-preflight-"+uuid.uuid4().hex
        con.execute("INSERT INTO git_commit_preflights VALUES(?,?,?,?,?,?)", (preflight_id, approval_record, actor, manifest["hash"], manifest["tree_hash"], now()))
        con.commit()
        event(con, "GIT_COMMIT_PREFLIGHT_ALLOWED", actor, "Staged manifest matched one-time Git commit approval", status="ALLOWED", data={"preflight_id":preflight_id,"approval_record":approval_record,"manifest_hash":manifest["hash"],"tree_hash":manifest["tree_hash"],"paths":manifest["paths"]})
    if result=="ALLOWED" and operation=="DEPLOY" and deploy_commit_hash:
        con.execute("INSERT INTO deploy_authorization_usage VALUES(?,?,?,?,?)", (approval_record, actor, deploy_remote, deploy_commit_hash, now()))
        con.commit()
        event(con, "DEPLOY_AUTHORIZATION_CONSUMED", actor, "Deploy approval consumed at pre-push (push success itself is not locally observable)", status="RECORDED", data={"approval_record":approval_record,"remote":deploy_remote,"commit_hash":deploy_commit_hash})
    return result,need,risk

def event(con, event_type, actor, summary, task_id=None, status="RECORDED", data=None):
    data=redact(data or {}); summary=redact(summary)
    row=con.execute("SELECT event_hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
    payload={"event_id":str(uuid.uuid4()),"occurred_at":now(),"event_type":event_type,"actor":actor,"task_id":task_id,"status":status,"summary":summary,"data":data,"prev_hash":row[0] if row else None}
    payload["event_hash"]=sha(payload)
    con.execute("INSERT INTO audit_events(event_id,occurred_at,event_type,actor,task_id,status,summary,data_json,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?,?)", (payload["event_id"],payload["occurred_at"],payload["event_type"],payload["actor"],payload["task_id"],payload["status"],payload["summary"],canonical(payload["data"]),payload["prev_hash"],payload["event_hash"]))
    con.commit()  # Persist first; a failed mirror write is retried on the next invocation.
    append_mirror(payload); return payload

def task_exists(con, task_id):
    if not con.execute("SELECT 1 FROM tasks WHERE id=?",(task_id,)).fetchone(): raise ValueError(f"작업 #{task_id}를 찾지 못했습니다.")

def cmd_init(args):
    with lock():
        con=connect(); mark_v1_authorizer_retired(con); event(con,"SYSTEM_INIT",args.actor,"QMS 협업 기록 원장 초기화",status="CONFIRMED",data={"storage":".collab/runtime/audit.sqlite3 + .collab/events/*.jsonl"}); con.commit(); con.close()
    print("QMS 협업 기록 원장을 준비했습니다.")

def cmd_start(args):
    with lock():
        con=connect(); stamp=now(); title=redact(args.title); scope=redact(args.scope); baseline=redact(args.baseline)
        cur=con.execute("INSERT INTO tasks(title,actor,scope,baseline,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(title,args.actor,scope,baseline,"OPEN",stamp,stamp)); tid=cur.lastrowid
        event(con,"TASK_STARTED",args.actor,args.summary or title,tid,"RECORDED",{"title":title,"scope":scope,"baseline":baseline}); con.commit(); con.close()
    print(f"TASK_ID={tid}")

def cmd_simple(args, event_type, task_status=None):
    with lock():
        con=connect(); task_exists(con,args.task)
        if task_status:
            task_state = args.status if event_type == "TASK_ENDED" else task_status
            con.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",(task_state,now(),args.task))
        event(con,event_type,args.actor,args.summary,args.task,args.status,{"basis":args.basis,"unknown":args.unknown,"state":getattr(args,"state",None)}); con.commit(); con.close()
    print(f"작업 #{args.task} 기록 완료")

def cmd_run(args):
    command=args.command[1:] if args.command and args.command[0]=="--" else args.command
    if not command: raise ValueError("실행할 명령은 -- 뒤에 지정하세요.")
    with lock():
        con=connect(); task_exists(con,args.task)
        if args.actor in {"claude", "codex"}:
            profile_ok = workflow_test_permission_allowed(con, args.actor, args.task, "run", args.summary)
            runner_ok = workflow_runner_allowed(
                con, args.actor, args.task, args.run, args.capability_token, "run", args.summary
            )
            command_ok = allowed_workflow_test_command(command)
            if not command_ok:
                event(con, "WORKFLOW_TEST_COMMAND_BLOCKED", args.actor,
                      "run: BLOCKED command is outside the .collab verification allowlist",
                      args.task, "BLOCKED", {"command_summary": args.summary, "command_recorded": False})
            allowed = "ALLOWED" if profile_ok and runner_ok and command_ok else "BLOCKED"
        else:
            allowed,_,_=preflight(con,args.actor,"RUN_TEST",approval=args.approval,scope=args.summary,task_id=args.task)
        con.close()
    if allowed!="ALLOWED": print("명령 실행이 preflight에서 차단됐습니다."); return 3
    started=now(); proc=subprocess.run(command, cwd=ROOT); result="SUCCESS" if proc.returncode==0 else "FAILED"
    with lock():
        con=connect(); event(con,"TERMINAL_RUN",args.actor,args.summary,args.task,result,{"command_summary":args.summary,"exit_code":proc.returncode,"started_at":started,"command_recorded":False,"preflight":"ALLOWED"}); con.commit(); con.close()
    print(f"명령 종료 코드 {proc.returncode}; 작업 #{args.task}에 기록했습니다."); return proc.returncode

def rows(con):
    out=[]
    for r in con.execute("SELECT * FROM audit_events ORDER BY seq"):
        out.append({"event_id":r["event_id"],"occurred_at":r["occurred_at"],"event_type":r["event_type"],"actor":r["actor"],"task_id":r["task_id"],"status":r["status"],"summary":r["summary"],"data":json.loads(r["data_json"]),"prev_hash":r["prev_hash"],"event_hash":r["event_hash"]})
    return out

def cmd_status(args):
    with lock():
        con=connect()
        for r in con.execute("SELECT id,status,title,actor,updated_at FROM tasks ORDER BY id DESC LIMIT ?",(args.limit,)):
            final = con.execute("SELECT status FROM audit_events WHERE task_id=? AND event_type='TASK_ENDED' ORDER BY seq DESC LIMIT 1", (r['id'],)).fetchone()
            # Completion is an event fact. Do not infer it from the mutable task row.
            display_status = final['status'] if final else r['status']
            print(f"#{r['id']} [{display_status}] {r['title']} - {r['actor']} ({r['updated_at']})")
        con.close()

def cmd_verify(args):
    with lock():
        con=connect(); events=rows(con); previous=None; problems=[]
        for e in events:
            check={k:e[k] for k in ("event_id","occurred_at","event_type","actor","task_id","status","summary","data","prev_hash")}
            if e["prev_hash"]!=previous: problems.append(f"DB chain mismatch: {e['event_id']}")
            if e["event_hash"]!=sha(check): problems.append(f"DB hash mismatch: {e['event_id']}")
            previous=e["event_hash"]
        disk=[]
        for p in sorted(EVENTS.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip(): disk.append(json.loads(line))
        if canonical(disk)!=canonical(events): problems.append("JSONL mirror differs from SQLite ledger")
        protocol=protocol_rows(con); record_disk=[]
        for p in sorted(RECORDS.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip(): record_disk.append(json.loads(line))
        if canonical(record_disk)!=canonical(protocol): problems.append("Protocol record JSONL differs from SQLite ledger")
        for item in protocol:
            raw={k:item[k] for k in item if k!="record_hash"}
            if item["record_hash"]!=sha(raw): problems.append(f"Protocol record hash mismatch: {item['record_id']}")
        for table, folder, cols, hkey in (("protocol_relations",RELATIONS_DIR,("relation_id","from_record_id","to_record_id","relation_type","created_at","created_by","relation_hash"),"relation_hash"),("protocol_conflicts",CONFLICTS_DIR,("conflict_id","target_record_id","incoming_base_version","current_version","reason","created_at","status"),None)):
            db=[dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]; disk=[]
            for p in sorted(folder.glob("*.jsonl")):
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip(): disk.append(json.loads(line))
            if canonical(db)!=canonical(disk): problems.append(f"{table} JSONL differs from SQLite ledger")
        con.close()
    if problems:
        print("VERIFY FAILED\n"+"\n".join(problems)); return 1
    print(f"VERIFY OK: {len(events)} audit events and {len(protocol)} protocol records; SQLite and JSONL mirrors match."); return 0

def cmd_repair(args):
    with lock():
        con=connect(); events=rows(con)
        # Verify DB chain first; never write a mirror from damaged DB.
        previous=None
        for e in events:
            raw={k:e[k] for k in ("event_id","occurred_at","event_type","actor","task_id","status","summary","data","prev_hash")}
            if e["prev_hash"]!=previous or e["event_hash"]!=sha(raw): raise RuntimeError("SQLite hash chain is invalid; mirror repair stopped.")
            previous=e["event_hash"]
        for p in EVENTS.glob("*.jsonl"): p.unlink()
        for e in events: append_mirror(e)
        con.close()
    print(f"JSONL mirror repaired from {len(events)} SQLite events.")


WORKFLOW_ROLES = {"planner", "developer", "designer", "quality-watcher", "reuse-scout"}
WORKFLOW_OUTCOMES = {"PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"}
WORKFLOW_TEST_COMMANDS = {
    ("python", "-m", "unittest", "discover", "tests"),
    ("python", "-m", "py_compile", "qms_audit.py"),
    ("python", "qms_audit.py", "verify"),
}


def workflow_allowed(con, actor, task_id, action, scope):
    """Only the explicitly authorized Claude or Codex main actor can orchestrate a run."""
    rows_ = con.execute(
        "SELECT payload_json FROM protocol_records WHERE record_type='PERMISSION_PROFILE' "
        "AND status='ACTIVE' ORDER BY rowid DESC"
    ).fetchall()
    granted = any(
        (p := json.loads(row[0])).get("agent_id") == actor
        and p.get("operation") == "WORKFLOW_ORCHESTRATE"
        and p.get("permission_level") == "EXECUTE"
        and p.get("scope") == ".collab workflow orchestration only"
        and p.get("profile_version") == "v2"
        for row in rows_
    )
    status = "ALLOWED" if granted else "BLOCKED"
    event(con, "WORKFLOW_PREFLIGHT", actor, f"{action}: {status}", task_id, status,
          {"action": action, "scope": scope,
           "reason": "actor-neutral .collab workflow-orchestration profile"})
    return granted


def workflow_test_permission_allowed(con, actor, task_id, action, scope):
    """Allow only the dedicated .collab verification profile for a main actor."""
    rows_ = con.execute(
        "SELECT payload_json FROM protocol_records WHERE record_type='PERMISSION_PROFILE' "
        "AND status='ACTIVE' ORDER BY rowid DESC"
    ).fetchall()
    granted = any(
        (p := json.loads(row[0])).get("agent_id") == actor
        and p.get("operation") == "WORKFLOW_RUN_TEST"
        and p.get("permission_level") == "EXECUTE"
        and p.get("scope") == ".collab verification commands only"
        and p.get("profile_version") == "v1"
        for row in rows_
    )
    status = "ALLOWED" if granted else "BLOCKED"
    event(con, "WORKFLOW_TEST_PREFLIGHT", actor, f"{action}: {status}", task_id, status,
          {"action": action, "scope": scope,
           "reason": "allowlisted .collab verification profile"})
    return granted


def allowed_workflow_test_command(command):
    """Commands are compared as argv, not shell text; no shell is ever used."""
    return tuple(command) in WORKFLOW_TEST_COMMANDS


def _workflow_hash(value):
    return sha(redact(value))


def mark_v1_authorizer_retired(con):
    """Retain the immutable v1 secret hash, but mark it operationally unused by v2."""
    key = "workflow-authorizer-v1-retired-by-v2"
    if con.execute("SELECT 1 FROM protocol_migrations WHERE migration_key=?", (key,)).fetchone():
        return
    legacy = con.execute("SELECT 1 FROM workflow_authorizer_secrets WHERE version='v1'").fetchone()
    details = {"legacy_v1_secret_hash_retained": bool(legacy), "operational_status": "unused", "replacement_scheme": "direct_user_instruction_v2"}
    con.execute("INSERT INTO protocol_migrations VALUES(?,?,?)", (key, now(), canonical(details)))
    event(con, "WORKFLOW_AUTHORIZER_V1_RETIRED", "system", "v1 workflow authorizer retained but operationally unused", status="RECORDED", data=details)
    con.commit()

def workflow_authorization(con, record_id, actor, scope, user_request):
    row = con.execute("SELECT created_by,knowledge_state,status,payload_json FROM protocol_records WHERE record_id=? AND record_type='DECISION'", (record_id,)).fetchone()
    if not row:
        raise ValueError("사용자 작업 지시 DECISION Record를 찾지 못했습니다.")
    payload = json.loads(row[3])
    expected_scope_hash = _workflow_hash(scope)
    expected_request_hash = _workflow_hash(user_request)
    valid = (
        row[0] == "user" and row[1] == "CONFIRMED_DECISION" and row[2] == "ACTIVE"
        and payload.get("decision_type") == "WORKFLOW_AUTHORIZATION"
        and payload.get("authorized_actor") == actor
        and payload.get("scope_hash") == expected_scope_hash
        and payload.get("user_request_hash") == expected_request_hash
        and payload.get("workflow_approval_state") == "APPROVED"
        and payload.get("workflow_scheme") == "direct_user_instruction_v2"
        and payload.get("direct_user_instruction") is True
    )
    if not valid:
        raise ValueError("DECISION Record가 현재 actor·scope·사용자 지시와 일치하는 승인 기록이 아닙니다.")
    if con.execute("SELECT 1 FROM workflow_authorization_usage WHERE authorization_record=?", (record_id,)).fetchone():
        raise ValueError("이 사용자 작업 지시 DECISION Record는 이미 다른 workflow run에 사용됐습니다.")
    return expected_scope_hash, expected_request_hash


def workflow_runner_allowed(con, actor, task_id, run_id, capability_token, action, scope):
    """Runtime token binds exactly one active task/run/main-actor context; it is not process identity."""
    row = con.execute("SELECT task_id,runner,token_hash,status FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    task = con.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    token_ok = bool(capability_token) and secrets.compare_digest(sha(capability_token), row[2]) if row else False
    granted = bool(row and task and row[0] == task_id and row[1] == actor and row[3] == "ACTIVE" and task[0] in {"IN_PROGRESS", "PARTIAL"} and token_ok)
    status = "ALLOWED" if granted else "BLOCKED"
    event(con, "WORKFLOW_RUNNER_PREFLIGHT", actor, f"{action}: {status}", task_id, status,
          {"action": action, "run_id": run_id, "scope": scope,
           "reason": "run/task/runner/token and active-task validation"})
    return granted


def cmd_workflow_authorize(args):
    if args.actor not in {"claude", "codex"}:
        raise ValueError("workflow authorization actor는 claude 또는 codex여야 합니다.")
    with lock():
        con = connect()
        # This command records a user's direct instruction as operational evidence.
        # It cannot technically prove who typed it; agents must never invoke it for themselves.
        mark_v1_authorizer_retired(con)
        decision = record(con, "DECISION", "user", {
            "decision_type": "WORKFLOW_AUTHORIZATION", "workflow_approval_state": "APPROVED",
            "scope": redact(args.scope), "authorized_actor": args.actor, "scope_hash": _workflow_hash(args.scope),
            "user_request_hash": _workflow_hash(args.user_request),
            "scope_summary": redact(args.scope), "user_request_summary": redact(args.user_request),
            "decision": "사용자가 지정 actor에게 이 작업의 workflow 시작·종합 기록을 직접 지시함",
            "decided_by": "USER", "one_time_use": True,
            "workflow_scheme": "direct_user_instruction_v2", "direct_user_instruction": True,
            "operational_evidence_only": True,
        }, knowledge_state="CONFIRMED_DECISION", status="ACTIVE", risk_level="MEDIUM")
        event(con, "WORKFLOW_AUTHORIZATION_RECORDED", "user", "Per-task direct user instruction recorded", status="CONFIRMED",
              data={"authorization_record": decision["record_id"], "authorized_actor": args.actor, "scope_hash": _workflow_hash(args.scope), "workflow_scheme": "direct_user_instruction_v2", "technical_identity_proof": False})
        con.close()
    print("WORKFLOW_AUTHORIZATION_RECORD=" + decision["record_id"])

def workflow_task_record(con, task_id):
    for row in con.execute("SELECT record_id,payload_json FROM protocol_records WHERE record_type='TASK' ORDER BY rowid DESC"):
        payload = json.loads(row[1])
        if payload.get("task_id") == task_id:
            return row[0]
    raise ValueError(f"작업 #{task_id}의 workflow TASK Record를 찾지 못했습니다.")


def workflow_results(con, task_id, run_id):
    found = []
    for row in con.execute("SELECT record_id,record_type,payload_json,status FROM protocol_records ORDER BY rowid"):
        payload = json.loads(row[2])
        if payload.get("workflow_task_id") == task_id and payload.get("workflow_run_id") == run_id and payload.get("workflow_role") in WORKFLOW_ROLES:
            found.append({"record_id": row[0], "record_type": row[1], "payload": payload, "status": row[3]})
    return found


def cmd_workflow_start(args):
    required_roles = [x.strip() for x in args.required_roles.split(",") if x.strip()]
    invalid = set(required_roles) - WORKFLOW_ROLES
    if invalid: raise ValueError("지원하지 않는 workflow 역할: " + ", ".join(sorted(invalid)))
    with lock():
        con = connect()
        if not workflow_allowed(con, args.actor, None, "workflow-start", args.scope):
            con.close(); print("워크플로우 시작이 별도 권한 경계에서 차단됐습니다."); return 3
        scope_hash, request_hash = workflow_authorization(con, args.authorization_record, args.actor, args.scope, args.user_request)
        stamp = now()
        cur = con.execute("INSERT INTO tasks(title,actor,scope,baseline,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                          (redact(args.title), args.actor, redact(args.scope), redact(args.baseline), "IN_PROGRESS", stamp, stamp))
        task_id = cur.lastrowid
        run_id = "run-" + uuid.uuid4().hex
        capability_token = "wft-" + secrets.token_urlsafe(32)
        con.execute("INSERT INTO workflow_runs(run_id,task_id,runner,token_hash,created_at,status) VALUES(?,?,?,?,?,?)",
                    (run_id, task_id, args.actor, sha(capability_token), stamp, "ACTIVE"))
        con.execute("INSERT INTO workflow_authorization_usage(authorization_record,run_id,task_id,actor,scope_hash,request_hash,consumed_at) VALUES(?,?,?,?,?,?,?)",
                    (args.authorization_record, run_id, task_id, args.actor, scope_hash, request_hash, stamp))
        task = record(con, "TASK", args.actor, {
            "task_id": task_id, "title": redact(args.title), "owner": args.actor, "scope": redact(args.scope),
            "baseline": redact(args.baseline), "user_request": redact(args.user_request),
            "required_roles": required_roles, "visual_change": args.visual_change, "e2e_required": args.e2e_required,
            "workflow_run_id": run_id, "workflow": "actor-neutral-subagent-pipeline", "workflow_authorization_record": args.authorization_record,
            "workflow_authorized_actor": args.actor, "scope_hash": scope_hash, "user_request_hash": request_hash,
        }, knowledge_state="USER_INTENT", status="ACTIVE", risk_level="MEDIUM")
        state = record(con, "STATE", args.actor, {
            "scope_type": "TASK", "scope_ref": task["record_id"], "summary": "IN_PROGRESS",
            "workflow_task_id": task_id, "workflow_run_id": run_id, "required_roles": required_roles,
            "next_action": "dispatch planner or the first required role",
        }, knowledge_state="FACT", status="ACTIVE", risk_level="LOW")
        relation(con, args.actor, task["record_id"], state["record_id"], "result_of")
        event(con, "WORKFLOW_STARTED", args.actor, "Actor-neutral subagent workflow started", task_id, "RECORDED",
              {"task_record": task["record_id"], "state_record": state["record_id"], "required_roles": required_roles, "run_id": run_id})
        con.commit(); con.close()
    print(f"TASK_ID={task_id}\nRUN_ID={run_id}\nTASK_RECORD={task['record_id']}\nWORKFLOW_CAPABILITY_TOKEN={capability_token}")


def cmd_workflow_dispatch(args):
    if args.role not in WORKFLOW_ROLES: raise ValueError("지원하지 않는 workflow 역할입니다.")
    with lock():
        con = connect(); task_exists(con, args.task)
        if not workflow_runner_allowed(con, args.actor, args.task, args.run, args.capability_token, "workflow-dispatch", args.scope):
            con.close(); print("배정이 별도 권한 경계에서 차단됐습니다."); return 3
        duplicate = any(
            (payload := json.loads(row[0])).get("workflow_task_id") == args.task
            and payload.get("workflow_run_id") == args.run and payload.get("workflow_role") == args.role
            for row in con.execute("SELECT payload_json FROM protocol_records WHERE record_type='REQUEST'")
        )
        if duplicate: raise ValueError("같은 task/run/role에는 workflow-dispatch를 한 번만 실행할 수 있습니다.")
        task_record = workflow_task_record(con, args.task)
        request = record(con, "REQUEST", args.actor, {
            "request_type": "SUBAGENT_WORK", "from_agent": args.actor, "to_agent": args.role,
            "scope": redact(args.scope), "why": redact(args.why), "context": redact(args.context),
            "expected_result": "ARCHIVE_RESULT structured final report", "constraints": redact(args.constraints),
            "work": redact(args.work), "dependencies": redact(args.dependencies), "reporting": "Return ARCHIVE_RESULT only; the authorized main actor records it through workflow-result.",
            "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role,
        }, knowledge_state="FACT", status="ACTIVE", risk_level="LOW")
        relation(con, args.actor, task_record, request["record_id"], "result_of")
        # REQUEST lifecycle remains explicit and append-only, as required by the protocol verifier.
        for lifecycle in ("ACKNOWLEDGED", "IN_PROGRESS"):
            state = record(con, "STATE", args.actor, {
                "scope_type": "REQUEST", "scope_ref": request["record_id"], "summary": lifecycle,
                "request": request["record_id"], "lifecycle": lifecycle, "effective_request_status": lifecycle,
                "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role,
            }, knowledge_state="FACT", status="ACTIVE", risk_level="LOW")
            relation(con, args.actor, request["record_id"], state["record_id"], "result_of")
        event(con, "WORKFLOW_DISPATCHED", args.actor, f"{args.role} dispatched", args.task, "RECORDED",
              {"request_record": request["record_id"], "role": args.role, "scope": redact(args.scope)})
        con.close()
    context = {
        "task_id": args.task, "run_id": args.run, "task_record": task_record, "request_record": request["record_id"],
        "role": args.role, "archive_result_contract": {
            "role": args.role, "outcome": "PASS|FAIL|PARTIAL|NOT_APPLICABLE", "summary": "fact-based result",
            "scope": "reviewed/changed scope", "evidence": ["test, file, render, or inspection evidence"],
            "verification_status": "VERIFIED|PARTIAL|UNVERIFIED|NEEDS_VERIFICATION", "issues": [], "coverage_limits": [], "next_action": "next step",
        },
    }
    print("REQUEST_RECORD=" + request["record_id"])
    print("ARCHIVE_CONTEXT=" + canonical(context))


def cmd_workflow_result(args):
    result = parse_json_object(args.result, "ARCHIVE_RESULT")
    required = {"role", "outcome", "summary", "scope", "evidence", "verification_status", "issues", "coverage_limits", "next_action"}
    missing = required - set(result)
    if missing: raise ValueError("ARCHIVE_RESULT 필수 필드 누락: " + ", ".join(sorted(missing)))
    if result["role"] not in WORKFLOW_ROLES or result["role"] != args.role: raise ValueError("ARCHIVE_RESULT role이 배정 역할과 다릅니다.")
    if result["outcome"] not in WORKFLOW_OUTCOMES: raise ValueError("ARCHIVE_RESULT outcome 값이 올바르지 않습니다.")
    if result["verification_status"] not in {"VERIFIED", "PARTIAL", "UNVERIFIED", "NEEDS_VERIFICATION"}: raise ValueError("verification_status 값이 올바르지 않습니다.")
    if not isinstance(result["evidence"], list) or not isinstance(result["issues"], list) or not isinstance(result["coverage_limits"], list): raise ValueError("evidence, issues, coverage_limits는 배열이어야 합니다.")
    if any(not isinstance(item, str) or not item.strip() for item in result["issues"]): raise ValueError("issues 항목은 비어 있지 않은 문자열이어야 합니다.")
    if any(not isinstance(item, str) or not item.strip() for item in result["coverage_limits"]): raise ValueError("coverage_limits 항목은 비어 있지 않은 문자열이어야 합니다.")
    with lock():
        con = connect(); task_exists(con, args.task)
        if not workflow_runner_allowed(con, args.actor, args.task, args.run, args.capability_token, "workflow-result", result["scope"]):
            con.close(); print("결과 수집이 별도 권한 경계에서 차단됐습니다."); return 3
        task_record = workflow_task_record(con, args.task)
        request_row = con.execute("SELECT record_id,payload_json FROM protocol_records WHERE record_id=? AND record_type='REQUEST'", (args.request,)).fetchone()
        if not request_row: raise ValueError("배정 REQUEST Record를 찾지 못했습니다.")
        request_payload = json.loads(request_row[1])
        if (request_payload.get("workflow_task_id") != args.task or request_payload.get("workflow_run_id") != args.run
                or request_payload.get("workflow_role") != args.role or request_payload.get("from_agent") != args.actor):
            raise ValueError("REQUEST가 현재 task/run/role/runner의 workflow-dispatch 결과가 아닙니다.")
        if any(json.loads(row[0]).get("request_record") == args.request for row in con.execute("SELECT payload_json FROM protocol_records WHERE record_type='EVIDENCE'")):
            raise ValueError("같은 REQUEST의 workflow-result는 한 번만 기록할 수 있습니다.")
        if not isinstance(result.get("evidence"), list) or any(not isinstance(item, dict) for item in result["evidence"]):
            raise ValueError("ARCHIVE_RESULT evidence는 type·status·role·detail을 가진 객체 배열이어야 합니다.")
        for item in result["evidence"]:
            required_evidence = {"type", "status", "role", "detail"}
            if required_evidence - set(item): raise ValueError("evidence 항목 필수 필드 누락: " + ", ".join(sorted(required_evidence-set(item))))
            if item["status"] not in {"VERIFIED", "PARTIAL", "UNVERIFIED", "NEEDS_VERIFICATION"}: raise ValueError("evidence status 값이 올바르지 않습니다.")
            if item["role"] != args.role: raise ValueError("evidence role은 ARCHIVE_RESULT role과 정확히 일치해야 합니다.")
        evidence = record(con, "EVIDENCE", args.actor, {
            "source_type": "subagent_final_report", "content": {"summary": result["summary"], "evidence": result["evidence"], "outcome": result["outcome"], "coverage_limits": result["coverage_limits"]},
            "verification_status": result["verification_status"], "scope": result["scope"],
            "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role, "request_record": args.request,
        }, knowledge_state="FACT" if result["outcome"] != "PARTIAL" else "UNKNOWN", status="ACTIVE", risk_level="LOW")
        interpretation = record(con, "AI_INTERPRETATION", args.actor, {
            "interpretation": result["summary"], "derived_from": evidence["record_id"], "scope": result["scope"],
            "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role, "outcome": result["outcome"], "next_action": result["next_action"],
        }, knowledge_state="FACT" if result["outcome"] == "PASS" else "UNKNOWN", status="ACTIVE", risk_level="LOW")
        relation(con, args.actor, evidence["record_id"], interpretation["record_id"], "based_on")
        relation(con, args.actor, args.request, evidence["record_id"], "result_of")
        role_records = [evidence["record_id"], interpretation["record_id"]]
        if args.role in {"developer", "designer"}:
            change = record(con, "CHANGE", args.actor, {
                "change_type": "SUBAGENT_IMPLEMENTATION_RESULT", "target_record_id": task_record,
                "reason": result["summary"], "basis": evidence["record_id"], "verification": result["verification_status"],
                "previous_value": "implementation pending", "new_value": result["outcome"], "changed_by": args.role, "base_version": 1,
                "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role,
            }, knowledge_state="FACT", status="ACTIVE", risk_level="LOW")
            relation(con, args.actor, evidence["record_id"], change["record_id"], "supports"); role_records.append(change["record_id"])
        if args.role == "quality-watcher":
            review = record(con, "REVIEW", args.actor, {
                "review_type": "QUALITY_WATCH", "reviewer": args.role, "review_target": task_record,
                "verification": result["verification_status"], "result": result["outcome"], "summary": result["summary"],
                "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role,
            }, knowledge_state="FACT" if result["outcome"] == "PASS" else "UNKNOWN", status="ACTIVE", risk_level="LOW")
            relation(con, args.actor, evidence["record_id"], review["record_id"], "supports"); role_records.append(review["record_id"])
        # The final report advances the REQUEST lifecycle; an unverified result intentionally stops at RESULT.
        lifecycle_values = ["RESULT"]
        if result["verification_status"] == "VERIFIED" and result["outcome"] == "PASS":
            lifecycle_values += ["VERIFIED", "COMPLETED"]
        for lifecycle in lifecycle_values:
            lifecycle_record = record(con, "STATE", args.actor, {
                "scope_type": "REQUEST", "scope_ref": args.request, "summary": lifecycle,
                "request": args.request, "lifecycle": lifecycle, "effective_request_status": lifecycle,
                "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role, "verification": result["verification_status"],
            }, knowledge_state="FACT" if lifecycle != "RESULT" or result["outcome"] == "PASS" else "UNKNOWN", status="ACTIVE", risk_level="LOW")
            relation(con, args.actor, args.request, lifecycle_record["record_id"], "result_of")
            role_records.append(lifecycle_record["record_id"])
        for issue_text in result["issues"]:
            issue = record(con, "ISSUE", args.actor, {
                "title": f"{args.role} result issue", "description": str(issue_text), "next_action": result["next_action"],
                "workflow_task_id": args.task, "workflow_run_id": args.run, "workflow_role": args.role, "source_evidence": evidence["record_id"],
            }, knowledge_state="FACT" if result["outcome"] == "FAIL" else "UNKNOWN", status="ACTIVE", risk_level="MEDIUM")
            relation(con, args.actor, evidence["record_id"], issue["record_id"], "supports"); role_records.append(issue["record_id"])
        event(con, "WORKFLOW_RESULT_RECORDED", args.actor, f"{args.role} result recorded", args.task,
              "RECORDED" if result["outcome"] == "PASS" else result["outcome"],
              {"request_record": args.request, "records": role_records, "outcome": result["outcome"]})
        con.close()
    print("RESULT_RECORDS=" + canonical(role_records))


def workflow_issue_is_active(con, issue_record_id, task_record_id):
    """ISSUE is immutable; resolution is a TASK-scoped STATE payload, not a new scope enum."""
    rows_ = con.execute("SELECT payload_json FROM protocol_records WHERE record_type='STATE' ORDER BY rowid").fetchall()
    resolved = any(
        (payload := json.loads(row[0])).get("scope_type") == "TASK"
        and payload.get("scope_ref") == task_record_id
        and payload.get("issue_record_id") == issue_record_id
        and payload.get("issue_resolution") in {"RESOLVED", "SUPERSEDED"}
        for row in rows_
    )
    return not resolved


def cmd_workflow_issue_resolution(args):
    if args.resolution not in {"RESOLVED", "SUPERSEDED"}: raise ValueError("ISSUE resolution은 RESOLVED 또는 SUPERSEDED이어야 합니다.")
    with lock():
        con = connect(); task_exists(con, args.task)
        if not workflow_runner_allowed(con, args.actor, args.task, args.run, args.capability_token, "workflow-issue-resolution", args.basis):
            con.close(); print("ISSUE 해결 기록이 별도 권한 경계에서 차단됐습니다."); return 3
        issue = con.execute("SELECT payload_json FROM protocol_records WHERE record_id=? AND record_type='ISSUE'", (args.issue,)).fetchone()
        if not issue: raise ValueError("ISSUE Record를 찾지 못했습니다.")
        issue_payload = json.loads(issue[0])
        if issue_payload.get("workflow_task_id") != args.task or issue_payload.get("workflow_run_id") != args.run:
            raise ValueError("ISSUE가 현재 task/run의 workflow 결과가 아닙니다.")
        task_record = workflow_task_record(con, args.task)
        if not workflow_issue_is_active(con, args.issue, task_record): raise ValueError("ISSUE는 이미 해결 또는 대체 상태입니다.")
        evidence = con.execute("SELECT record_type,payload_json FROM protocol_records WHERE record_id=?", (args.evidence_record,)).fetchone()
        if not evidence or evidence[0] != "EVIDENCE": raise ValueError("해결 근거는 EVIDENCE Record여야 합니다.")
        evidence_payload = json.loads(evidence[1])
        if evidence_payload.get("workflow_task_id") != args.task or evidence_payload.get("workflow_run_id") != args.run:
            raise ValueError("해결 근거 EVIDENCE가 ISSUE와 같은 workflow task/run에 속하지 않습니다.")
        state = record(con, "STATE", args.actor, {
            "scope_type": "TASK", "scope_ref": task_record, "summary": "ISSUE_RESOLUTION",
            "workflow_task_id": args.task, "workflow_run_id": args.run, "issue_record_id": args.issue,
            "issue_resolution": args.resolution, "resolution_basis": redact(args.basis),
            "resolution_evidence_ref": args.evidence_record, "resolved_by": args.actor,
        }, knowledge_state="FACT", status="ACTIVE", risk_level="LOW")
        relation(con, args.actor, args.issue, state["record_id"], "result_of")
        relation(con, args.actor, args.evidence_record, state["record_id"], "supports")
        event(con, "WORKFLOW_ISSUE_" + args.resolution, args.actor, "Workflow ISSUE resolution recorded", args.task, "RECORDED",
              {"issue": args.issue, "state_record": state["record_id"], "evidence_record": args.evidence_record, "resolution": args.resolution})
        con.close()
    print("ISSUE_RESOLUTION_RECORD=" + state["record_id"])


def workflow_result_has_blocking_gap(evidence):
    """Return whether a final report leaves a completion-blocking result gap.

    Planner and reuse-scout can report a PASS with a declared, non-defect coverage
    limit. Their limited verification does not replace developer/designer/quality
    verification, which remains a completion gate.
    """
    outcome = evidence.get("content", {}).get("outcome")
    if outcome in {"FAIL", "PARTIAL", "NOT_APPLICABLE"}:
        return True
    verification = evidence.get("verification_status")
    if verification not in {"PARTIAL", "UNVERIFIED", "NEEDS_VERIFICATION"}:
        return False
    role = evidence.get("workflow_role")
    coverage_limits = evidence.get("content", {}).get("coverage_limits", [])
    return not (role in {"planner", "reuse-scout"} and outcome == "PASS" and bool(coverage_limits))


def cmd_workflow_finalize(args):
    with lock():
        con = connect(); task_exists(con, args.task)
        if not workflow_runner_allowed(con, args.actor, args.task, args.run, args.capability_token, "workflow-finalize", "task aggregation"):
            con.close(); print("종합이 별도 권한 경계에서 차단됐습니다."); return 3
        task_record = workflow_task_record(con, args.task)
        task_payload = json.loads(con.execute("SELECT payload_json FROM protocol_records WHERE record_id=?", (task_record,)).fetchone()[0])
        required_roles = task_payload.get("required_roles", [])
        results = workflow_results(con, args.task, args.run)
        roles = {r["payload"].get("workflow_role") for r in results if r["record_type"] == "EVIDENCE"}
        blockers = []
        missing = [role for role in required_roles if role not in roles]
        if missing: blockers.append("필수 역할 결과 누락: " + ", ".join(missing))
        evidence_payloads = [r["payload"] for r in results if r["record_type"] == "EVIDENCE"]
        quality = [r["payload"] for r in results if r["record_type"] == "REVIEW" and r["payload"].get("workflow_role") == "quality-watcher"]
        if any(x.get("result") != "PASS" for x in quality): blockers.append("quality-watcher PASS 검토가 없음 또는 결함이 있음")
        typed_evidence = [item for evidence in evidence_payloads for item in evidence.get("content", {}).get("evidence", []) if isinstance(item, dict)]
        if task_payload.get("visual_change") and not any(item.get("type") == "render" and item.get("status") == "VERIFIED" and item.get("role") in {"developer", "designer", "quality-watcher"} for item in typed_evidence): blockers.append("시각 변경의 실제 렌더링 VERIFIED 근거 누락")
        if task_payload.get("e2e_required") and not any(item.get("type") == "e2e" and item.get("status") == "VERIFIED" and item.get("role") in {"developer", "quality-watcher"} for item in typed_evidence): blockers.append("필수 end-to-end VERIFIED 근거 누락")
        if any(e.get("content", {}).get("outcome") == "FAIL" for e in evidence_payloads): blockers.append("FAIL 결과가 해결되지 않음")
        if any(workflow_result_has_blocking_gap(e) for e in evidence_payloads): blockers.append("unknown/partial 결과가 해결되지 않음")
        open_issues = [r for r in results if r["record_type"] == "ISSUE" and workflow_issue_is_active(con, r["record_id"], task_record)]
        if open_issues: blockers.append("미해결 ISSUE가 있음")
        status = "COMPLETED" if not blockers else "PARTIAL"
        state = record(con, "STATE", args.actor, {
            "scope_type": "TASK", "scope_ref": task_record, "summary": status,
            "workflow_task_id": args.task, "workflow_run_id": args.run, "required_roles": required_roles, "result_records": [r["record_id"] for r in results],
            "blockers": blockers, "next_action": "user/Claude review before next action" if blockers else "user decision/commit boundary if applicable",
            "handoff_required": bool(blockers),
        }, knowledge_state="FACT" if not blockers else "UNKNOWN", status="ACTIVE", risk_level="MEDIUM" if blockers else "LOW")
        relation(con, args.actor, task_record, state["record_id"], "result_of")
        con.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now(), args.task))
        event(con, "WORKFLOW_FINALIZED" if not blockers else "WORKFLOW_FINALIZE_BLOCKED", args.actor,
              "Workflow finalized" if not blockers else "Workflow completion blocked; partial state retained", args.task, status,
              {"state_record": state["record_id"], "blockers": blockers, "handoff_required": bool(blockers)})
        con.close()
    print("WORKFLOW_STATUS=" + status + "\nSTATE_RECORD=" + state["record_id"])
    if blockers: print("BLOCKERS=" + canonical(blockers)); return 3


def cmd_backfill_workflow_permission(args):
    if args.approval != "APPROVED": raise ValueError("사용자 승인 상태 APPROVED가 필요합니다.")
    key = "workflow-orchestration-permission-v2"
    with lock():
        con = connect()
        if con.execute("SELECT 1 FROM protocol_migrations WHERE migration_key=?", (key,)).fetchone():
            print("WORKFLOW_PERMISSION_RECORD=EXISTS"); con.close(); return
        profiles = []
        for actor in ("claude", "codex"):
            profiles.append(record(con, "PERMISSION_PROFILE", "user", {
                "agent_id": actor, "operation": "WORKFLOW_ORCHESTRATE", "permission_level": "EXECUTE",
                "approval_required": True, "scope": ".collab workflow orchestration only", "profile_version": "v2",
                "conditions": ["only after a matching per-task user WORKFLOW_AUTHORIZATION DECISION", "TASK/RUN dispatch/result/finalize aggregation only", "does not permit QMS file/data change, commit, deployment", "subagents return reports; they do not write the ledger directly"],
            }, knowledge_state="CONFIRMED_DECISION", status="ACTIVE", risk_level="MEDIUM"))
        con.execute("INSERT INTO protocol_migrations VALUES(?,?,?)", (key, now(), canonical({"records": [p["record_id"] for p in profiles], "P1_P6_preserved": True, "actor_neutral": True})))
        event(con, "WORKFLOW_PERMISSION_RECORDED", "user", "Claude/Codex workflow aggregation profiles recorded", status="CONFIRMED", data={"records": [p["record_id"] for p in profiles]})
        con.close()
    print("WORKFLOW_PERMISSION_RECORDS=" + canonical([p["record_id"] for p in profiles]))


def cmd_backfill_workflow_test_permission(args):
    """Record the narrow verification-only profile once; it grants no QMS access."""
    if args.approval != "APPROVED":
        raise ValueError("사용자 승인 상태 APPROVED가 필요합니다.")
    key = "workflow-test-permission-v1"
    with lock():
        con = connect()
        if con.execute("SELECT 1 FROM protocol_migrations WHERE migration_key=?", (key,)).fetchone():
            print("WORKFLOW_TEST_PERMISSION_RECORD=EXISTS"); con.close(); return
        profiles = []
        for actor in ("claude", "codex"):
            profiles.append(record(con, "PERMISSION_PROFILE", "user", {
                "agent_id": actor, "operation": "WORKFLOW_RUN_TEST", "permission_level": "EXECUTE",
                "approval_required": True, "scope": ".collab verification commands only", "profile_version": "v1",
                "conditions": [
                    "only during an active matching workflow task/run/main-actor/token context",
                    "only exact allowlisted commands: python -m unittest discover tests; python -m py_compile qms_audit.py; python qms_audit.py verify",
                    "does not permit arbitrary shell commands, QMS file/data change, commit, deployment",
                ],
            }, knowledge_state="CONFIRMED_DECISION", status="ACTIVE", risk_level="LOW"))
        con.execute("INSERT INTO protocol_migrations VALUES(?,?,?)", (
            key, now(), canonical({"records": [p["record_id"] for p in profiles], "scope": ".collab verification commands only", "allowlisted_commands": [list(command) for command in sorted(WORKFLOW_TEST_COMMANDS)]})
        ))
        event(con, "WORKFLOW_TEST_PERMISSION_RECORDED", "user",
              "Claude/Codex allowlisted .collab verification profiles recorded", status="CONFIRMED",
              data={"records": [p["record_id"] for p in profiles], "qms_write_commit_deploy": "not permitted"})
        con.close()
    print("WORKFLOW_TEST_PERMISSION_RECORDS=" + canonical([p["record_id"] for p in profiles]))

def cmd_staged_manifest(args):
    manifest=staged_manifest()
    print("STAGED_MANIFEST=" + manifest["hash"])


def cmd_git_commit_authorize(args):
    if args.actor not in {"claude", "codex"}:
        raise ValueError("Git commit authorization actor는 claude 또는 codex여야 합니다.")
    manifest=staged_manifest()
    with lock():
        con=connect()
        # Like workflow-authorize, this is operational evidence of a direct user instruction.
        # It cannot authenticate chat origin and agents must never call it without that instruction.
        decision=record(con, "DECISION", "user", {
            "decision_type":"GIT_COMMIT_AUTHORIZATION", "operation":"GIT_COMMIT", "approval_state":"APPROVED",
            "authorized_actor":args.actor, "scope":redact(args.scope), "scope_summary":redact(args.scope),
            "user_request_hash":_workflow_hash(args.user_request), "user_request_summary":redact(args.user_request),
            "manifest_algorithm":manifest["algorithm"], "manifest_hash":manifest["hash"], "staged_tree_hash":manifest["tree_hash"],
            "staged_paths":manifest["paths"], "staged_entry_count":manifest["entry_count"],
            "decision":"사용자가 지정 actor에게 현재 staged manifest의 Git commit을 직접 지시함",
            "decided_by":"USER", "one_time_use":True, "direct_user_instruction":True, "operational_evidence_only":True,
        }, knowledge_state="CONFIRMED_DECISION", status="ACTIVE", risk_level="MEDIUM")
        event(con, "GIT_COMMIT_AUTHORIZATION_RECORDED", "user", "Per-commit direct user instruction recorded", status="CONFIRMED", data={"approval_record":decision["record_id"],"authorized_actor":args.actor,"manifest_hash":manifest["hash"],"paths":manifest["paths"],"technical_identity_proof":False})
        con.close()
    print("GIT_COMMIT_APPROVAL_RECORD="+decision["record_id"])
    print("STAGED_MANIFEST="+manifest["hash"])


def cmd_git_commit(args):
    sha_value=args.commit or os.environ.get("GIT_COMMIT") or "UNKNOWN"
    approval_record=args.approval_record or os.environ.get("QMS_AUDIT_GIT_APPROVAL_RECORD")
    approved=os.environ.get("QMS_AUDIT_GIT_APPROVAL") == "APPROVED"
    with lock():
        con=connect(); status="PARTIAL" if sha_value=="UNKNOWN" else "RECORDED"
        event(con,"GIT_COMMIT",args.actor,f"Git commit recorded: {sha_value}",status=status,data={"commit":sha_value,"source":"post-commit hook","task_id_known":False,"approval_record":approval_record})
        if not (approved and approval_record and sha_value != "UNKNOWN"):
            con.close(); return 0
        tree=subprocess.run(["git","-C",str(git_repo_root()),"rev-parse",f"{sha_value}^{{tree}}"],text=True,capture_output=True)
        if tree.returncode:
            con.close(); raise ValueError("commit tree hash를 읽지 못했습니다.")
        preflight=con.execute("SELECT preflight_id,actor,manifest_hash,tree_hash FROM git_commit_preflights WHERE approval_record=? AND actor=? AND tree_hash=? ORDER BY rowid DESC LIMIT 1",(approval_record,args.actor,tree.stdout.strip())).fetchone()
        unused=not con.execute("SELECT 1 FROM git_commit_authorization_usage WHERE approval_record=?",(approval_record,)).fetchone()
        if not preflight or not unused:
            event(con,"GIT_COMMIT_AUTHORIZATION_CONSUME_FAILED",args.actor,"Successful commit could not consume its one-time authorization",status="FAILED",data={"commit":sha_value,"approval_record":approval_record,"reason":"matching preflight missing or approval already consumed"}); con.close(); return 3
        con.execute("INSERT INTO git_commit_authorization_usage VALUES(?,?,?,?,?,?,?)",(approval_record,preflight[0],args.actor,sha_value,preflight[2],preflight[3],now()))
        con.commit()
        event(con,"GIT_COMMIT_AUTHORIZATION_CONSUMED",args.actor,"Successful commit consumed one-time authorization",status="RECORDED",data={"commit":sha_value,"approval_record":approval_record,"preflight_id":preflight[0],"manifest_hash":preflight[2],"tree_hash":preflight[3]})
        con.close()


def cmd_deploy_authorize(args):
    if args.actor not in {"claude", "codex"}:
        raise ValueError("Deploy authorization actor는 claude 또는 codex여야 합니다.")
    repo = git_repo_root()
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, capture_output=True)
    if head.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", head.stdout.strip()):
        raise ValueError("HEAD commit hash를 계산하지 못했습니다.")
    commit_hash = head.stdout.strip()
    with lock():
        con=connect()
        # git-commit-authorize와 같은 성격: 사용자 직접 지시의 운영상 근거이며 채팅
        # 출처를 기술적으로 증명하지 않는다. HEAD의 정확한 commit hash + remote 이름에
        # 결속된 1회성 승인이다 — 그 사이 HEAD가 바뀌면(추가 커밋 등) 승인은 무효가 된다.
        decision=record(con, "DECISION", "user", {
            "decision_type":"DEPLOY_AUTHORIZATION", "operation":"DEPLOY", "approval_state":"APPROVED",
            "authorized_actor":args.actor, "remote":args.remote, "commit_hash":commit_hash,
            "scope":redact(args.scope), "scope_summary":redact(args.scope),
            "user_request_hash":_workflow_hash(args.user_request), "user_request_summary":redact(args.user_request),
            "decision":"사용자가 지정 actor에게 현재 HEAD 커밋의 지정 remote 배포를 직접 지시함",
            "decided_by":"USER", "one_time_use":True, "direct_user_instruction":True, "operational_evidence_only":True,
        }, knowledge_state="CONFIRMED_DECISION", status="ACTIVE", risk_level="HIGH")
        event(con, "DEPLOY_AUTHORIZATION_RECORDED", "user", "Per-deploy direct user instruction recorded", status="CONFIRMED", data={"approval_record":decision["record_id"],"authorized_actor":args.actor,"remote":args.remote,"commit_hash":commit_hash,"technical_identity_proof":False})
        con.close()
    print("DEPLOY_APPROVAL_RECORD="+decision["record_id"])
    print("DEPLOY_COMMIT="+commit_hash)


def cmd_bootstrap(args):
    """Create protocol records from local facts. It never changes Claude's existing configuration."""
    with lock():
        con=connect()
        if con.execute("SELECT 1 FROM protocol_migrations WHERE migration_key='protocol-bootstrap-v1'").fetchone():
            print("프로토콜 기준 Record가 이미 준비되어 있습니다."); con.close(); return
        project=record(con,"PROJECT","system",{"name":"IQC 입고검사 성적서 자동화 시스템","purpose":"QMS 협업 기록·작업·검토·인계를 안전하게 보존","current_owner":"codex","project_rules_ref":"AGENTS.md, CLAUDE.md, CLAUDE-CHATGPT-PROTOCOL-v1.0.0-draft.md"},knowledge_state="FACT",status="ACTIVE")
        for actor, role, source in (("user","Project Owner","사용자 명시 역할"),("claude","existing Claude configuration","기존 Claude 설정 참조만; 수정하지 않음"),("codex","Project Owner","사용자 명시 역할")):
            record(con,"AGENT","system",{"agent_name":actor,"role":role,"model":"UNKNOWN","model_version":"UNKNOWN","instructions_ref":source,"skills":[],"tools":[],"permission_profile_ref":f"local-{actor}-profile"},knowledge_state="FACT",status="ACTIVE")
        # Profiles encode local operating boundaries, not a replacement for existing Claude configuration.
        profiles=[
          ("user","READ","EXECUTE",False,"QMS project",["user terminal and explicit user decisions"]),
          ("claude","READ","READ",True,"iqc-app",["existing configuration retained; local protocol does not elevate"]),
          ("claude","CREATE_PROPOSAL","PROPOSE",False,".collab REQUEST/REVIEW only",["safe collaboration records only; no QMS file/data change"]),
          ("codex","READ","READ",True,"iqc-app",["P-1/P-3 apply until a recorded approval and conditions exist"]),
          ("codex","CREATE_PROPOSAL","PROPOSE",False,".collab REQUEST/REVIEW only",["safe collaboration records only; no QMS file/data change"]),
        ]
        for agent,op,level,approval,scope,conditions in profiles:
            record(con,"PERMISSION_PROFILE","system",{"agent_id":agent,"operation":op,"permission_level":level,"approval_required":approval,"scope":scope,"conditions":conditions},knowledge_state="FACT",status="ACTIVE")
        decision=record(con,"DECISION","user",{"task_id":None,"decision_type":"DESIGN","decision":"QMS collaboration archive and handoff storage is unified under .collab; post-commit hook installation approved.","decided_by":"USER","decision_basis":"explicit user decision in this session","scope":"iqc-app .collab archive migration"},knowledge_state="CONFIRMED_DECISION",status="ACTIVE",risk_level="HIGH")
        change=record(con,"CHANGE","system",{"task_id":None,"change_type":"ARCHIVE_BACKEND_MIGRATION","target_record_id":project["record_id"],"previous_value":"docs/archive handoff storage","new_value":".collab/handoffs canonical archive with docs/EMERGENCY_HANDOFF.md current signal","changed_by":"system","base_version":1,"reason":"user-approved archive backend migration","basis":decision["record_id"],"verification":"pending protocol verification"},knowledge_state="FACT",status="ACTIVE",risk_level="HIGH")
        relation(con,"system",decision["record_id"],change["record_id"],"supports")
        con.execute("INSERT INTO protocol_migrations VALUES(?,?,?)",("protocol-bootstrap-v1",now(),canonical({"project_record":project["record_id"],"tbd":"TBD-0002..0011 remain unresolved"})))
        event(con,"PROTOCOL_BOOTSTRAP","system","Protocol v1 local foundation created",status="RECORDED",data={"project_record":project["record_id"],"preserves_existing_claude_configuration":True})
        con.close()
    print("프로토콜 기준 Record·권한 프로필을 만들었습니다.")

def cmd_migrate_legacy(args):
    with lock():
        con=connect(); migrated=0
        existing={r[0] for r in con.execute("SELECT migration_key FROM protocol_migrations WHERE migration_key LIKE 'legacy-event:%'")}
        for item in rows(con):
            key="legacy-event:"+item["event_id"]
            if key in existing: continue
            rec=record(con,"EVIDENCE","system",{"source_type":"legacy_audit_event","source_ref":".collab/events/*.jsonl + runtime/audit.sqlite3","original_ref":item["event_id"],"captured_at":item["occurred_at"],"captured_by":item["actor"],"content":item,"content_type":"application/json","verification_status":"UNVERIFIED","scope":"historical migration; original audit event retained"},knowledge_state="FACT",status="ARCHIVED")
            con.execute("INSERT INTO protocol_migrations VALUES(?,?,?)",(key,now(),canonical({"record_id":rec["record_id"]}))); con.commit(); migrated+=1
        event(con,"LEGACY_MIGRATION","system",f"Legacy audit events represented as immutable Evidence: {migrated}",status="RECORDED",data={"migrated":migrated,"source_preserved":True}); con.close()
    print(f"기존 감사 이벤트 {migrated}건을 새 Record로 표현했습니다. 원본은 변경하지 않았습니다.")

def parse_json_object(text, label):
    try:
        value=json.loads(text)
    except json.JSONDecodeError as exc: raise ValueError(f"{label}은 JSON 객체여야 합니다: {exc.msg}")
    if not isinstance(value,dict): raise ValueError(f"{label}은 JSON 객체여야 합니다.")
    return value

def cmd_record(args):
    payload=parse_json_object(args.payload,"payload")
    with lock():
        guarded=args.record_type in ("STATE","RULE","DECISION","PERMISSION_PROFILE")
        con=connect(); result,_,risk=preflight(con,args.actor,"UPDATE_STATE" if guarded else "CREATE_PROPOSAL",approval=args.approval,scope=args.scope)
        if result=="BLOCKED":
            print("기록 대상 Operation이 차단됐습니다. 감사 이벤트는 남았습니다."); con.close(); return 3
        if args.record_type=="AI_INTERPRETATION":
            evidence_id=payload.get("derived_from")
            evidence=con.execute("SELECT 1 FROM protocol_records WHERE record_id=? AND record_type='EVIDENCE'",(evidence_id,)).fetchone()
            if not evidence: raise ValueError("AI_INTERPRETATION은 기존 EVIDENCE Record를 derived_from으로 지정해야 합니다.")
        rec=record(con,args.record_type,args.actor,payload,knowledge_state=args.knowledge_state,status=args.status,risk_level=args.risk_level or risk)
        if args.record_type=="AI_INTERPRETATION": relation(con,args.actor,payload["derived_from"],rec["record_id"],"based_on")
        event(con,"RECORD_CREATED",args.actor,f"{args.record_type} created",status="RECORDED",data={"record_id":rec["record_id"],"record_type":args.record_type})
        con.close()
    print("RECORD_ID="+rec["record_id"])

def cmd_relation(args):
    with lock():
        con=connect(); rel=relation(con,args.actor,args.from_record,args.to_record,args.relation_type)
        event(con,"RELATION_CREATED",args.actor,"Record relation created",status="RECORDED",data=rel); con.close()
    print("RELATION_ID="+rel["relation_id"])

def cmd_preflight(args):
    with lock():
        con=connect(); result,need,risk=preflight(con,args.actor,args.operation,approval=args.approval,scope=args.scope,task_id=args.task,approval_record=getattr(args,"approval_record",None),staged_manifest_hash=getattr(args,"staged_manifest",None),deploy_commit_hash=getattr(args,"deploy_commit",None),deploy_remote=getattr(args,"remote",None)); con.close()
    print(f"{result}: permission={need}, risk={risk}"); return 0 if result=="ALLOWED" else 3

def cmd_handoff(args):
    payload=parse_json_object(args.payload,"payload")
    required={"reason","current_state","completed_work","unresolved","risks","important_evidence","decisions","next_action","verification","context_hint"}
    missing=sorted(required-set(payload))
    if missing: raise ValueError("Handoff 필수 내용 누락: "+", ".join(missing))
    emergency=args.emergency
    if emergency:
        for key in ("emergency_reason","last_known_state","last_verification","unfinished_work","risks","next_action"):
            if key not in payload: raise ValueError("Emergency Handoff 필수 내용 누락: "+key)
    payload.update({"task_id":args.task,"previous_owner":args.actor,"new_owner":args.new_owner,"handoff_kind":"EMERGENCY" if emergency else "NORMAL","transfer_status":"HANDOFF_CREATED","approval_status":args.approval})
    with lock():
        con=connect(); allowed,_,_=preflight(con,args.actor,"UPDATE_STATE",approval=args.approval,scope="handoff",task_id=args.task)
        if allowed=="BLOCKED": con.close(); print("인계가 권한/승인 경계에서 차단됐습니다."); return 3
        rec=record(con,"HANDOFF",args.actor,payload,knowledge_state="FACT",status="ACTIVE",risk_level="MEDIUM")
        # A readable handoff is a representation, never a replacement of the canonical record.
        handoff_id=rec['record_id'].replace('rec-','')
        path=HANDOFFS/f"handoff-{handoff_id}.md"
        md=f"""# EMERGENCY HANDOFF\n\n- HANDOFF_ID: {handoff_id}\n- 작성 에이전트: {args.actor}\n- 작성 시각: {rec['created_at']}\n- 중단 사유: {payload['reason']}\n\n## 완료된 변경\n- {payload['completed_work']}\n\n## 미완료 작업\n- {payload['unresolved']}\n\n## 현재 Git 상태\n- 마지막 커밋: {payload.get('git_commit','UNKNOWN')}\n- 미커밋 변경: {payload.get('git_status','UNKNOWN')}\n\n## 검증 수행 여부\n- quality-watcher 통과: {payload.get('quality_review','미수행')}\n- 실제 렌더링 확인: {payload.get('render_verification','미수행')}\n\n## 재개 시 첫 번째 확인 명령\n- {payload.get('first_check','git status')}\n\n## Codex 대체 작업 허용 범위\n- {payload.get('codex_scope','허용 안 함')}\n\n## 사용자 승인 필요 여부\n- {payload.get('user_approval','예 — 범위 확인 필요')}\n\n## Protocol context\n- Record: {rec['record_id']}\n- Important Evidence: {payload['important_evidence']}\n- Decisions: {payload['decisions']}\n- Next action: {payload['next_action']}\n- Verification: {payload['verification']}\n"""
        path.write_text(md,encoding="utf-8")
        if emergency:
            signal=ROOT.parent/"docs"/"EMERGENCY_HANDOFF.md"
            if signal.exists():
                old_text=signal.read_text(encoding="utf-8")
                match=re.search(r"^\s*- HANDOFF_ID:\s*([^\s]+)",old_text,re.MULTILINE)
                if not match: raise RuntimeError("기존 EMERGENCY_HANDOFF.md의 HANDOFF_ID를 읽을 수 없어 덮어쓰기를 차단했습니다.")
                old=HANDOFFS/f"handoff-{match.group(1)}.md"
                if old.exists() and old.read_text(encoding="utf-8")!=old_text: raise RuntimeError("동일 HANDOFF_ID의 archive 내용이 달라 덮어쓰기를 차단했습니다.")
                if not old.exists(): old.write_text(old_text,encoding="utf-8")
            signal.write_text(md,encoding="utf-8")
        change=record(con,"CHANGE",args.actor,{"change_type":"HANDOFF_CREATED","target_record_id":rec["record_id"],"reason":"handoff created","basis":payload["reason"],"verification":payload["verification"],"previous_value":args.actor,"new_value":args.new_owner,"changed_by":args.actor,"base_version":1},knowledge_state="FACT",status="ACTIVE",risk_level="MEDIUM")
        relation(con,args.actor,rec["record_id"],change["record_id"],"result_of")
        event(con,"HANDOFF_CREATED",args.actor,"Emergency handoff created" if emergency else "Normal handoff created",args.task,"RECORDED",{"record_id":rec["record_id"],"change_record":change["record_id"],"file":str(path.relative_to(ROOT)),"new_owner":args.new_owner})
        con.close()
    print("HANDOFF_ID="+rec["record_id"])

def cmd_accept_handoff(args):
    with lock():
        con=connect(); row=con.execute("SELECT payload_json FROM protocol_records WHERE record_id=? AND record_type='HANDOFF'",(args.handoff,)).fetchone()
        if not row: raise ValueError("HANDOFF Record를 찾지 못했습니다.")
        p=json.loads(row[0]); previous=p.get("previous_owner")
        change=record(con,"CHANGE",args.actor,{"task_id":p.get("task_id"),"change_type":"OWNER_TRANSFER","target_record_id":args.handoff,"previous_value":previous,"new_value":args.actor,"changed_by":args.actor,"base_version":1,"reason":"handoff accepted","basis":args.basis,"verification":args.verification},knowledge_state="FACT",status="ACTIVE",risk_level="MEDIUM")
        relation(con,args.actor,args.handoff,change["record_id"],"handoff_to")
        event(con,"OWNER_CHANGED",args.actor,"Handoff accepted; owner transfer recorded",p.get("task_id"),"RECORDED",{"handoff":args.handoff,"change":change["record_id"],"previous_owner":previous,"new_owner":args.actor})
        con.close()
    print("OWNER_TRANSFER_RECORD="+change["record_id"])

def cmd_search(args):
    needle=args.query.lower(); con=connect(); found=[]
    for r in con.execute("SELECT record_id,record_type,status,knowledge_state,payload_json FROM protocol_records ORDER BY created_at DESC"):
        text=canonical(json.loads(r[4])).lower()
        if needle in text: found.append({"record_id":r[0],"record_type":r[1],"status":r[2],"knowledge_state":r[3]})
    con.close(); print(canonical({"status":"FOUND" if found else "NOT_FOUND","relevance":"text match in Record payload","summary":f"{len(found)} matching Records","matched_records":found[:args.limit],"why_relevant":"query appears in canonical payload","next_depth":"Depth 2 record detail; Depth 3 source Evidence"})); return 0

def cmd_envelope(args):
    payload=parse_json_object(args.payload,"payload")
    if args.sender not in ("claude","codex","user") or args.receiver not in ("claude","codex","user"): raise ValueError("등록된 협업 actor만 sender/receiver가 될 수 있습니다.")
    env={"protocol_name":"CLAUDE_CHATGPT_PROTOCOL","protocol_version":"1.0.0","message_id":"msg-"+uuid.uuid4().hex,"sender":args.sender,"receiver":args.receiver,"timestamp":now(),"payload":redact(payload)}
    OUTBOX.mkdir(parents=True,exist_ok=True)
    path=OUTBOX/f"{env['timestamp'].replace(':','')}-{env['message_id']}.json"; path.write_text(json.dumps(env,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with lock():
        con=connect(); event(con,"ENVELOPE_QUEUED",args.sender,"AI-to-AI envelope queued",status="RECORDED",data={"message_id":env["message_id"],"receiver":args.receiver,"file":str(path.relative_to(ROOT))}); con.close()
    print("MESSAGE_ID="+env["message_id"])

def cmd_resource_check(args):
    payload={"scope_type":"TASK","scope_ref":args.scope,"summary":args.state,"scope":args.scope,"resource_state":args.state,"safe_stop":args.safe_stop,"next_action":args.next_action}
    with lock():
        con=connect(); rec=record(con,"STATE",args.actor,payload,knowledge_state="FACT",status="NEEDS_VERIFICATION" if args.state!="SUFFICIENT" else "ACTIVE",risk_level="MEDIUM"); event(con,"RESOURCE_CHECK",args.actor,"Resource/safe-stop state recorded",status="RECORDED",data={"record_id":rec["record_id"],**payload}); con.close()
    print("RECORD_ID="+rec["record_id"])

def cmd_report(args):
    con=connect(); rows_out=[]
    for r in con.execute("SELECT record_type,knowledge_state,status,payload_json FROM protocol_records ORDER BY rowid DESC LIMIT ?",(args.limit,)):
        rows_out.append({"type":r[0],"knowledge_state":r[1],"status":r[2],"payload":json.loads(r[3])})
    con.close(); print(json.dumps({"FACTS":[x for x in rows_out if x["knowledge_state"]=="FACT"],"AI_VIEW":[x for x in rows_out if x["knowledge_state"] in ("INFERENCE","HYPOTHESIS")],"UNKNOWN_CONFLICT":[x for x in rows_out if x["knowledge_state"] in ("UNKNOWN","CONFLICT")],"OPTIONS":[],"RISK":[],"USER_DECISION_REQUIRED":[],"NEXT_ACTION":"Read linked Evidence before action"},ensure_ascii=False,indent=2))

def cmd_conflict(args):
    """Records a mismatch and deliberately never attempts an automatic merge."""
    with lock():
        con=connect(); row=con.execute("SELECT record_version FROM protocol_records WHERE record_id=?",(args.target_record,)).fetchone()
        if not row: raise ValueError("대상 Record를 찾지 못했습니다.")
        current=row[0]; cid="conf-"+uuid.uuid4().hex
        reason=args.reason if args.base_version!=current else args.reason+" (동일 버전이나 자동 병합은 구현하지 않음)"
        con.execute("INSERT INTO protocol_conflicts VALUES(?,?,?,?,?,?,?)",(cid,args.target_record,args.base_version,current,redact(reason),now(),"OPEN")); con.commit()
        conflict_item={"conflict_id":cid,"target_record_id":args.target_record,"incoming_base_version":args.base_version,"current_version":current,"reason":redact(reason),"created_at":now(),"status":"OPEN"}
        with (CONFLICTS_DIR/f"{conflict_item['created_at'][:7]}.jsonl").open("a",encoding="utf-8",newline="\n") as f: f.write(canonical(conflict_item)+"\n")
        # TBD-0005 (2026-09-21 resolved): ISSUE는 append-only로 취급한다 — status는
        # 절대 안 바꾸고(항상 ACTIVE), "해소됐다"는 사실은 별도 STATE Record로 연결해
        # 표현한다(workflow ISSUE와 동일한 관행, workflow_issue_is_active() 참고).
        # 예전엔 이 자리만 status="OPEN"으로 시작해서 다른 값(RESOLVED 등)으로 바뀔 걸
        # 전제한 것처럼 보였는데, 실제로 이 status를 UPDATE하는 코드는 어디에도 없었다.
        issue=record(con,"ISSUE",args.actor,{"title":"Record version/merge conflict","description":reason,"priority":"HIGH","discovered_by":args.actor,"facts":{"target_record_id":args.target_record,"incoming_base_version":args.base_version,"current_version":current},"attempts":[],"failed_hypotheses":[],"current_hypothesis":"automatic merge prohibited until user-approved implementation","next_action":"USER_DECISION_REQUIRED","risks":"silent overwrite"},knowledge_state="CONFLICT",status="ACTIVE",risk_level="HIGH")
        relation(con,args.actor,args.target_record,issue["record_id"],"contradicts")
        event(con,"CONFLICT_DETECTED",args.actor,"Automatic merge blocked; conflict recorded",status="BLOCKED",data={"conflict_id":cid,"issue_record":issue["record_id"],"target_record":args.target_record,"base_version":args.base_version,"current_version":current})
        con.close()
    print("CONFLICT_ID="+cid+"; USER_DECISION_REQUIRED")


# Protocol v1.0.0 operational extensions.  These commands only preserve facts and
# block ambiguous state changes; they do not fill any TBD Registry item.
def _protocol_jsonl(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob('*.jsonl'): old.unlink()
    for item in rows:
        stamp=item.get('created_at') or item.get('accepted_at') or item.get('received_at') or item.get('registered_at') or now()
        with (folder/f"{stamp[:7]}.jsonl").open('a',encoding='utf-8',newline='\n') as f: f.write(canonical(item)+'\n')

def _table_rows(con, table):
    return [dict(r) for r in con.execute(f'SELECT * FROM {table} ORDER BY rowid')]

def _verify_append_rows(items, label, hash_key):
    problems=[]
    for item in items:
        raw={k:v for k,v in item.items() if k!=hash_key}
        if item.get(hash_key)!=sha(raw): problems.append(f'{label} hash mismatch: {item.get(hash_key, "UNKNOWN")}')
    return problems

def current_owner(con, task_id=None):
    if task_id is not None:
        row=con.execute('SELECT new_owner FROM handoff_acceptances WHERE task_id=? ORDER BY rowid DESC LIMIT 1',(task_id,)).fetchone()
        if row: return row[0]
        row=con.execute('SELECT actor FROM tasks WHERE id=?',(task_id,)).fetchone()
        return row[0] if row else 'UNKNOWN'
    row=con.execute('SELECT new_owner FROM handoff_acceptances WHERE task_id IS NULL ORDER BY rowid DESC LIMIT 1').fetchone()
    return row[0] if row else 'UNKNOWN'

def _preflight_or_block(con, actor, operation, approval='MISSING', scope='', task_id=None):
    result,_,_=preflight(con,actor,operation,approval=approval,scope=scope,task_id=task_id)
    return result=='ALLOWED'

def cmd_accept_handoff_v2(args):
    with lock():
        con=connect(); row=con.execute("SELECT payload_json,status,created_by FROM protocol_records WHERE record_id=? AND record_type='HANDOFF'",(args.handoff,)).fetchone()
        if not row: raise ValueError('HANDOFF Record를 찾지 못했습니다.')
        if row['status'] not in ('ACTIVE','PENDING_ACCEPTANCE'): raise ValueError('수락 가능한 HANDOFF 상태가 아닙니다.')
        p=json.loads(row['payload_json'])
        if p.get('new_owner')!=args.actor: raise ValueError('지정된 new_owner만 HANDOFF를 수락할 수 있습니다.')
        if con.execute('SELECT 1 FROM handoff_acceptances WHERE handoff_id=?',(args.handoff,)).fetchone(): raise ValueError('이미 수락된 HANDOFF입니다.')
        # The authorization boundary was checked when the designated handoff was created.
        # Acceptance only confirms that exact recipient; it must not silently change owner again.
        authorized = p.get('approval_status')=='APPROVED' or row['created_by']=='user'
        event(con,'OPERATION_PREFLIGHT',args.actor,'HANDOFF_ACCEPT: '+('ALLOWED' if authorized else 'BLOCKED'),p.get('task_id'),'ALLOWED' if authorized else 'BLOCKED',{'operation':'HANDOFF_ACCEPT','required_permission':'designated recipient','granted_permission':args.actor,'approval_status':p.get('approval_status','inherited from user-created handoff'),'reason':'initial handoff authorization' if authorized else 'handoff lacks recorded authorization'})
        if not authorized:
            con.close(); print('인계 수락이 권한/승인 경계에서 차단됐습니다.'); return 3
        previous=p.get('previous_owner','UNKNOWN'); acceptance={'acceptance_id':'accept-'+uuid.uuid4().hex,'handoff_id':args.handoff,'task_id':p.get('task_id'),'previous_owner':previous,'new_owner':args.actor,'accepted_at':now(),'basis':redact(args.basis),'verification':redact(args.verification)}
        acceptance['acceptance_hash']=sha(acceptance)
        con.execute('INSERT INTO handoff_acceptances VALUES(?,?,?,?,?,?,?,?,?)',tuple(acceptance.values())); con.commit()
        state=record(con,'STATE',args.actor,{'scope_type':'TASK' if p.get('task_id') else 'PROJECT','scope_ref':str(p.get('task_id') or PROJECT_ID),'summary':'ACTIVE_OWNER','owner':args.actor,'previous_owner':previous,'handoff_record':args.handoff,'verification':args.verification},knowledge_state='FACT',status='ACTIVE',risk_level='MEDIUM')
        change=record(con,'CHANGE',args.actor,{'task_id':p.get('task_id'),'change_type':'OWNER_TRANSFER','target_record_id':args.handoff,'previous_value':previous,'new_value':args.actor,'changed_by':args.actor,'base_version':1,'reason':'designated owner accepted handoff','basis':args.basis,'verification':args.verification},knowledge_state='FACT',status='ACTIVE',risk_level='MEDIUM')
        relation(con,args.actor,args.handoff,change['record_id'],'handoff_to'); relation(con,args.actor,change['record_id'],state['record_id'],'result_of')
        event(con,'OWNER_CHANGED',args.actor,'Designated handoff accepted; active ownership recorded',p.get('task_id'),'RECORDED',{'handoff':args.handoff,'acceptance_id':acceptance['acceptance_id'],'state_record':state['record_id'],'previous_owner':previous,'new_owner':args.actor})
        con.close()
    print('OWNER_TRANSFER_RECORD='+change['record_id'])

def validate_envelope(env, receiver):
    required={'protocol_name','protocol_version','message_id','sender','receiver','timestamp','payload'}
    if not isinstance(env,dict) or required-set(env): raise ValueError('Envelope 필수 필드가 누락됐습니다.')
    if env['protocol_name']!='CLAUDE_CHATGPT_PROTOCOL' or env['protocol_version']!='1.0.0': raise ValueError('지원하지 않는 protocol/version Envelope입니다. adapter 등록 전에는 수신을 차단합니다.')
    if env['receiver']!=receiver: raise ValueError('수신자와 Envelope receiver가 다릅니다.')
    if env['sender'] not in ('user','claude','codex') or receiver not in ('user','claude','codex'): raise ValueError('등록되지 않은 actor입니다.')
    if not isinstance(env['payload'],dict): raise ValueError('Envelope payload는 JSON 객체여야 합니다.')

def cmd_receive_envelope(args):
    source=Path(args.file).resolve()
    if not source.is_file(): raise ValueError('수신할 Envelope 파일을 찾지 못했습니다.')
    raw=source.read_text(encoding='utf-8')
    try: env=json.loads(raw); validate_envelope(env,args.receiver)
    except (json.JSONDecodeError,ValueError) as exc:
        with lock():
            con=connect(); event(con,'ENVELOPE_RECEIVE_FAILED',args.receiver,'Envelope preserved; validation blocked',status='BLOCKED',data={'file':str(source),'reason':str(exc),'sync_action':'preserve source and request retransmission'}); con.close()
        raise ValueError('Envelope 검증 실패: '+str(exc))
    with lock():
        con=connect()
        if con.execute('SELECT 1 FROM envelope_receipts WHERE message_id=?',(env['message_id'],)).fetchone(): con.close(); print('DUPLICATE_ENVELOPE_IGNORED'); return
        # Receiving preserves the sender's immutable message; it does not execute or modify QMS data.
        event(con,'OPERATION_PREFLIGHT',args.receiver,'ENVELOPE_RECEIVE: ALLOWED',status='ALLOWED',data={'operation':'ENVELOPE_RECEIVE','required_permission':'READ','granted_permission':'READ','scope':'inbound envelope','reason':'receipt/acknowledgement only'})
        request_id=None; payload=redact(env['payload'])
        if payload.get('message_type')=='REQUEST':
            request_required={'request_type','scope','why','context','expected_result','constraints','work','dependencies','reporting'}
            missing=request_required-set(payload)
            if missing:
                event(con,'REQUEST_RECEIVE_FAILED',args.receiver,'REQUEST payload validation failed',status='FAILED',data={'message_id':env['message_id'],'missing_fields':sorted(missing)})
                con.close(); raise ValueError('REQUEST payload 필수 필드 누락: '+', '.join(sorted(missing)))
            req={'request_type':payload['request_type'],'from_agent':env['sender'],'to_agent':env['receiver'],'scope':payload['scope'],'lifecycle':'ACKNOWLEDGED','effective_request_status':'ACKNOWLEDGED','message_id':env['message_id'],'why':payload['why'],'context':payload['context'],'expected_result':payload['expected_result'],'constraints':payload['constraints'],'work':payload['work'],'dependencies':payload['dependencies'],'reporting':payload['reporting'],'base_version':payload.get('base_version'),'evidence_refs':payload.get('evidence_refs',[])}
            rec=record(con,'REQUEST',env['sender'],req,knowledge_state='FACT',status='OPEN',risk_level='LOW'); request_id=rec['record_id']
            life=record(con,'STATE',env['receiver'],{'scope_type':'REQUEST','scope_ref':request_id,'summary':'ACKNOWLEDGED','request':request_id,'lifecycle':'ACKNOWLEDGED','effective_request_status':'ACKNOWLEDGED','message_id':env['message_id']},knowledge_state='FACT',status='ACTIVE',risk_level='LOW')
            relation(con,env['receiver'],rec['record_id'],life['record_id'],'result_of')
        receipt={'message_id':env['message_id'],'received_at':now(),'receiver':args.receiver,'status':'RECEIVED','request_record_id':request_id}; receipt['receipt_hash']=sha(receipt)
        con.execute('INSERT INTO envelope_receipts VALUES(?,?,?,?,?,?)',tuple(receipt.values())); con.commit()
        ack={'protocol_name':'CLAUDE_CHATGPT_PROTOCOL','protocol_version':'1.0.0','message_id':'msg-'+uuid.uuid4().hex,'sender':args.receiver,'receiver':env['sender'],'timestamp':now(),'payload':{'message_type':'ACK','acknowledges':env['message_id'],'status':'RECEIVED','request_record_id':request_id}}
        path=OUTBOX/f"{ack['timestamp'].replace(':','')}-{ack['message_id']}.json"; path.write_text(json.dumps(ack,ensure_ascii=False,indent=2)+'\\n',encoding='utf-8')
        event(con,'ENVELOPE_RECEIVED',args.receiver,'Envelope validated and acknowledgement queued',status='RECORDED',data={'message_id':env['message_id'],'request_record_id':request_id,'ack_file':str(path.relative_to(ROOT))}); con.close()
    print('MESSAGE_ID='+env['message_id']+'; ACK_QUEUED')

def cmd_answer_request(args):
    with lock():
        con=connect(); row=con.execute("SELECT payload_json,status FROM protocol_records WHERE record_id=? AND record_type='REQUEST'",(args.request,)).fetchone()
        if not row: raise ValueError('REQUEST Record를 찾지 못했습니다.')
        payload=json.loads(row['payload_json'])
        if payload.get('to_agent')!=args.actor: raise ValueError('REQUEST의 지정 수신자만 응답할 수 있습니다.')
        if row['status']!='OPEN': raise ValueError('OPEN 상태 REQUEST만 진행할 수 있습니다.')
        states=con.execute("SELECT payload_json FROM protocol_records WHERE record_type='STATE' ORDER BY rowid DESC").fetchall()
        prior=next((json.loads(x['payload_json']).get('lifecycle') for x in states if json.loads(x['payload_json']).get('scope_ref')==args.request),None)
        expected={'ACKNOWLEDGED':'IN_PROGRESS','IN_PROGRESS':'RESULT','RESULT':'VERIFIED','VERIFIED':'COMPLETED'}
        if expected.get(prior)!=args.state: raise ValueError(f'REQUEST lifecycle 전이가 올바르지 않습니다: {prior} -> {args.state}')
        if not _preflight_or_block(con,args.actor,'CREATE_PROPOSAL','MISSING','request response'):
            con.close(); print('응답이 권한 경계에서 차단됐습니다.'); return 3
        response=record(con,'CHANGE',args.actor,{'change_type':'REQUEST_RESPONSE','target_record_id':args.request,'reason':args.summary,'basis':args.basis,'verification':args.verification,'previous_value':'OPEN','new_value':'ANSWERED','changed_by':args.actor,'base_version':1},knowledge_state='FACT',status='ACTIVE',risk_level='LOW')
        relation(con,args.actor,args.request,response['record_id'],'result_of')
        lifecycle=record(con,'STATE',args.actor,{'scope_type':'REQUEST','scope_ref':args.request,'summary':args.state,'request':args.request,'lifecycle':args.state,'effective_request_status':args.state,'response_record':response['record_id'],'verification':args.verification},knowledge_state='FACT',status='ACTIVE',risk_level='LOW')
        relation(con,args.actor,response['record_id'],lifecycle['record_id'],'result_of'); event(con,'REQUEST_'+args.state,args.actor,'Request lifecycle state recorded',status='RECORDED',data={'request':args.request,'response':response['record_id'],'state_record':lifecycle['record_id'],'lifecycle':args.state}); con.close()
    print('RESPONSE_RECORD='+response['record_id'])

def cmd_capability_check(args):
    with lock():
        con=connect(); task_exists(con,args.task)
        superseded={json.loads(x['payload_json']).get('scope_ref') for x in con.execute("SELECT payload_json FROM protocol_records WHERE record_type='STATE'") if json.loads(x['payload_json']).get('summary')=='SUPERSEDED'}
        agent=con.execute("SELECT record_id,payload_json FROM protocol_records WHERE record_type='AGENT' AND status='ACTIVE' ORDER BY rowid DESC",()).fetchall()
        configured=next((json.loads(r['payload_json']) for r in agent if r['record_id'] not in superseded and json.loads(r['payload_json']).get('agent_name')==args.agent),None)
        if not configured: raise ValueError('기존 AGENT 설정 Record를 찾지 못했습니다. 모델/역할을 추측하지 않습니다.')
        payload={'review_type':'CAPABILITY_MODEL_FIT','reviewer':args.actor,'review_target':f'TASK:{args.task}','verification':args.result,'agent':args.agent,'existing_configuration_ref':configured,'required_capability':args.required,'result':args.result,'basis':args.basis,'scope':args.scope,'action':'retain existing configuration; no model/role/instruction change'}
        rec=record(con,'REVIEW',args.actor,payload,knowledge_state='FACT' if args.result!='UNKNOWN' else 'UNKNOWN',status='ACTIVE' if args.result=='PASS' else 'NEEDS_VERIFICATION',risk_level='LOW')
        event(con,'CAPABILITY_CHECK',args.actor,'Task-bound capability/model fit recorded',args.task,'RECORDED',data={'record_id':rec['record_id'],'agent':args.agent,'result':args.result}); con.close()
    print('REVIEW_RECORD='+rec['record_id'])

def cmd_current_state(args):
    with lock():
        con=connect(); task_id=args.task
        if task_id is not None: task_exists(con,task_id)
        query="SELECT record_id,payload_json,status,created_at FROM protocol_records WHERE record_type='STATE'"
        state_rows=con.execute(query+' ORDER BY rowid DESC').fetchall()
        ref=str(task_id or PROJECT_ID)
        found=next((r for r in state_rows if json.loads(r['payload_json']).get('scope_ref')==ref),None)
        result={'task_id':task_id,'active_owner':current_owner(con,task_id),'state_record':found['record_id'] if found else None,'state':json.loads(found['payload_json']) if found else 'UNKNOWN','status':found['status'] if found else 'UNKNOWN'}
        con.close()
    print(json.dumps(result,ensure_ascii=False,indent=2))

def cmd_register_adapter(args):
    with lock():
        con=connect()
        if args.source_version=='1.0.0' and args.target_version=='1.0.0': raise ValueError('동일 버전 adapter는 등록하지 않습니다.')
        if not _preflight_or_block(con,args.actor,'CHANGE_RULE',args.approval,args.scope):
            con.close(); print('Adapter 등록이 권한/승인 경계에서 차단됐습니다.'); return 3
        item={'adapter_id':'adapter-'+uuid.uuid4().hex,'source_version':args.source_version,'target_version':args.target_version,'status':'REGISTERED','scope':redact(args.scope),'evidence_ref':redact(args.evidence_ref),'registered_at':now(),'registered_by':args.actor}; item['adapter_hash']=sha(item)
        con.execute('INSERT INTO protocol_adapters VALUES(?,?,?,?,?,?,?,?,?)',tuple(item.values())); con.commit()
        decision=record(con,'DECISION',args.actor,{'decision':'Protocol compatibility adapter registered','decided_by':args.actor,'scope':args.scope,'adapter_id':item['adapter_id'],'source_version':args.source_version,'target_version':args.target_version,'evidence_ref':args.evidence_ref},knowledge_state='CONFIRMED_DECISION',status='ACTIVE',risk_level='HIGH')
        event(con,'ADAPTER_REGISTERED',args.actor,'Versioned compatibility adapter registered',status='RECORDED',data={'adapter_id':item['adapter_id'],'decision_record':decision['record_id']}); con.close()
    print('ADAPTER_ID='+item['adapter_id'])

def cmd_unification_migration(args):
    with lock():
        con=connect()
        key='collab-unification-v1'
        if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): con.close(); print('이미 공식 이관 기록이 있습니다.'); return
        if args.actor!='user' or args.approval!='APPROVED':
            event(con,'UNIFICATION_MIGRATION_BLOCKED',args.actor,'Project archive unification requires explicit user approval',status='BLOCKED',data={'approval':args.approval}); con.close(); return 3
        hook_path=subprocess.run(['git','-C',str(ROOT.parent),'config','--get','core.hooksPath'],text=True,capture_output=True).stdout.strip()
        decision=record(con,'DECISION','user',{'decision':'QMS archive unified under .collab; legacy docs/archive retained as historical material','decided_by':'user','scope':'QMS collaboration archive and hook integration','approval':'APPROVED','hook_install_requested':True,'core_hooks_path':hook_path or 'DEFAULT'},knowledge_state='CONFIRMED_DECISION',status='ACTIVE',risk_level='HIGH')
        change=record(con,'CHANGE','system',{'change_type':'ARCHIVE_UNIFICATION','target_record_id':decision['record_id'],'reason':'user-approved .collab unification and post-commit audit integration','basis':'user instruction: 2번으로 하고 설치도 해','verification':'hook path inspected; installation state recorded separately','previous_value':'docs/archive handoff location','new_value':'.collab/handoffs canonical location; docs/archive legacy retained','changed_by':'system','base_version':1},knowledge_state='FACT',status='ACTIVE',risk_level='HIGH')
        relation(con,'system',decision['record_id'],change['record_id'],'result_of')
        agent_backfills=[]
        for old in con.execute("SELECT record_id,payload_json FROM protocol_records WHERE record_type='AGENT' AND status='ACTIVE' ORDER BY rowid").fetchall():
            op=json.loads(old['payload_json'])
            replacement_payload={**op,'model':op.get('model') or 'UNKNOWN','model_version':op.get('model_version') or 'UNKNOWN','configuration_preserved':True,'backfill_reason':'Protocol v1 canonical agent model/version fields; existing configuration not modified'}
            newer=record(con,'AGENT','system',replacement_payload,knowledge_state='FACT',status='ACTIVE',risk_level='NONE')
            relation(con,'system',old['record_id'],newer['record_id'],'supersedes'); relation(con,'system',newer['record_id'],old['record_id'],'superseded_by'); agent_backfills.append(newer['record_id'])
        con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'decision_record':decision['record_id'],'change_record':change['record_id'],'core_hooks_path':hook_path or 'DEFAULT','legacy_preserved':True}))); con.commit()
        event(con,'ARCHIVE_UNIFICATION_MIGRATED','system','User-approved QMS .collab archive unification recorded',status='CONFIRMED',data={'decision_record':decision['record_id'],'change_record':change['record_id'],'core_hooks_path':hook_path or 'DEFAULT','hook_install_requested':True,'agent_backfill_records':agent_backfills}); con.close()
    print('MIGRATION='+key)

def cmd_sync(args):
    """Validate inbound files only.  Never performs semantic merge; divergence becomes a conflict."""
    files=sorted(INBOX.glob('*.json'))
    processed=0; blocked=0
    for path in files:
        try:
            result=cmd_receive_envelope(argparse.Namespace(file=str(path),receiver=args.receiver))
            if result==3: blocked+=1
            else: processed+=1
        except ValueError:
            blocked+=1
    if not files:
        with lock():
            con=connect(); event(con,'SYNC_CHECK',args.receiver,'No inbound envelopes; local ledger preserved',status='RECORDED',data={'inbox':'empty','network':'not required'}); con.close()
    print(f'SYNC processed={processed} blocked={blocked}; automatic semantic merge is disabled.')



def _read_canonical(folder):
    values=[]; problems=[]
    for path in sorted(folder.glob('*.jsonl')):
        for number,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
            if not line.strip(): continue
            try: item=json.loads(line)
            except json.JSONDecodeError: problems.append(f'Invalid JSONL: {path.name}:{number}'); continue
            if line != canonical(item): problems.append(f'Non-canonical JSONL: {path.name}:{number}')
            values.append(item)
    return values,problems

def validate_protocol_contracts(con, protocol):
    problems=[]
    required={"PROJECT":{"name","purpose"},"AGENT":{"agent_name","role","model","model_version"},"PERMISSION_PROFILE":{"agent_id","operation","permission_level","scope"},"TASK":{"title","owner","scope"},"DECISION":{"decision","decided_by","scope"},"EVIDENCE":{"source_type","content","verification_status"},"AI_INTERPRETATION":{"interpretation","derived_from","scope"},"STATE":{"scope_type","scope_ref","summary"},"REQUEST":{"request_type","from_agent","to_agent","scope"},"REVIEW":{"review_type","reviewer","review_target","verification"},"CHANGE":{"change_type","target_record_id","reason"},"ISSUE":{"title","description","next_action"},"RELATION":{"relation_id","from_record_id","to_record_id","relation_type"}}
    ids={r['record_id'] for r in protocol}
    for r in protocol:
        if r['record_type'] not in RECORD_TYPES: problems.append('Unknown record type: '+r['record_id']); continue
        if r['knowledge_state'] not in KNOWLEDGE or r['risk_level'] not in RISKS: problems.append('Invalid enum: '+r['record_id'])
        missing=required.get(r['record_type'],set())-set(r['payload'])
        if missing:
            successor=con.execute("SELECT 1 FROM protocol_relations WHERE from_record_id=? AND relation_type='supersedes'",(r['record_id'],)).fetchone()
            if not successor: problems.append('Contract fields missing '+r['record_id']+': '+','.join(sorted(missing)))
        if r['record_type']=='AI_INTERPRETATION':
            d=r['payload'].get('derived_from'); source=next((x for x in protocol if x['record_id']==d),None)
            if not source or source['record_type']!='EVIDENCE': problems.append('AI_INTERPRETATION must derive from EVIDENCE: '+r['record_id'])
        if r['record_type']=='REQUEST':
            req={'why','context','expected_result','constraints','work','dependencies','reporting'}
            if not req <= set(r['payload']): problems.append('REQUEST contract missing: '+r['record_id'])
    for rel in _table_rows(con,'protocol_relations'):
        if rel['from_record_id'] not in ids or rel['to_record_id'] not in ids or rel['relation_type'] not in RELATIONS: problems.append('Invalid relation reference: '+rel['relation_id'])
    relation_envelopes={r['payload'].get('relation_id') for r in protocol if r['record_type']=='RELATION'}
    for rel in _table_rows(con,'protocol_relations'):
        if rel['relation_id'] not in relation_envelopes: problems.append('Relation lacks RELATION Record: '+rel['relation_id'])
    for r in protocol:
        if r['record_type']=='CHANGE':
            target=r['payload'].get('target_record_id','')
            if target not in ids and not target.startswith('migration:'): problems.append('CHANGE target missing: '+r['record_id'])
        if r['record_type']=='EVIDENCE' and r['payload'].get('verification_status') not in {'UNVERIFIED','VERIFIED','PARTIAL','NEEDS_VERIFICATION'}: problems.append('Invalid Evidence verification status: '+r['record_id'])
    for receipt in _table_rows(con,'envelope_receipts'):
        request=receipt.get('request_record_id')
        if request and request not in ids: problems.append('Invalid request receipt: '+receipt['message_id'])
    for r in protocol:
        if r['record_type']=='REQUEST':
            ls=[json.loads(x['payload_json']).get('lifecycle') for x in con.execute("SELECT payload_json FROM protocol_records WHERE record_type='STATE' ORDER BY rowid") if json.loads(x['payload_json']).get('scope_ref')==r['record_id']]
            allowed=['ACKNOWLEDGED','IN_PROGRESS','RESULT','VERIFIED','COMPLETED']
            if not ls or ls!=allowed[:len(ls)] or effective_request_status(con,r['record_id'])!=ls[-1]: problems.append('Invalid REQUEST lifecycle/effective status: '+r['record_id'])
    return problems

def cmd_verify_v2(args):
    with lock():
        con=connect(); events=rows(con); problems=[]; previous=None
        for e in events:
            raw={k:e[k] for k in ('event_id','occurred_at','event_type','actor','task_id','status','summary','data','prev_hash')}
            if e['prev_hash']!=previous: problems.append(f"DB chain mismatch: {e['event_id']}")
            if e['event_hash']!=sha(raw): problems.append(f"DB hash mismatch: {e['event_id']}")
            previous=e['event_hash']
        disk,p=_read_canonical(EVENTS); problems+=p
        if canonical(disk)!=canonical(events): problems.append('JSONL mirror differs from SQLite ledger')
        protocol=protocol_rows(con); problems += validate_protocol_contracts(con,protocol); disk,p=_read_canonical(RECORDS); problems+=p
        if canonical(disk)!=canonical(protocol): problems.append('Protocol record JSONL differs from SQLite ledger')
        for item in protocol:
            if item['record_hash']!=sha({k:item[k] for k in item if k!='record_hash'}): problems.append(f"Protocol record hash mismatch: {item['record_id']}")
        for table, folder, key in (('protocol_relations',RELATIONS_DIR,'relation_hash'),('protocol_conflicts',CONFLICTS_DIR,None)):
            db=_table_rows(con,table); disk,p=_read_canonical(folder); problems+=p
            if canonical(db)!=canonical(disk): problems.append(f'{table} JSONL differs from SQLite ledger')
            if key: problems += _verify_append_rows(db,table,key)
        for table,key in (('handoff_acceptances','acceptance_hash'),('envelope_receipts','receipt_hash'),('protocol_adapters','adapter_hash')):
            problems += _verify_append_rows(_table_rows(con,table),table,key)
        for run in _table_rows(con, 'workflow_runs'):
            if run['status'] != 'ACTIVE' or not re.fullmatch(r'run-[0-9a-f]{32}', run['run_id']) or not re.fullmatch(r'[0-9a-f]{64}', run['token_hash']):
                problems.append('Invalid workflow run: ' + run['run_id'])
            try:
                task_record = workflow_task_record(con, run['task_id'])
                payload = json.loads(con.execute('SELECT payload_json FROM protocol_records WHERE record_id=?', (task_record,)).fetchone()[0])
                if payload.get('workflow_run_id') != run['run_id']: problems.append('Workflow TASK/RUN mismatch: ' + run['run_id'])
            except ValueError:
                problems.append('Workflow RUN has no TASK record: ' + run['run_id'])
        con.close()
    if problems: print('VERIFY FAILED\\n'+'\\n'.join(problems)); return 1
    print(f'VERIFY OK: {len(events)} audit events and {len(protocol)} protocol records; canonical JSONL mirrors match.'); return 0

def cmd_repair_v2(args):
    with lock():
        con=connect()
        # Rebuild only after full DB integrity validation. This is a representation repair, never a semantic merge.
        events=rows(con); prev=None
        for e in events:
            raw={k:e[k] for k in ('event_id','occurred_at','event_type','actor','task_id','status','summary','data','prev_hash')}
            if e['prev_hash']!=prev or e['event_hash']!=sha(raw): raise RuntimeError('SQLite hash chain is invalid; mirror repair stopped.')
            prev=e['event_hash']
        protocol=protocol_rows(con)
        for item in protocol:
            if item['record_hash']!=sha({k:item[k] for k in item if k!='record_hash'}): raise RuntimeError('Protocol record hash invalid; mirror repair stopped.')
        _protocol_jsonl(EVENTS,events); _protocol_jsonl(RECORDS,protocol); _protocol_jsonl(RELATIONS_DIR,_table_rows(con,'protocol_relations')); _protocol_jsonl(CONFLICTS_DIR,_table_rows(con,'protocol_conflicts'))
        con.close()
    print(f'Canonical JSONL mirrors repaired from SQLite: {len(events)} events, {len(protocol)} records, relations and conflicts.')



def cmd_backfill_schema(args):
    """Append canonical replacements for legacy incomplete records; originals remain immutable."""
    with lock():
        con=connect(); key='protocol-schema-backfill-v1'
        if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): con.close(); print('이미 schema backfill이 완료됐습니다.'); return
        if args.actor!='user' or args.approval!='APPROVED': raise ValueError('Schema backfill은 명시적 사용자 승인으로만 실행합니다.')
        made=[]
        for old in con.execute("SELECT record_id,record_type,payload_json FROM protocol_records WHERE record_type IN ('AGENT','STATE') ORDER BY rowid").fetchall():
            payload=json.loads(old['payload_json']); typ=old['record_type']
            if typ=='AGENT':
                required={'agent_name','role','model','model_version'}; normalized={**payload,'model':payload.get('model','UNKNOWN'),'model_version':payload.get('model_version','UNKNOWN'),'configuration_preserved':True}
            else:
                required={'scope_type','scope_ref','summary'}; normalized={**payload,'scope_type':payload.get('scope_type','PROJECT'),'scope_ref':payload.get('scope_ref',PROJECT_ID),'summary':payload.get('summary',payload.get('resource_state','UNKNOWN')),'schema_backfill_reason':'legacy STATE normalized without changing original'}
            if required <= set(payload): continue
            newer=record(con,typ,'system',normalized,knowledge_state='FACT',status='ACTIVE',risk_level='NONE')
            relation(con,'system',old['record_id'],newer['record_id'],'supersedes'); relation(con,'system',newer['record_id'],old['record_id'],'superseded_by'); made.append(newer['record_id'])
        con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'records':made,'originals_preserved':True}))); con.commit()
        event(con,'SCHEMA_BACKFILL','system','Legacy incomplete records received append-only canonical successors',status='RECORDED',data={'records':made}); con.close()
    print('BACKFILL_RECORDS='+str(len(made)))



def cmd_record_hook_installation(args):
    with lock():
        con=connect(); key='qms-hook-install-v1'
        if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): con.close(); print('이미 hook 설치 기록이 있습니다.'); return
        if args.actor!='user' or args.approval!='APPROVED': raise ValueError('Hook 설치 기록은 사용자 승인으로만 확정합니다.')
        hook_path=subprocess.run(['git','-C',str(ROOT.parent),'config','--get','core.hooksPath'],text=True,capture_output=True).stdout.strip() or 'DEFAULT'
        pre=ROOT.parent/'.git'/'hooks'/'pre-commit'; post=ROOT.parent/'.git'/'hooks'/'post-commit'
        if hook_path!='DEFAULT' or not pre.is_file() or not post.is_file(): raise RuntimeError('현재 hook 경로/파일이 설치 기준을 충족하지 않아 설치 완료 기록을 차단했습니다.')
        change=record(con,'CHANGE','system',{'change_type':'GIT_HOOK_INSTALL','target_record_id':'migration:collab-unification-v1','reason':'user-approved preflight enforcement and post-commit observation hooks installed','basis':'user instruction: 2번으로 하고 설치도 해','verification':'core.hooksPath DEFAULT; pre-commit and post-commit files present','previous_value':'no verified pre-commit enforcement','new_value':'pre-commit preflight enforcement + post-commit observation','changed_by':'system','base_version':1},knowledge_state='FACT',status='ACTIVE',risk_level='HIGH')
        con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'change_record':change['record_id'],'core_hooks_path':hook_path,'pre_commit':True,'post_commit':True}))); con.commit()
        event(con,'GIT_HOOK_INSTALLED','system','Pre-commit enforcement and post-commit observation installed',status='CONFIRMED',data={'change_record':change['record_id'],'core_hooks_path':hook_path}); con.close()
    print('HOOK_INSTALL_RECORD='+change['record_id'])



def cmd_backfill_relation_envelopes(args):
    with lock():
        con=connect(); key='relation-envelope-backfill-v1'
        if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): con.close(); print('이미 Relation envelope backfill이 완료됐습니다.'); return
        if args.actor!='user' or args.approval!='APPROVED': raise ValueError('Relation backfill은 사용자 승인으로만 실행합니다.')
        existing={json.loads(r['payload_json']).get('relation_id') for r in con.execute("SELECT payload_json FROM protocol_records WHERE record_type='RELATION'")}
        made=[]
        for rel in _table_rows(con,'protocol_relations'):
            if rel['relation_id'] in existing: continue
            rec=record(con,'RELATION','system',{'relation_id':rel['relation_id'],'from_record_id':rel['from_record_id'],'to_record_id':rel['to_record_id'],'relation_type':rel['relation_type'],'backfill_reason':'legacy edge representation'},knowledge_state='FACT',status='ACTIVE',risk_level='NONE'); made.append(rec['record_id'])
        con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'records':made,'edges_preserved':True}))); con.commit(); event(con,'RELATION_ENVELOPE_BACKFILL','system','Legacy relations received RELATION Records',status='RECORDED',data={'records':made}); con.close()
    print('RELATION_BACKFILL_RECORDS='+str(len(made)))

def cmd_backfill_agent_supersession(args):
    with lock():
        con=connect(); key='agent-supersession-state-v1'
        if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): con.close(); print('이미 Agent supersession 상태 기록이 있습니다.'); return
        if args.actor!='user' or args.approval!='APPROVED': raise ValueError('Agent supersession backfill은 사용자 승인으로만 실행합니다.')
        made=[]
        for rel in _table_rows(con,'protocol_relations'):
            if rel['relation_type']!='supersedes': continue
            old=con.execute("SELECT record_type FROM protocol_records WHERE record_id=?",(rel['from_record_id'],)).fetchone()
            if not old or old['record_type']!='AGENT': continue
            rec=record(con,'STATE','system',{'scope_type':'AGENT','scope_ref':rel['from_record_id'],'summary':'SUPERSEDED','superseded_by':rel['to_record_id'],'reason':'append-only active selection state'},knowledge_state='FACT',status='ACTIVE',risk_level='NONE'); relation(con,'system',rel['from_record_id'],rec['record_id'],'result_of'); made.append(rec['record_id'])
        con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'state_records':made,'originals_unchanged':True}))); con.commit(); event(con,'AGENT_SUPERSESSION_BACKFILL','system','Legacy Agent records marked SUPERSEDED by state representation',status='RECORDED',data={'records':made}); con.close()
    print('AGENT_SUPERSESSION_STATES='+str(len(made)))



def cmd_ingest_postcommit_failures(args):
    folder=OUTBOX/'postcommit-failures'
    with lock():
        con=connect(); count=0
        for path in sorted(folder.glob('*.json')) if folder.exists() else []:
            item=parse_json_object(path.read_text(encoding='utf-8'),'failure marker')
            key='postcommit-failure:'+path.name
            if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): continue
            event(con,'GIT_COMMIT_AUDIT_FAILED','system','Post-commit observation failed; durable marker ingested',status='FAILED',data={'commit':item.get('commit','UNKNOWN'),'marker':str(path.relative_to(ROOT)),'reason':item.get('reason','UNKNOWN')})
            con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'marker':str(path.relative_to(ROOT)),'preserved':True}))); con.commit(); count+=1
        con.close()
    print('POSTCOMMIT_FAILURE_MARKERS_INGESTED='+str(count))



def effective_request_status(con, request_id):
    states=[json.loads(r['payload_json']) for r in con.execute("SELECT payload_json FROM protocol_records WHERE record_type='STATE' ORDER BY rowid")]
    matched=[x.get('effective_request_status') for x in states if x.get('scope_ref')==request_id and x.get('effective_request_status')]
    return matched[-1] if matched else 'UNKNOWN'

def cmd_backfill_safe_permissions(args):
    with lock():
        con=connect(); key='safe-request-permissions-v1'
        if con.execute('SELECT 1 FROM protocol_migrations WHERE migration_key=?',(key,)).fetchone(): con.close(); print('이미 안전 REQUEST 권한 기록이 있습니다.'); return
        if args.actor!='user' or args.approval!='APPROVED': raise ValueError('권한 backfill은 사용자 승인으로만 실행합니다.')
        records=[]
        for agent in ('claude','codex'):
            rec=record(con,'PERMISSION_PROFILE','user',{'agent_id':agent,'operation':'CREATE_PROPOSAL','permission_level':'PROPOSE','approval_required':False,'scope':'.collab REQUEST/REVIEW only','conditions':['safe record lifecycle only','does not permit QMS file/data change, commit, deployment']},knowledge_state='CONFIRMED_DECISION',status='ACTIVE',risk_level='LOW'); records.append(rec['record_id'])
        con.execute('INSERT INTO protocol_migrations VALUES(?,?,?)',(key,now(),canonical({'records':records,'P1_P3_preserved':True})));con.commit();event(con,'SAFE_REQUEST_PERMISSIONS_BACKFILLED','user','Claude/Codex safe REQUEST lifecycle permission recorded; QMS write gates unchanged',status='CONFIRMED',data={'records':records});con.close()
    print('SAFE_PERMISSION_RECORDS='+str(len(records)))


def parser():
    p=argparse.ArgumentParser(); sp=p.add_subparsers(dest="action",required=True)
    x=sp.add_parser("init"); x.add_argument("--actor",default="system"); x.set_defaults(fn=cmd_init)
    x=sp.add_parser("start"); x.add_argument("--title",required=True); x.add_argument("--actor",required=True); x.add_argument("--scope",default=""); x.add_argument("--baseline",default=""); x.add_argument("--summary",default=""); x.set_defaults(fn=cmd_start)
    for name,typ,final in (("note","NOTE",None),("decision","DECISION",None),("end","TASK_ENDED","COMPLETED")):
        x=sp.add_parser(name); x.add_argument("--task",type=int,required=True); x.add_argument("--actor",required=True); x.add_argument("--summary",required=True); x.add_argument("--basis",default=""); x.add_argument("--unknown",default=""); x.add_argument("--status",default="RECORDED");
        if name=="decision": x.add_argument("--state",required=True)
        x.set_defaults(fn=lambda a,t=typ,f=final: cmd_simple(a,t,f))
    x=sp.add_parser("run"); x.add_argument("--task",type=int,required=True); x.add_argument("--actor",required=True); x.add_argument("--summary",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.add_argument("--run"); x.add_argument("--capability-token",default=os.environ.get("QMS_WORKFLOW_CAPABILITY_TOKEN")); x.add_argument("command",nargs=argparse.REMAINDER); x.set_defaults(fn=cmd_run)
    x=sp.add_parser("workflow-authorize"); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--scope",required=True); x.add_argument("--user-request",required=True); x.set_defaults(fn=cmd_workflow_authorize)
    x=sp.add_parser("workflow-start"); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--authorization-record",required=True); x.add_argument("--title",required=True); x.add_argument("--scope",required=True); x.add_argument("--user-request",required=True); x.add_argument("--baseline",default=""); x.add_argument("--required-roles",default="planner,developer,quality-watcher"); x.add_argument("--visual-change",action="store_true"); x.add_argument("--e2e-required",action="store_true"); x.set_defaults(fn=cmd_workflow_start)
    x=sp.add_parser("workflow-dispatch"); x.add_argument("--task",type=int,required=True); x.add_argument("--run",required=True); x.add_argument("--capability-token",default=os.environ.get("QMS_WORKFLOW_CAPABILITY_TOKEN")); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--role",required=True,choices=sorted(WORKFLOW_ROLES)); x.add_argument("--scope",required=True); x.add_argument("--why",required=True); x.add_argument("--context",default=""); x.add_argument("--constraints",default=""); x.add_argument("--work",required=True); x.add_argument("--dependencies",default=""); x.set_defaults(fn=cmd_workflow_dispatch)
    x=sp.add_parser("workflow-result"); x.add_argument("--task",type=int,required=True); x.add_argument("--run",required=True); x.add_argument("--capability-token",default=os.environ.get("QMS_WORKFLOW_CAPABILITY_TOKEN")); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--role",required=True,choices=sorted(WORKFLOW_ROLES)); x.add_argument("--request",required=True); x.add_argument("--result",required=True); x.set_defaults(fn=cmd_workflow_result)
    x=sp.add_parser("workflow-finalize"); x.add_argument("--task",type=int,required=True); x.add_argument("--run",required=True); x.add_argument("--capability-token",default=os.environ.get("QMS_WORKFLOW_CAPABILITY_TOKEN")); x.add_argument("--actor",required=True,choices=("claude","codex")); x.set_defaults(fn=cmd_workflow_finalize)
    x=sp.add_parser("workflow-issue-resolution"); x.add_argument("--task",type=int,required=True); x.add_argument("--run",required=True); x.add_argument("--capability-token",default=os.environ.get("QMS_WORKFLOW_CAPABILITY_TOKEN")); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--issue",required=True); x.add_argument("--resolution",required=True,choices=("RESOLVED","SUPERSEDED")); x.add_argument("--basis",required=True); x.add_argument("--evidence-record",required=True); x.set_defaults(fn=cmd_workflow_issue_resolution)
    x=sp.add_parser("backfill-workflow-permission"); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_backfill_workflow_permission)
    x=sp.add_parser("backfill-workflow-test-permission"); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_backfill_workflow_test_permission)
    x=sp.add_parser("status"); x.add_argument("--limit",type=int,default=20); x.set_defaults(fn=cmd_status)
    x=sp.add_parser("verify"); x.set_defaults(fn=cmd_verify_v2)
    x=sp.add_parser("repair-mirror"); x.set_defaults(fn=cmd_repair_v2)
    x=sp.add_parser("staged-manifest"); x.set_defaults(fn=cmd_staged_manifest)
    x=sp.add_parser("git-commit-authorize"); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--scope",required=True); x.add_argument("--user-request",required=True); x.set_defaults(fn=cmd_git_commit_authorize)
    x=sp.add_parser("record-git-commit"); x.add_argument("--actor",default=os.environ.get("QMS_AUDIT_ACTOR","user")); x.add_argument("--commit"); x.add_argument("--approval-record"); x.set_defaults(fn=cmd_git_commit)
    x=sp.add_parser("deploy-authorize"); x.add_argument("--actor",required=True,choices=("claude","codex")); x.add_argument("--scope",required=True); x.add_argument("--user-request",required=True); x.add_argument("--remote",required=True); x.set_defaults(fn=cmd_deploy_authorize)
    x=sp.add_parser("bootstrap-protocol"); x.set_defaults(fn=cmd_bootstrap)
    x=sp.add_parser("migrate-legacy"); x.set_defaults(fn=cmd_migrate_legacy)
    x=sp.add_parser("record"); x.add_argument("--record-type",required=True,choices=sorted(RECORD_TYPES)); x.add_argument("--actor",required=True); x.add_argument("--payload",required=True); x.add_argument("--knowledge-state",default="FACT",choices=sorted(KNOWLEDGE)); x.add_argument("--status",default="ACTIVE"); x.add_argument("--risk-level",choices=sorted(RISKS)); x.add_argument("--scope",default=""); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_record)
    x=sp.add_parser("relate"); x.add_argument("--actor",required=True); x.add_argument("--from-record",required=True); x.add_argument("--to-record",required=True); x.add_argument("--relation-type",required=True,choices=sorted(RELATIONS)); x.set_defaults(fn=cmd_relation)
    x=sp.add_parser("preflight"); x.add_argument("--actor",required=True); x.add_argument("--operation",required=True,choices=sorted(OP_REGISTRY)); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.add_argument("--approval-record"); x.add_argument("--staged-manifest"); x.add_argument("--deploy-commit"); x.add_argument("--remote"); x.add_argument("--scope",default=""); x.add_argument("--task",type=int); x.set_defaults(fn=cmd_preflight)
    x=sp.add_parser("handoff"); x.add_argument("--task",type=int); x.add_argument("--actor",required=True); x.add_argument("--new-owner",required=True); x.add_argument("--payload",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.add_argument("--emergency",action="store_true"); x.set_defaults(fn=cmd_handoff)
    x=sp.add_parser("accept-handoff"); x.add_argument("--handoff",required=True); x.add_argument("--actor",required=True); x.add_argument("--basis",required=True); x.add_argument("--verification",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_accept_handoff_v2)
    x=sp.add_parser("search"); x.add_argument("--query",required=True); x.add_argument("--limit",type=int,default=20); x.set_defaults(fn=cmd_search)
    x=sp.add_parser("conflict"); x.add_argument("--actor",required=True); x.add_argument("--target-record",required=True); x.add_argument("--base-version",type=int,required=True); x.add_argument("--reason",required=True); x.set_defaults(fn=cmd_conflict)
    x=sp.add_parser("envelope"); x.add_argument("--sender",required=True); x.add_argument("--receiver",required=True); x.add_argument("--payload",required=True); x.set_defaults(fn=cmd_envelope)
    x=sp.add_parser("receive-envelope"); x.add_argument("--file",required=True); x.add_argument("--receiver",required=True); x.set_defaults(fn=cmd_receive_envelope)
    x=sp.add_parser("answer-request"); x.add_argument("--request",required=True); x.add_argument("--actor",required=True); x.add_argument("--summary",required=True); x.add_argument("--basis",required=True); x.add_argument("--verification",required=True); x.add_argument("--state",required=True,choices=("IN_PROGRESS","RESULT","VERIFIED","COMPLETED")); x.set_defaults(fn=cmd_answer_request)
    x=sp.add_parser("capability-check"); x.add_argument("--task",type=int,required=True); x.add_argument("--actor",required=True); x.add_argument("--agent",required=True); x.add_argument("--required",required=True); x.add_argument("--result",required=True,choices=("PASS","INSUFFICIENT","UNKNOWN")); x.add_argument("--basis",required=True); x.add_argument("--scope",default=""); x.set_defaults(fn=cmd_capability_check)
    x=sp.add_parser("current-state"); x.add_argument("--task",type=int); x.set_defaults(fn=cmd_current_state)
    x=sp.add_parser("register-adapter"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.add_argument("--source-version",required=True); x.add_argument("--target-version",required=True); x.add_argument("--scope",required=True); x.add_argument("--evidence-ref",required=True); x.set_defaults(fn=cmd_register_adapter)
    x=sp.add_parser("record-unification-migration"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_unification_migration)
    x=sp.add_parser("backfill-schema"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_backfill_schema)
    x=sp.add_parser("record-hook-installation"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_record_hook_installation)
    x=sp.add_parser("backfill-relation-envelopes"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_backfill_relation_envelopes)
    x=sp.add_parser("backfill-agent-supersession"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_backfill_agent_supersession)
    x=sp.add_parser("ingest-postcommit-failures"); x.set_defaults(fn=cmd_ingest_postcommit_failures)
    x=sp.add_parser("backfill-safe-request-permissions"); x.add_argument("--actor",required=True); x.add_argument("--approval",default="MISSING",choices=("MISSING","REQUESTED","APPROVED","REJECTED")); x.set_defaults(fn=cmd_backfill_safe_permissions)
    x=sp.add_parser("sync"); x.add_argument("--receiver",required=True); x.set_defaults(fn=cmd_sync)
    x=sp.add_parser("resource-check"); x.add_argument("--actor",required=True); x.add_argument("--scope",required=True); x.add_argument("--state",required=True,choices=("SUFFICIENT","LOW_RESOURCE","BLOCKED")); x.add_argument("--safe-stop",required=True); x.add_argument("--next-action",required=True); x.set_defaults(fn=cmd_resource_check)
    x=sp.add_parser("report"); x.add_argument("--limit",type=int,default=50); x.set_defaults(fn=cmd_report)
    return p

def main():
    # 2026-09-21 실제 사고: 이 CLI의 출력에 em dash(—) 등 cp949로 못 옮기는 문자가 하나만
    # 있어도(예: start/note/decision --title·--summary에 흔히 들어감) Windows 콘솔에서
    # UnicodeEncodeError로 명령 자체가 죽어서(status/search 등 조회 명령까지) 기록을
    # 남기지도 확인하지도 못하는 상태가 됐었다.
    # 처음엔 encoding="utf-8"로 강제했는데, 이러면 이 CLI를 subprocess.run(text=True)로
    # (encoding 지정 없이) 호출하는 쪽(.collab/tests/test_qms_audit.py 포함, 부모가 로케일
    # 기본 인코딩=cp949로 디코딩)과 인코딩이 어긋나서 부모 쪽 리더 스레드가
    # UnicodeDecodeError를 내고 stdout이 통째로 None이 되는 회귀가 났다(테스트 7건 실패로
    # 실제 확인함). 그래서 인코딩 자체는 그대로 두고 errors="replace"만 켠다 — 이러면
    # 어느 쪽 인코딩을 쓰든(직접 실행/subprocess 양쪽) 못 옮기는 문자만 "?"로 바뀌고
    # 크래시는 안 난다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        args=parser().parse_args(); code=args.fn(args); return code or 0
    except (ValueError, RuntimeError, sqlite3.Error) as exc: print(f"[qms-audit error] {exc}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
