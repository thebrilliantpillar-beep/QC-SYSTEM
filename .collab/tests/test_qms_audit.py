import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "qms_audit.py"
HOOKS = Path(__file__).resolve().parents[1] / "hooks"

class AuditCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy2(SOURCE, self.root / "qms_audit.py")
        (self.root / "events").mkdir()
        (self.root / "runtime").mkdir()
    def tearDown(self): self.tmp.cleanup()
    def invoke(self, *args, input_text=None):
        return subprocess.run([sys.executable, "qms_audit.py", *args], cwd=self.root, text=True, capture_output=True, input=input_text)

    def test_append_only_chain_and_mirror_repair(self):
        self.assertEqual(self.invoke("init").returncode, 0)
        start=self.invoke("start", "--title", "Test", "--actor", "user", "--scope", "x", "--summary", "begin")
        self.assertEqual(start.returncode, 0)
        task=int(start.stdout.strip().split("=")[1])
        self.assertEqual(self.invoke("note", "--task", str(task), "--actor", "user", "--summary", "password=abc", "--status", "PARTIAL").returncode, 0)
        self.assertEqual(self.invoke("verify").returncode, 0)
        payload=(self.root / "events").glob("*.jsonl")
        rows=[json.loads(x) for p in payload for x in p.read_text(encoding="utf-8").splitlines()]
        self.assertIn("[REDACTED]", rows[-1]["summary"])
        # Task title and all common secret/header spellings must be redacted in SQLite and JSONL.
        secret_title = self.invoke("start", "--title", "password is swordfish; api key: abc; Bearer xyz; Authorization: Bearer authbearer", "--actor", "user")
        self.assertEqual(secret_title.returncode, 0)
        secret_task = int(secret_title.stdout.strip().split("=")[1])
        self.assertEqual(self.invoke("note", "--task", str(secret_task), "--actor", "user", "--summary", "Authorization: Bearer authbearer").returncode, 0)
        db = sqlite3.connect(self.root / "runtime" / "audit.sqlite3")
        title = db.execute("SELECT title FROM tasks ORDER BY id DESC LIMIT 1").fetchone()[0]
        db.close()
        self.assertNotIn("swordfish", title); self.assertNotIn("abc", title); self.assertNotIn("xyz", title); self.assertNotIn("authbearer", title)
        self.assertIn("[REDACTED]", title)
        self.assertIn("Authorization: Bearer [REDACTED]", title)
        # The raw Authorization bearer value must not survive in either ledger.
        db = sqlite3.connect(self.root / "runtime" / "audit.sqlite3")
        database_text = "\n".join(str(value) for row in db.execute("SELECT title, scope, baseline, summary, data_json FROM tasks LEFT JOIN audit_events ON audit_events.task_id = tasks.id") for value in row if value is not None)
        db.close()
        jsonl_text = "\n".join(p.read_text(encoding="utf-8") for p in (self.root / "events").glob("*.jsonl"))
        self.assertNotIn("authbearer", database_text)
        self.assertNotIn("authbearer", jsonl_text)
        self.assertIn("Authorization: Bearer [REDACTED]", database_text)
        self.assertIn("Authorization: Bearer [REDACTED]", jsonl_text)
        # Tampered mirror must fail; repair may only rebuild from the DB chain.
        p=next((self.root / "events").glob("*.jsonl")); p.write_text("{}\n",encoding="utf-8")
        self.assertNotEqual(self.invoke("verify").returncode, 0)
        self.assertEqual(self.invoke("repair-mirror").returncode, 0)
        self.assertEqual(self.invoke("verify").returncode, 0)
    def test_run_captures_exit_code_without_command_text(self):
        self.invoke("init"); task=int(self.invoke("start", "--title", "T", "--actor", "user").stdout.strip().split("=")[1])
        result=self.invoke("run", "--task", str(task), "--actor", "user", "--summary", "intentional test", "--", sys.executable, "-c", "raise SystemExit(3)")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.invoke("verify").returncode, 0)

    def test_pending_mirror_retries_on_next_invocation(self):
        self.invoke("init")
        task = int(self.invoke("start", "--title", "T", "--actor", "user").stdout.strip().split("=")[1])
        mirror = next((self.root / "events").glob("*.jsonl"))
        lines = mirror.read_text(encoding="utf-8").splitlines()
        mirror.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        # `status` opens the ledger; a correct prefix is automatically completed.
        self.assertEqual(self.invoke("status").returncode, 0)
        self.assertEqual(self.invoke("verify").returncode, 0)

    def test_status_uses_actual_task_ended_status(self):
        self.invoke("init")
        partial = int(self.invoke("start", "--title", "CSRF historical", "--actor", "codex").stdout.strip().split("=")[1])
        self.assertEqual(self.invoke("end", "--task", str(partial), "--actor", "codex", "--summary", "partial evidence", "--status", "PARTIAL").returncode, 0)
        completed = int(self.invoke("start", "--title", "Normal task", "--actor", "user").stdout.strip().split("=")[1])
        self.assertEqual(self.invoke("end", "--task", str(completed), "--actor", "user", "--summary", "done", "--status", "COMPLETED").returncode, 0)
        listing = self.invoke("status", "--limit", "10")
        self.assertEqual(listing.returncode, 0)
        self.assertIn(f"#{partial} [PARTIAL]", listing.stdout)
        self.assertIn(f"#{completed} [COMPLETED]", listing.stdout)

    def test_hook_installer_creates_once_and_preserves_existing_hook(self):
        if shutil.which("powershell") is None:
            self.skipTest("PowerShell unavailable")
        git = shutil.which("git")
        if git is None: self.skipTest("git unavailable")
        subprocess.run([git, "init", "-q"], cwd=self.root, check=True)
        hook_dir = self.root / ".collab" / "hooks"; hook_dir.mkdir(parents=True)
        shutil.copy2(HOOKS / "post-commit", hook_dir / "post-commit")
        shutil.copy2(HOOKS / "pre-commit", hook_dir / "pre-commit")
        shutil.copy2(HOOKS / "pre-push", hook_dir / "pre-push")
        shutil.copy2(HOOKS / "install-post-commit-hook.ps1", hook_dir / "install-post-commit-hook.ps1")
        installer = hook_dir / "install-post-commit-hook.ps1"
        first = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)], cwd=self.root, capture_output=True)
        self.assertEqual(first.returncode, 0, first.stderr.decode(errors="replace"))
        installed = self.root / ".git" / "hooks" / "post-commit"
        self.assertEqual(installed.read_text(encoding="utf-8"), (hook_dir / "post-commit").read_text(encoding="utf-8"))
        installed.write_text("original hook", encoding="utf-8")
        second = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)], cwd=self.root, capture_output=True)
        self.assertEqual(second.returncode, 0, second.stderr.decode(errors="replace"))
        backup = self.root / ".git" / "hooks" / "post-commit.qms-original"
        self.assertEqual(backup.read_text(encoding="utf-8"), "original hook")
        self.assertIn("QMS collaboration audit hook", installed.read_text(encoding="utf-8"))

    def test_protocol_records_preflight_and_handoff(self):
        self.assertEqual(self.invoke("bootstrap-protocol").returncode, 0)
        self.assertEqual(self.invoke("migrate-legacy").returncode, 0)
        denied = self.invoke("preflight", "--actor", "codex", "--operation", "DEPLOY")
        self.assertEqual(denied.returncode, 3)
        allowed = self.invoke("preflight", "--actor", "user", "--operation", "DEPLOY", "--approval", "APPROVED")
        self.assertEqual(allowed.returncode, 0)
        handoff = self.invoke("handoff", "--actor", "user", "--new-owner", "codex", "--approval", "APPROVED", "--payload", json.dumps({
            "reason":"test", "current_state":"state", "completed_work":"done", "unresolved":"none", "risks":"low", "important_evidence":"none", "decisions":"none", "next_action":"verify", "verification":"partial", "context_hint":"read evidence"
        }))
        self.assertEqual(handoff.returncode, 0, handoff.stderr)
        handoff_id = handoff.stdout.strip().split("=")[1]
        accepted = self.invoke("accept-handoff", "--handoff", handoff_id, "--actor", "codex", "--basis", "test", "--verification", "unverified")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.invoke("verify").returncode, 0)


    def test_relations_conflicts_and_canonical_repair(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode,0)
        one=self.invoke('record','--record-type','EVIDENCE','--actor','user','--payload',json.dumps({'source_type':'test','content':'one','verification_status':'VERIFIED'}))
        two=self.invoke('record','--record-type','EVIDENCE','--actor','user','--payload',json.dumps({'source_type':'test','content':'two','verification_status':'VERIFIED'}))
        a=one.stdout.strip().split('=')[1]; b=two.stdout.strip().split('=')[1]
        self.assertEqual(self.invoke('relate','--actor','user','--from-record',a,'--to-record',b,'--relation-type','supports').returncode,0)
        self.assertEqual(self.invoke('conflict','--actor','user','--target-record',a,'--base-version','99','--reason','incoming divergence').returncode,0)
        path=next((self.root/'relations').glob('*.jsonl')); path.write_text('{"bad": true}\n',encoding='utf-8')
        self.assertNotEqual(self.invoke('verify').returncode,0)
        self.assertEqual(self.invoke('repair-mirror').returncode,0)
        self.assertEqual(self.invoke('verify').returncode,0)

    def test_envelope_and_designated_owner(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode,0)
        task=int(self.invoke('start','--title','T','--actor','user').stdout.strip().split('=')[1])
        handoff=self.invoke('handoff','--task',str(task),'--actor','user','--new-owner','codex','--approval','APPROVED','--payload',json.dumps({'reason':'test','current_state':'state','completed_work':'done','unresolved':'none','risks':'low','important_evidence':'none','decisions':'none','next_action':'verify','verification':'partial','context_hint':'read'}))
        hid=handoff.stdout.strip().split('=')[1]
        self.assertNotEqual(self.invoke('accept-handoff','--handoff',hid,'--actor','claude','--basis','wrong','--verification','none').returncode,0)
        self.assertEqual(self.invoke('accept-handoff','--handoff',hid,'--actor','codex','--basis','accepted','--verification','checked').returncode,0)
        state=self.invoke('current-state','--task',str(task)); self.assertIn('codex',state.stdout)
        env={'protocol_name':'CLAUDE_CHATGPT_PROTOCOL','protocol_version':'1.0.0','message_id':'msg-test','sender':'claude','receiver':'codex','timestamp':'2026-09-20T00:00:00Z','payload':{'message_type':'REQUEST','request_type':'REVIEW','scope':'app.py','why':'quality','context':'test','expected_result':'review result','constraints':'none','work':'review','dependencies':'none','reporting':'record'}}
        src=self.root/'inbound.json';src.write_text(json.dumps(env),encoding='utf-8')
        received=self.invoke('receive-envelope','--file',str(src),'--receiver','codex');self.assertEqual(received.returncode,0,received.stderr)
        self.assertIn('ACK_QUEUED',received.stdout)
        self.assertEqual(self.invoke('verify').returncode,0)

    def test_unification_migration_and_capability_record(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode,0)
        task=int(self.invoke('start','--title','T','--actor','user').stdout.strip().split('=')[1])
        self.assertEqual(self.invoke('capability-check','--task',str(task),'--actor','user','--agent','claude','--required','review','--result','UNKNOWN','--basis','no model detail').returncode,0)
        self.assertEqual(self.invoke('record-unification-migration','--actor','user','--approval','APPROVED').returncode,0)
        self.assertEqual(self.invoke('verify').returncode,0)



    def test_git_commit_authorization_manifest_actor_and_one_time_consume(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git unavailable")
        repo = self.root / "repo"; repo.mkdir()
        subprocess.run([git, "init", "-q"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.email", "audit@example.test"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.name", "Audit Test"], cwd=repo, check=True)
        collab = repo / ".collab"; collab.mkdir()
        shutil.copy2(SOURCE, collab / "qms_audit.py")
        note = collab / "archive-note.txt"; note.write_text("first\n", encoding="utf-8")
        subprocess.run([git, "add", ".collab/archive-note.txt"], cwd=repo, check=True)
        auth = subprocess.run([sys.executable, "qms_audit.py", "git-commit-authorize", "--actor", "codex", "--scope", ".collab/archive-note.txt", "--user-request", "archive commit"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(auth.returncode, 0, auth.stderr)
        approval = next(line.split("=", 1)[1] for line in auth.stdout.splitlines() if line.startswith("GIT_COMMIT_APPROVAL_RECORD="))
        manifest = next(line.split("=", 1)[1] for line in auth.stdout.splitlines() if line.startswith("STAGED_MANIFEST="))
        valid = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "codex", "--operation", "GIT_COMMIT", "--approval", "APPROVED", "--approval-record", approval, "--staged-manifest", manifest], cwd=collab, text=True, capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        wrong_actor = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "claude", "--operation", "GIT_COMMIT", "--approval", "APPROVED", "--approval-record", approval, "--staged-manifest", manifest], cwd=collab, text=True, capture_output=True)
        self.assertEqual(wrong_actor.returncode, 3)
        # Commit without a hook in this disposable repo, then consume only via the post-commit command path.
        subprocess.run([git, "commit", "-m", "archive note"], cwd=repo, check=True)
        env = {**os.environ, "QMS_AUDIT_GIT_APPROVAL": "APPROVED"}
        consumed = subprocess.run([sys.executable, "qms_audit.py", "record-git-commit", "--actor", "codex", "--commit", "HEAD", "--approval-record", approval], cwd=collab, text=True, capture_output=True, env=env)
        self.assertEqual(consumed.returncode, 0, consumed.stderr)
        db = sqlite3.connect(collab / "runtime" / "audit.sqlite3")
        self.assertEqual(db.execute("SELECT actor FROM git_commit_authorization_usage WHERE approval_record=?", (approval,)).fetchone()[0], "codex")
        db.close()
        # Changed staged content and a consumed Record both block reuse.
        note.write_text("second\n", encoding="utf-8")
        subprocess.run([git, "add", ".collab/archive-note.txt"], cwd=repo, check=True)
        changed = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "codex", "--operation", "GIT_COMMIT", "--approval", "APPROVED", "--approval-record", approval, "--staged-manifest", manifest], cwd=collab, text=True, capture_output=True)
        self.assertEqual(changed.returncode, 3)
        verify = subprocess.run([sys.executable, "qms_audit.py", "verify"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

    def test_git_commit_authorization_manifest_hook_e2e(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git unavailable")
        # Git-for-Windows on this host can fail to create a signal pipe when it executes hooks.
        # The focused command-path test above remains deterministic; skip this OS-level hook check only for that known host failure.
        repo = self.root / "hook-repo"; repo.mkdir()
        subprocess.run([git, "init", "-q"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.email", "audit@example.test"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.name", "Audit Test"], cwd=repo, check=True)
        collab = repo / ".collab"; collab.mkdir()
        shutil.copy2(SOURCE, collab / "qms_audit.py")
        hooks = collab / "hooks"; hooks.mkdir()
        for name in ("pre-commit", "post-commit"):
            target = repo / ".git" / "hooks" / name
            shutil.copy2(HOOKS / name, target); target.chmod(target.stat().st_mode | 0o111)
        note = collab / "archive-note.txt"; note.write_text("hook\n", encoding="utf-8")
        subprocess.run([git, "add", ".collab/archive-note.txt"], cwd=repo, check=True)
        auth = subprocess.run([sys.executable, "qms_audit.py", "git-commit-authorize", "--actor", "codex", "--scope", ".collab/archive-note.txt", "--user-request", "archive commit"], cwd=collab, text=True, capture_output=True)
        approval = next(line.split("=", 1)[1] for line in auth.stdout.splitlines() if line.startswith("GIT_COMMIT_APPROVAL_RECORD="))
        env = {**os.environ, "QMS_AUDIT_GIT_APPROVAL": "APPROVED", "QMS_AUDIT_GIT_APPROVAL_RECORD": approval, "QMS_AUDIT_ACTOR": "codex"}
        committed = subprocess.run([git, "commit", "-m", "archive note"], cwd=repo, text=True, capture_output=True, env=env)
        if "couldn't create signal pipe, Win32 error 5" in committed.stderr:
            self.skipTest("Git-for-Windows hook subprocess signal pipe unavailable on this host")
        self.assertEqual(committed.returncode, 0, committed.stderr)
        db = sqlite3.connect(collab / "runtime" / "audit.sqlite3")
        self.assertIsNotNone(db.execute("SELECT 1 FROM git_commit_authorization_usage WHERE approval_record=?", (approval,)).fetchone())
        db.close()

    def test_deploy_authorization_actor_remote_commit_and_one_time_consume(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git unavailable")
        repo = self.root / "deploy-repo"; repo.mkdir()
        subprocess.run([git, "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.email", "audit@example.test"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.name", "Audit Test"], cwd=repo, check=True)
        collab = repo / ".collab"; collab.mkdir()
        shutil.copy2(SOURCE, collab / "qms_audit.py")
        (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run([git, "add", "app.py"], cwd=repo, check=True)
        subprocess.run([git, "commit", "-m", "app"], cwd=repo, check=True)
        head = subprocess.run([git, "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True).stdout.strip()
        auth = subprocess.run([sys.executable, "qms_audit.py", "deploy-authorize", "--actor", "codex", "--scope", "iqc-app", "--user-request", "deploy to production", "--remote", "deploy"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(auth.returncode, 0, auth.stderr)
        approval = next(line.split("=", 1)[1] for line in auth.stdout.splitlines() if line.startswith("DEPLOY_APPROVAL_RECORD="))
        commit = next(line.split("=", 1)[1] for line in auth.stdout.splitlines() if line.startswith("DEPLOY_COMMIT="))
        self.assertEqual(commit, head)
        wrong_actor = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "claude", "--operation", "DEPLOY", "--approval", "APPROVED", "--approval-record", approval, "--deploy-commit", commit, "--remote", "deploy"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(wrong_actor.returncode, 3)
        wrong_remote = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "codex", "--operation", "DEPLOY", "--approval", "APPROVED", "--approval-record", approval, "--deploy-commit", commit, "--remote", "origin"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(wrong_remote.returncode, 3)
        wrong_commit = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "codex", "--operation", "DEPLOY", "--approval", "APPROVED", "--approval-record", approval, "--deploy-commit", "0"*40, "--remote", "deploy"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(wrong_commit.returncode, 3)
        valid = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "codex", "--operation", "DEPLOY", "--approval", "APPROVED", "--approval-record", approval, "--deploy-commit", commit, "--remote", "deploy"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        db = sqlite3.connect(collab / "runtime" / "audit.sqlite3")
        self.assertEqual(db.execute("SELECT actor,remote,commit_hash FROM deploy_authorization_usage WHERE approval_record=?", (approval,)).fetchone(), ("codex", "deploy", commit))
        db.close()
        # One-time use: the exact same call must now be rejected.
        reused = subprocess.run([sys.executable, "qms_audit.py", "preflight", "--actor", "codex", "--operation", "DEPLOY", "--approval", "APPROVED", "--approval-record", approval, "--deploy-commit", commit, "--remote", "deploy"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(reused.returncode, 3)
        self.assertEqual(subprocess.run([sys.executable, "qms_audit.py", "verify"], cwd=collab, text=True, capture_output=True).returncode, 0)

    def test_deploy_pre_push_hook_blocks_missing_approval_and_gates_only_deploy_remote(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git unavailable")
        repo = self.root / "push-repo"; repo.mkdir()
        subprocess.run([git, "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.email", "audit@example.test"], cwd=repo, check=True)
        subprocess.run([git, "config", "user.name", "Audit Test"], cwd=repo, check=True)
        collab = repo / ".collab"; collab.mkdir()
        shutil.copy2(SOURCE, collab / "qms_audit.py")
        (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run([git, "add", "app.py"], cwd=repo, check=True)
        subprocess.run([git, "commit", "-m", "app"], cwd=repo, check=True)
        target = repo / ".git" / "hooks" / "pre-push"
        shutil.copy2(HOOKS / "pre-push", target); target.chmod(target.stat().st_mode | 0o111)
        # A bare repo stands in for the real "deploy" remote (GitHub) in this test.
        bare_deploy = self.root / "bare-deploy.git"
        subprocess.run([git, "init", "-q", "--bare", "-b", "main", str(bare_deploy)], check=True)
        subprocess.run([git, "remote", "add", "deploy", str(bare_deploy)], cwd=repo, check=True)
        bare_backup = self.root / "bare-backup.git"
        subprocess.run([git, "init", "-q", "--bare", "-b", "main", str(bare_backup)], check=True)
        subprocess.run([git, "remote", "add", "origin", str(bare_backup)], cwd=repo, check=True)
        # A differently-named remote (origin) is never gated, even with no approval at all.
        unrelated_push = subprocess.run([git, "push", "origin", "main"], cwd=repo, text=True, capture_output=True)
        self.assertEqual(unrelated_push.returncode, 0, unrelated_push.stderr)
        # deploy remote with no approval env vars must be blocked.
        blocked = subprocess.run([git, "push", "deploy", "main"], cwd=repo, text=True, capture_output=True)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("QMS deploy blocked", blocked.stderr)
        # With a valid one-time approval, the push to deploy succeeds.
        auth = subprocess.run([sys.executable, "qms_audit.py", "deploy-authorize", "--actor", "codex", "--scope", "iqc-app", "--user-request", "deploy to production", "--remote", "deploy"], cwd=collab, text=True, capture_output=True)
        self.assertEqual(auth.returncode, 0, auth.stderr)
        approval = next(line.split("=", 1)[1] for line in auth.stdout.splitlines() if line.startswith("DEPLOY_APPROVAL_RECORD="))
        env = {**os.environ, "QMS_AUDIT_DEPLOY_APPROVAL": "APPROVED", "QMS_AUDIT_DEPLOY_APPROVAL_RECORD": approval, "QMS_AUDIT_ACTOR": "codex"}
        allowed = subprocess.run([git, "push", "deploy", "main"], cwd=repo, text=True, capture_output=True, env=env)
        if "couldn't create signal pipe, Win32 error 5" in allowed.stderr:
            self.skipTest("Git-for-Windows hook subprocess signal pipe unavailable on this host")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        db = sqlite3.connect(collab / "runtime" / "audit.sqlite3")
        self.assertIsNotNone(db.execute("SELECT 1 FROM deploy_authorization_usage WHERE approval_record=?", (approval,)).fetchone())
        db.close()
        # One-time-use rejection at the qms_audit.py command level is covered by
        # test_deploy_authorization_actor_remote_commit_and_one_time_consume above;
        # a real second push here would be a no-op (nothing changed) and git would
        # not even invoke pre-push, so it would not actually exercise that path.

    def test_git_preflight_rejects_generic_decision_record(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode,0)
        created=self.invoke('record','--record-type','DECISION','--actor','user','--approval','APPROVED','--knowledge-state','CONFIRMED_DECISION','--payload',json.dumps({'decision':'commit approved','decided_by':'user','scope':'test'}))
        self.assertEqual(created.returncode,0,created.stderr)
        rid=created.stdout.strip().split('=')[1]
        denied=self.invoke('preflight','--actor','user','--operation','GIT_COMMIT','--approval','APPROVED','--approval-record',rid,'--staged-manifest','0'*64)
        self.assertEqual(denied.returncode,3)


    def test_invalid_request_is_audited_and_safe_agent_can_progress(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode,0)
        bad={'protocol_name':'CLAUDE_CHATGPT_PROTOCOL','protocol_version':'1.0.0','message_id':'msg-bad','sender':'claude','receiver':'codex','timestamp':'2026-09-20T00:00:00Z','payload':{'message_type':'REQUEST','request_type':'REVIEW','scope':'x'}}
        path=self.root/'bad.json'; path.write_text(json.dumps(bad),encoding='utf-8')
        self.assertNotEqual(self.invoke('receive-envelope','--file',str(path),'--receiver','codex').returncode,0)
        db=sqlite3.connect(self.root/'runtime'/'audit.sqlite3'); events=[x[0] for x in db.execute('select event_type from audit_events')]; db.close()
        self.assertIn('REQUEST_RECEIVE_FAILED',events)


    def workflow_start(self, *extra, actor='claude', scope='x', user_request='x'):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode, 0)
        self.assertEqual(self.invoke('backfill-workflow-permission', '--approval', 'APPROVED').returncode, 0)
        authorized = self.invoke('workflow-authorize', '--actor', actor, '--scope', scope, '--user-request', user_request)
        self.assertEqual(authorized.returncode, 0, authorized.stderr)
        authorization = authorized.stdout.strip().split('=', 1)[1]
        started = self.invoke('workflow-start', '--actor', actor, '--authorization-record', authorization, '--title', 'workflow', '--scope', scope, '--user-request', user_request, *extra)
        self.assertEqual(started.returncode, 0, started.stderr)
        values = dict(line.split('=', 1) for line in started.stdout.splitlines() if '=' in line)
        return int(values['TASK_ID']), values['RUN_ID'], values['WORKFLOW_CAPABILITY_TOKEN']

    @staticmethod
    def hash_value(value):
        import hashlib
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

    @staticmethod
    def report(role, *, outcome='PASS', verification='VERIFIED', evidence=None, issues=None, coverage_limits=None):
        return {'role': role, 'outcome': outcome, 'summary': role+' result', 'scope': 'x',
                'evidence': evidence if evidence is not None else [{'type':'inspection','status':verification,'role':role,'detail':'checked'}],
                'verification_status': verification, 'issues': issues or [], 'coverage_limits': coverage_limits or [], 'next_action': 'next'}

    def dispatch(self, task, run, token, role, actor='claude'):
        dispatched=self.invoke('workflow-dispatch','--task',str(task),'--run',run,'--capability-token',token,'--actor',actor,'--role',role,'--scope','x','--why','test','--work','work')
        self.assertEqual(dispatched.returncode, 0, dispatched.stderr)
        self.assertIn('ARCHIVE_CONTEXT=', dispatched.stdout)
        return next(line.split('=',1)[1] for line in dispatched.stdout.splitlines() if line.startswith('REQUEST_RECORD='))

    def result(self, task, run, token, role, request, report, actor='claude'):
        return self.invoke('workflow-result','--task',str(task),'--run',run,'--capability-token',token,'--actor',actor,'--role',role,'--request',request,'--result',json.dumps(report))

    def test_workflow_token_and_run_are_required(self):
        task, run, token = self.workflow_start()
        blocked = self.invoke('workflow-dispatch','--task',str(task),'--run',run,'--capability-token','wrong','--actor','claude','--role','planner','--scope','x','--why','test','--work','work')
        self.assertEqual(blocked.returncode, 3)
        request = self.dispatch(task, run, token, 'planner')
        self.assertTrue(request)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_result_requires_matching_dispatched_request(self):
        task, run, token = self.workflow_start()
        planner_request = self.dispatch(task, run, token, 'planner')
        wrong_role = self.result(task, run, token, 'developer', planner_request, self.report('developer'))
        self.assertEqual(wrong_role.returncode, 2)
        task2, run2, token2 = self.workflow_start()
        other_request = self.dispatch(task2, run2, token2, 'planner')
        wrong_task = self.result(task, run, token, 'planner', other_request, self.report('planner'))
        self.assertEqual(wrong_task.returncode, 2)
        self.assertEqual(self.result(task, run, token, 'planner', planner_request, self.report('planner')).returncode, 0)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_blocks_missing_typed_render_and_e2e_evidence(self):
        task, run, token = self.workflow_start('--visual-change', '--e2e-required')
        for role in ('planner','developer','quality-watcher'):
            request=self.dispatch(task, run, token, role)
            self.assertEqual(self.result(task, run, token, role, request, self.report(role)).returncode, 0)
        final=self.invoke('workflow-finalize','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude')
        self.assertEqual(final.returncode, 3)
        self.assertIn('렌더링', final.stdout); self.assertIn('end-to-end', final.stdout)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_completes_with_typed_verified_evidence(self):
        task, run, token = self.workflow_start('--visual-change', '--e2e-required')
        evidence = {
            'planner': [{'type':'plan','status':'VERIFIED','role':'planner','detail':'spec'}],
            'developer': [{'type':'render','status':'VERIFIED','role':'developer','detail':'headless render'}, {'type':'e2e','status':'VERIFIED','role':'developer','detail':'full flow'}],
            'quality-watcher': [{'type':'review','status':'VERIFIED','role':'quality-watcher','detail':'review'}],
        }
        for role in ('planner','developer','quality-watcher'):
            request=self.dispatch(task, run, token, role)
            self.assertEqual(self.result(task, run, token, role, request, self.report(role, evidence=evidence[role])).returncode, 0)
        final=self.invoke('workflow-finalize','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude')
        self.assertEqual(final.returncode, 0, final.stderr); self.assertIn('WORKFLOW_STATUS=COMPLETED', final.stdout)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_blocks_quality_issue_and_unknown_conflicts(self):
        task, run, token = self.workflow_start()
        reports = {
            'planner': self.report('planner'),
            'developer': self.report('developer', verification='PARTIAL'),
            'quality-watcher': self.report('quality-watcher', issues=['needs decision']),
        }
        for role in ('planner','developer','quality-watcher'):
            request=self.dispatch(task, run, token, role)
            self.assertEqual(self.result(task, run, token, role, request, reports[role]).returncode, 0)
        final=self.invoke('workflow-finalize','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude')
        self.assertEqual(final.returncode, 3); self.assertIn('unknown/partial', final.stdout); self.assertIn('ISSUE', final.stdout)
        self.assertEqual(self.invoke('verify').returncode, 0)


    def test_workflow_allows_planner_declared_coverage_limit_without_issue(self):
        task, run, token = self.workflow_start()
        reports = {
            'planner': self.report('planner', verification='PARTIAL', coverage_limits=['planner does not execute the live CLI']),
            'developer': self.report('developer'),
            'quality-watcher': self.report('quality-watcher'),
        }
        for role in ('planner', 'developer', 'quality-watcher'):
            request = self.dispatch(task, run, token, role)
            self.assertEqual(self.result(task, run, token, role, request, reports[role]).returncode, 0)
        final = self.invoke('workflow-finalize', '--task', str(task), '--run', run, '--capability-token', token, '--actor', 'claude')
        self.assertEqual(final.returncode, 0, final.stdout)
        db = sqlite3.connect(self.root/'runtime'/'audit.sqlite3')
        payload = json.loads(db.execute("select payload_json from protocol_records where record_type='EVIDENCE' and json_extract(payload_json, '$.workflow_role')='planner' order by rowid desc limit 1").fetchone()[0])
        db.close()
        self.assertEqual(payload['content']['coverage_limits'], ['planner does not execute the live CLI'])

    def test_workflow_blocks_planner_partial_without_coverage_or_with_actual_issue(self):
        task, run, token = self.workflow_start()
        reports = {
            'planner': self.report('planner', verification='PARTIAL'),
            'developer': self.report('developer'),
            'quality-watcher': self.report('quality-watcher'),
        }
        for role in ('planner', 'developer', 'quality-watcher'):
            request = self.dispatch(task, run, token, role)
            self.assertEqual(self.result(task, run, token, role, request, reports[role]).returncode, 0)
        blocked = self.invoke('workflow-finalize', '--task', str(task), '--run', run, '--capability-token', token, '--actor', 'claude')
        self.assertEqual(blocked.returncode, 3); self.assertIn('unknown/partial', blocked.stdout)

        task2, run2, token2 = self.workflow_start()
        reports2 = {
            'planner': self.report('planner', verification='PARTIAL', coverage_limits=['no live CLI'], issues=['implementation defect found']),
            'developer': self.report('developer'),
            'quality-watcher': self.report('quality-watcher'),
        }
        for role in ('planner', 'developer', 'quality-watcher'):
            request = self.dispatch(task2, run2, token2, role)
            self.assertEqual(self.result(task2, run2, token2, role, request, reports2[role]).returncode, 0)
        blocked_issue = self.invoke('workflow-finalize', '--task', str(task2), '--run', run2, '--capability-token', token2, '--actor', 'claude')
        self.assertEqual(blocked_issue.returncode, 3); self.assertIn('ISSUE', blocked_issue.stdout)

    def test_workflow_blocks_developer_or_quality_verification_gap(self):
        for limited_role in ('developer', 'quality-watcher'):
            task, run, token = self.workflow_start()
            reports = {
                'planner': self.report('planner'),
                'developer': self.report('developer'),
                'quality-watcher': self.report('quality-watcher'),
            }
            reports[limited_role] = self.report(limited_role, verification='PARTIAL', coverage_limits=['not a completion exemption'])
            for role in ('planner', 'developer', 'quality-watcher'):
                request = self.dispatch(task, run, token, role)
                self.assertEqual(self.result(task, run, token, role, request, reports[role]).returncode, 0)
            blocked = self.invoke('workflow-finalize', '--task', str(task), '--run', run, '--capability-token', token, '--actor', 'claude')
            self.assertEqual(blocked.returncode, 3); self.assertIn('unknown/partial', blocked.stdout)

    def test_workflow_result_validates_coverage_limits(self):
        task, run, token = self.workflow_start()
        request = self.dispatch(task, run, token, 'planner')
        missing = self.report('planner'); missing.pop('coverage_limits')
        self.assertEqual(self.result(task, run, token, 'planner', request, missing).returncode, 2)
        invalid = self.report('planner', coverage_limits=[''])
        self.assertEqual(self.result(task, run, token, 'planner', request, invalid).returncode, 2)

    def test_workflow_rejects_evidence_role_impersonation(self):
        task, run, token = self.workflow_start('--visual-change', '--e2e-required')
        request = self.dispatch(task, run, token, 'planner')
        forged = self.report('planner', evidence=[{'type':'render','status':'VERIFIED','role':'developer','detail':'forged'}, {'type':'e2e','status':'VERIFIED','role':'developer','detail':'forged'}])
        rejected = self.result(task, run, token, 'planner', request, forged)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn('evidence role', rejected.stderr)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_blocks_missing_required_role_and_quality_fail(self):
        task, run, token = self.workflow_start()
        for role in ('planner', 'developer'):
            request = self.dispatch(task, run, token, role)
            self.assertEqual(self.result(task, run, token, role, request, self.report(role)).returncode, 0)
        missing = self.invoke('workflow-finalize','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude')
        self.assertEqual(missing.returncode, 3); self.assertIn('quality-watcher', missing.stdout)

        task2, run2, token2 = self.workflow_start()
        for role in ('planner', 'developer', 'quality-watcher'):
            request = self.dispatch(task2, run2, token2, role)
            report = self.report(role, outcome='FAIL') if role == 'quality-watcher' else self.report(role)
            self.assertEqual(self.result(task2, run2, token2, role, request, report).returncode, 0)
        failed = self.invoke('workflow-finalize','--task',str(task2),'--run',run2,'--capability-token',token2,'--actor','claude')
        self.assertEqual(failed.returncode, 3); self.assertIn('quality-watcher PASS', failed.stdout)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_issue_resolution_is_append_only_and_unblocks(self):
        task, run, token = self.workflow_start()
        ids = {}
        for role in ('planner', 'developer', 'quality-watcher'):
            request = self.dispatch(task, run, token, role)
            report = self.report(role, issues=['follow up']) if role == 'quality-watcher' else self.report(role)
            response = self.result(task, run, token, role, request, report)
            self.assertEqual(response.returncode, 0, response.stderr)
            if role == 'quality-watcher':
                ids['evidence'] = json.loads(next(line.split('=',1)[1] for line in response.stdout.splitlines() if line.startswith('RESULT_RECORDS=')))[0]
        db=sqlite3.connect(self.root/'runtime'/'audit.sqlite3')
        issue=db.execute("select record_id from protocol_records where record_type='ISSUE' order by rowid desc limit 1").fetchone()[0]; db.close()
        blocked=self.invoke('workflow-finalize','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude')
        self.assertEqual(blocked.returncode,3); self.assertIn('ISSUE',blocked.stdout)
        unrelated=self.invoke('record','--record-type','EVIDENCE','--actor','user','--payload',json.dumps({'source_type':'unrelated','content':'outside workflow','verification_status':'VERIFIED'}))
        self.assertEqual(unrelated.returncode,0,unrelated.stderr)
        unrelated_id=unrelated.stdout.strip().split('=')[1]
        wrong_evidence=self.invoke('workflow-issue-resolution','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude','--issue',issue,'--resolution','RESOLVED','--basis','wrong evidence','--evidence-record',unrelated_id)
        self.assertEqual(wrong_evidence.returncode,2)
        resolved=self.invoke('workflow-issue-resolution','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude','--issue',issue,'--resolution','RESOLVED','--basis','verified fix','--evidence-record',ids['evidence'])
        self.assertEqual(resolved.returncode,0,resolved.stderr)
        final=self.invoke('workflow-finalize','--task',str(task),'--run',run,'--capability-token',token,'--actor','claude')
        self.assertEqual(final.returncode,0,final.stderr)
        db=sqlite3.connect(self.root/'runtime'/'audit.sqlite3')
        self.assertEqual(db.execute("select status from protocol_records where record_id=?",(issue,)).fetchone()[0], 'ACTIVE'); db.close()
        self.assertEqual(self.invoke('verify').returncode,0)


    def test_workflow_requires_matching_one_time_user_authorization(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode, 0)
        self.assertEqual(self.invoke('backfill-workflow-permission', '--approval', 'APPROVED').returncode, 0)
        missing = self.invoke('workflow-start', '--actor', 'codex', '--title', 'x', '--scope', 'scope', '--user-request', 'request')
        self.assertNotEqual(missing.returncode, 0)
        auth = self.invoke('workflow-authorize', '--actor', 'codex', '--scope', 'scope', '--user-request', 'request')
        self.assertEqual(auth.returncode, 0, auth.stderr)
        record = auth.stdout.strip().split('=', 1)[1]
        mismatch = self.invoke('workflow-start', '--actor', 'codex', '--authorization-record', record, '--title', 'x', '--scope', 'different', '--user-request', 'request')
        self.assertNotEqual(mismatch.returncode, 0)
        started = self.invoke('workflow-start', '--actor', 'codex', '--authorization-record', record, '--title', 'x', '--scope', 'scope', '--user-request', 'request')
        self.assertEqual(started.returncode, 0, started.stderr)
        reused = self.invoke('workflow-start', '--actor', 'codex', '--authorization-record', record, '--title', 'x', '--scope', 'scope', '--user-request', 'request')
        self.assertNotEqual(reused.returncode, 0)

    def test_workflow_v2_needs_no_secret_and_rejects_legacy_v1_authorization(self):
        self.assertEqual(self.invoke('bootstrap-protocol').returncode, 0)
        self.assertEqual(self.invoke('backfill-workflow-permission', '--approval', 'APPROVED').returncode, 0)
        legacy = self.invoke('record', '--record-type', 'DECISION', '--actor', 'user', '--approval', 'APPROVED', '--knowledge-state', 'CONFIRMED_DECISION', '--payload', json.dumps({'decision':'legacy workflow authorization','decided_by':'USER','scope':'x','decision_type':'WORKFLOW_AUTHORIZATION','workflow_approval_state':'APPROVED','authorized_actor':'claude','scope_hash':self.hash_value('x'),'user_request_hash':self.hash_value('x'),'authorizer_scheme':'runtime_authorizer_secret_v1','authorizer_verified':True}))
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        legacy_id = legacy.stdout.strip().split('=', 1)[1]
        blocked_legacy = self.invoke('workflow-start', '--actor', 'claude', '--authorization-record', legacy_id, '--title', 'x', '--scope', 'x', '--user-request', 'x')
        self.assertNotEqual(blocked_legacy.returncode, 0)
        valid = self.invoke('workflow-authorize', '--actor', 'claude', '--scope', 'x', '--user-request', 'x')
        self.assertEqual(valid.returncode, 0, valid.stderr)
        db=sqlite3.connect(self.root/'runtime'/'audit.sqlite3')
        events=[x[0] for x in db.execute('select event_type from audit_events')]
        migration=db.execute("select details_json from protocol_migrations where migration_key='workflow-authorizer-v1-retired-by-v2'").fetchone()
        db.close()
        self.assertIn('WORKFLOW_AUTHORIZER_V1_RETIRED', events)
        self.assertIsNotNone(migration)
        self.assertEqual(self.invoke('verify').returncode, 0)


    def test_workflow_is_actor_neutral_and_dispatch_result_are_one_time(self):
        task, run, token = self.workflow_start(actor='codex')
        request = self.dispatch(task, run, token, 'planner', actor='codex')
        duplicate = self.invoke('workflow-dispatch', '--task', str(task), '--run', run, '--capability-token', token, '--actor', 'codex', '--role', 'planner', '--scope', 'x', '--why', 'again', '--work', 'again')
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(self.result(task, run, token, 'planner', request, self.report('planner'), actor='codex').returncode, 0)
        repeated = self.result(task, run, token, 'planner', request, self.report('planner'), actor='codex')
        self.assertNotEqual(repeated.returncode, 0)
        wrong_actor = self.invoke('workflow-finalize', '--task', str(task), '--run', run, '--capability-token', token, '--actor', 'claude')
        self.assertEqual(wrong_actor.returncode, 3)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_test_permission_is_one_time_and_allows_only_exact_collab_commands(self):
        task, run, token = self.workflow_start(actor='codex')
        before = self.invoke('run', '--task', str(task), '--run', run, '--capability-token', token,
                             '--actor', 'codex', '--summary', 'ledger verification', '--',
                             'python', 'qms_audit.py', 'verify')
        self.assertEqual(before.returncode, 3)
        self.assertNotEqual(self.invoke('backfill-workflow-test-permission').returncode, 0)
        migration = self.invoke('backfill-workflow-test-permission', '--approval', 'APPROVED')
        self.assertEqual(migration.returncode, 0, migration.stderr)
        self.assertIn('WORKFLOW_TEST_PERMISSION_RECORDS=', migration.stdout)
        self.assertEqual(self.invoke('backfill-workflow-test-permission', '--approval', 'APPROVED').returncode, 0)

        allowed = self.invoke('run', '--task', str(task), '--run', run, '--capability-token', token,
                              '--actor', 'codex', '--summary', 'ledger verification', '--',
                              'python', 'qms_audit.py', 'verify')
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        db = sqlite3.connect(self.root / 'runtime' / 'audit.sqlite3')
        profiles = [json.loads(row[0]) for row in db.execute(
            "select payload_json from protocol_records where record_type='PERMISSION_PROFILE'"
        )]
        db.close()
        self.assertEqual(sum(p.get('operation') == 'WORKFLOW_RUN_TEST' for p in profiles), 2)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_workflow_test_run_rejects_wrong_context_and_arbitrary_command(self):
        task, run, token = self.workflow_start(actor='claude')
        self.assertEqual(self.invoke('backfill-workflow-test-permission', '--approval', 'APPROVED').returncode, 0)
        wrong_token = self.invoke('run', '--task', str(task), '--run', run, '--capability-token', 'wrong',
                                  '--actor', 'claude', '--summary', 'ledger verification', '--',
                                  'python', 'qms_audit.py', 'verify')
        self.assertEqual(wrong_token.returncode, 3)
        wrong_actor = self.invoke('run', '--task', str(task), '--run', run, '--capability-token', token,
                                  '--actor', 'codex', '--summary', 'ledger verification', '--',
                                  'python', 'qms_audit.py', 'verify')
        self.assertEqual(wrong_actor.returncode, 3)
        marker = self.root / 'must-not-exist'
        arbitrary = self.invoke('run', '--task', str(task), '--run', run, '--capability-token', token,
                                '--actor', 'claude', '--summary', 'attempt arbitrary command', '--',
                                'python', '-c', "open('must-not-exist', 'w').write('no')")
        self.assertEqual(arbitrary.returncode, 3)
        self.assertFalse(marker.exists())
        db = sqlite3.connect(self.root / 'runtime' / 'audit.sqlite3')
        events = [row[0] for row in db.execute('select event_type from audit_events')]
        db.close()
        self.assertIn('WORKFLOW_TEST_COMMAND_BLOCKED', events)
        self.assertEqual(self.invoke('verify').returncode, 0)

    def test_user_run_remains_unrestricted_by_workflow_test_allowlist(self):
        self.invoke('init')
        task = int(self.invoke('start', '--title', 'user test', '--actor', 'user').stdout.strip().split('=')[1])
        result = self.invoke('run', '--task', str(task), '--actor', 'user', '--summary', 'user command', '--',
                             sys.executable, '-c', 'raise SystemExit(0)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.invoke('verify').returncode, 0)

if __name__ == "__main__": unittest.main()
