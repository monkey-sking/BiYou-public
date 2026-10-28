import json
import os
import subprocess
import threading
import uuid
from datetime import datetime

from core.utils.config import PROJECT_ROOT, load_config
from core.utils.logger import log


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve_path(path: str, default_path: str) -> str:
    target = path or default_path
    if not os.path.isabs(target):
        target = os.path.join(PROJECT_ROOT, target)
    return os.path.abspath(os.path.expanduser(target))


def load_workflow_signal_config(config: dict) -> dict:
    cfg = config or load_config()
    raw = cfg.get("workflow_signals") or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "state_dir": _resolve_path(
            raw.get("state_dir"),
            os.path.join("logs", "workflow_status"),
        ),
        "events_file": _resolve_path(
            raw.get("events_file"),
            os.path.join("logs", "workflow_events.jsonl"),
        ),
        "agent_hook": (raw.get("agent_hook") or "").strip(),
        "notify_hook": (raw.get("notify_hook") or "").strip(),
    }


def classify_workflow_failure(stage: str, error_code: str) -> tuple[bool, str]:
    mapping = {
        ("input", "INVALID_URL"): (False, "notify_invalid_input"),
        ("download_prepare", "VIDEO_PREPARE_FAILED"): (True, "retry_download_prepare"),
        ("metadata", "METADATA_PROCESS_FAILED"): (True, "retry_metadata"),
        ("cover", "COVER_PROCESS_FAILED"): (True, "retry_cover"),
        ("download_wait", "DOWNLOAD_FINALIZE_FAILED"): (True, "retry_download"),
        ("upload", "UPLOAD_FAILED"): (True, "retry_upload"),
        ("workflow", "WORKFLOW_EXCEPTION"): (False, "inspect_logs"),
        ("workflow", "POST_PROCESS_EXCEPTION"): (False, "inspect_logs"),
    }
    return mapping.get((stage, error_code), (False, "inspect_logs"))


class WorkflowSignalRecorder:
    def __init__(
        self,
        url: str,
        dry_run: bool = False,
        config: dict = None,
        run_id: str = None,
    ):
        self.url = url
        self.dry_run = dry_run
        self.run_id = run_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.cfg = load_workflow_signal_config(config=config)
        self.enabled = self.cfg["enabled"]
        self.lock = threading.Lock()
        self.finished = False
        self.state = {
            "run_id": self.run_id,
            "url": url,
            "dry_run": dry_run,
            "status": "created",
            "current_stage": None,
            "started_at": _now_iso(),
            "finished_at": None,
            "success": None,
            "recoverable": None,
            "suggested_action": None,
            "metadata_path": None,
            "video_path": None,
            "last_error": None,
        }

        if self.enabled:
            os.makedirs(self.cfg["state_dir"], exist_ok=True)
            os.makedirs(os.path.dirname(self.cfg["events_file"]), exist_ok=True)

    @property
    def state_file(self) -> str:
        return os.path.join(self.cfg["state_dir"], f"{self.run_id}.json")

    @property
    def latest_state_file(self) -> str:
        return os.path.join(self.cfg["state_dir"], "latest.json")

    def set_paths(self, metadata_path: str = None, video_path: str = None):
        with self.lock:
            if metadata_path:
                self.state["metadata_path"] = os.path.abspath(metadata_path)
            if video_path:
                self.state["video_path"] = os.path.abspath(video_path)
            self._write_state_locked()

    def run_started(self, details: dict = None):
        self._emit(
            event_type="run_started",
            status="running",
            details=details,
        )

    def stage_started(self, stage: str, details: dict = None):
        self._emit(
            event_type="stage_started",
            status="running",
            stage=stage,
            details=details,
        )

    def stage_succeeded(self, stage: str, details: dict = None):
        self._emit(
            event_type="stage_succeeded",
            status="running",
            stage=stage,
            details=details,
        )

    def signal_stage_failure(
        self,
        stage: str,
        error_code: str,
        message: str,
        details: dict = None,
        recoverable: bool = None,
        suggested_action: str = None,
    ):
        if recoverable is None or suggested_action is None:
            auto_recoverable, auto_action = classify_workflow_failure(stage, error_code)
            if recoverable is None:
                recoverable = auto_recoverable
            if suggested_action is None:
                suggested_action = auto_action

        self._emit(
            event_type="run_failed",
            status="failed",
            stage=stage,
            error_code=error_code,
            message=message,
            details=details,
            recoverable=recoverable,
            suggested_action=suggested_action,
            finished=True,
        )

    def run_succeeded(self, details: dict = None):
        self._emit(
            event_type="run_succeeded",
            status="succeeded",
            details=details,
            finished=True,
        )

    def run_failed(self, details: dict = None, error_code: str = None, message: str = None, stage: str = None):
        self._emit(
            event_type="run_failed",
            status="failed",
            stage=stage,
            error_code=error_code,
            message=message,
            details=details,
            finished=True,
        )

    def _emit(
        self,
        event_type: str,
        status: str,
        stage: str = None,
        error_code: str = None,
        message: str = None,
        details: dict = None,
        recoverable: bool = None,
        suggested_action: str = None,
        finished: bool = False,
    ):
        timestamp = _now_iso()
        event = {
            "timestamp": timestamp,
            "event_type": event_type,
            "run_id": self.run_id,
            "url": self.url,
            "dry_run": self.dry_run,
            "status": status,
            "stage": stage,
            "error_code": error_code,
            "message": message,
            "recoverable": recoverable,
            "suggested_action": suggested_action,
            "details": details or {},
            "metadata_path": self.state.get("metadata_path"),
            "video_path": self.state.get("video_path"),
        }

        with self.lock:
            self.state["status"] = status
            self.state["current_stage"] = stage or self.state.get("current_stage")
            self.state["recoverable"] = recoverable
            self.state["suggested_action"] = suggested_action
            if error_code or message:
                self.state["last_error"] = {
                    "timestamp": timestamp,
                    "stage": stage,
                    "error_code": error_code,
                    "message": message,
                    "details": details or {},
                }
            if finished:
                self.state["finished_at"] = timestamp
                self.state["success"] = status == "succeeded"
                self.finished = True
            self._append_event_locked(event)
            self._write_state_locked()

        if event_type == "run_failed":
            self._invoke_hook(self.cfg.get("agent_hook"), event, hook_name="agent_hook")
            if recoverable is False:
                self._invoke_hook(self.cfg.get("notify_hook"), event, hook_name="notify_hook")

    def _append_event_locked(self, event: dict):
        if not self.enabled:
            return
        with open(self.cfg["events_file"], "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _write_state_locked(self):
        if not self.enabled:
            return
        for target in (self.state_file, self.latest_state_file):
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _invoke_hook(self, command: str, event: dict, hook_name: str):
        if not self.enabled or not command:
            return

        env = os.environ.copy()
        env["BIYOU_WORKFLOW_EVENT_JSON"] = json.dumps(event, ensure_ascii=False)
        env["BIYOU_WORKFLOW_EVENT_TYPE"] = event["event_type"]
        env["BIYOU_WORKFLOW_RUN_ID"] = self.run_id
        env["BIYOU_WORKFLOW_STATUS_FILE"] = self.state_file

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                log.warning(
                    "⚠️ Workflow {} failed (exit={}): {}",
                    hook_name,
                    result.returncode,
                    stderr[:200] if stderr else "no stderr",
                )
        except Exception as e:
            log.warning(f"⚠️ Workflow {hook_name} invocation failed: {e}")
