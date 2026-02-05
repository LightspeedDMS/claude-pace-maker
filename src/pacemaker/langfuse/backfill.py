#!/usr/bin/env python3
"""
Langfuse historical session backfill.

Provides functionality to scan historical transcript files and push them
to Langfuse for retrospective analysis.
"""

from datetime import datetime
from typing import Dict, Any, List
from pathlib import Path

from ..logger import log_info, log_warning
from ..telemetry.jsonl_parser import parse_session_metadata, extract_user_id
from ..telemetry.token_extractor import extract_token_usage
from ..telemetry.tool_call_extractor import extract_tool_calls
from .transformer import create_trace
from .push import push_trace


# Timeout for backfill operations (longer than realtime push)
BACKFILL_TIMEOUT_SECONDS = 5


def find_sessions_since(transcripts_dir: str, since: datetime) -> List[Dict[str, Any]]:
    """
    Find all transcript files modified since cutoff date.

    Args:
        transcripts_dir: Directory containing JSONL transcripts
        since: Cutoff datetime (only files modified after this are included)

    Returns:
        List of session dicts with 'path' and 'mtime' keys
    """
    sessions = []

    try:
        transcripts_path = Path(transcripts_dir)
        if not transcripts_path.exists():
            return []

        # Find all .jsonl files
        for transcript_file in transcripts_path.glob("*.jsonl"):
            # Check modification time
            mtime = datetime.fromtimestamp(transcript_file.stat().st_mtime)

            if mtime >= since:
                sessions.append({"path": str(transcript_file), "mtime": mtime})

    except Exception as e:
        log_warning("backfill", f"Error scanning directory: {transcripts_dir}", e)
        return []

    return sessions


def is_session_pushed(
    session_id: str, base_url: str, public_key: str, secret_key: str
) -> bool:
    """
    Check if session has already been pushed to Langfuse.

    For now, we'll always return False to attempt push. A more sophisticated
    implementation could query Langfuse API to check if trace exists.

    Args:
        session_id: Session identifier
        base_url: Langfuse base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key

    Returns:
        True if session already exists in Langfuse, False otherwise
    """
    # TODO: Implement actual check via Langfuse API
    # For now, always attempt to push (Langfuse handles duplicates)
    return False


def push_session(
    transcript_path: str, base_url: str, public_key: str, secret_key: str
) -> bool:
    """
    Parse session transcript and push to Langfuse.

    Args:
        transcript_path: Path to JSONL transcript file
        base_url: Langfuse base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key

    Returns:
        True if successful, False if failed
    """
    try:
        # Parse session data
        metadata = parse_session_metadata(transcript_path)
        user_id = extract_user_id(transcript_path)
        token_usage = extract_token_usage(transcript_path)
        tool_calls = extract_tool_calls(transcript_path)

        # Create trace
        trace = create_trace(
            session_id=metadata["session_id"],
            user_id=user_id,
            model=metadata["model"],
            token_usage=token_usage,
            tool_calls=tool_calls,
            timestamp=metadata["timestamp"],
        )

        # Push to Langfuse
        success = push_trace(
            base_url=base_url,
            public_key=public_key,
            secret_key=secret_key,
            trace=trace,
            timeout=BACKFILL_TIMEOUT_SECONDS,
        )

        return success

    except Exception as e:
        log_warning("backfill", f"Failed to push session: {transcript_path}", e)
        return False


def _process_single_session(
    session: Dict[str, Any],
    base_url: str,
    public_key: str,
    secret_key: str,
    progress: bool,
) -> str:
    """
    Process a single session during backfill.

    Args:
        session: Session dict with 'path' key
        base_url: Langfuse base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key
        progress: Whether to print progress

    Returns:
        Status: 'success', 'failed', or 'skipped'
    """
    transcript_path = session["path"]

    # Check if already pushed
    try:
        metadata = parse_session_metadata(transcript_path)
        session_id = metadata.get("session_id", "unknown")

        if is_session_pushed(session_id, base_url, public_key, secret_key):
            if progress:
                print(f"  Skipped: {session_id} (already pushed)")
            return "skipped"

    except Exception as e:
        log_warning("backfill", f"Error checking session status: {transcript_path}", e)
        if progress:
            print(f"  Failed: {transcript_path} (corrupt or unreadable)")
        return "failed"

    # Push session
    success = push_session(transcript_path, base_url, public_key, secret_key)

    if success:
        if progress:
            print(f"  Success: {session_id}")
        return "success"
    else:
        if progress:
            print(f"  Failed: {session_id}")
        return "failed"


def backfill_sessions(
    transcripts_dir: str,
    since: datetime,
    base_url: str,
    public_key: str,
    secret_key: str,
    progress: bool = False,
) -> Dict[str, int]:
    """
    Backfill historical sessions to Langfuse.

    Args:
        transcripts_dir: Directory containing JSONL transcripts
        since: Only backfill sessions modified after this date
        base_url: Langfuse base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key
        progress: Whether to print progress messages

    Returns:
        Dict with 'total', 'success', 'failed', 'skipped' counts
    """
    result = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    # Find sessions
    sessions = find_sessions_since(transcripts_dir, since)
    result["total"] = len(sessions)

    if progress:
        print(f"Found {len(sessions)} sessions to process")

    # Process each session
    for i, session in enumerate(sessions, 1):
        if progress:
            print(f"Processing {i}/{len(sessions)} sessions...")

        status = _process_single_session(
            session, base_url, public_key, secret_key, progress
        )
        result[status] += 1

    # Print summary
    if progress:
        print("\nBackfill complete:")
        print(
            f"  {result['total']} processed, {result['success']} new, {result['skipped']} skipped"
        )
        if result["failed"] > 0:
            print(f"  {result['failed']} failed")

    log_info(
        "backfill",
        f"Backfill complete: {result['success']} pushed, {result['failed']} failed, {result['skipped']} skipped",
    )

    return result
