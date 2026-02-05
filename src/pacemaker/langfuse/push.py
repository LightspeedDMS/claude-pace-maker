#!/usr/bin/env python3
"""
Langfuse trace push functionality.

Handles submission of traces to Langfuse API with timeout and error handling.
"""

import requests
from typing import Dict, Any

from ..logger import log_warning, log_info


def push_trace(
    base_url: str,
    public_key: str,
    secret_key: str,
    trace: Dict[str, Any],
    timeout: int = 2,
) -> bool:
    """
    Push trace to Langfuse API.

    Implements AC4 (<2s timeout) and AC5 (graceful failure) requirements.

    Args:
        base_url: Langfuse API base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key
        trace: Langfuse trace dict
        timeout: Request timeout in seconds (default: 2 for AC4)

    Returns:
        True if successful, False if failed (graceful failure for AC5)
    """
    try:
        # Use direct traces API endpoint
        traces_url = f"{base_url.rstrip('/')}/api/public/traces"

        # Submit trace directly
        response = requests.post(
            traces_url,
            json=trace,
            auth=(public_key, secret_key),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code in (200, 201, 202, 207):
            log_info(
                "langfuse_push",
                f"Successfully pushed trace {trace.get('id', 'unknown')}",
            )
            return True
        else:
            log_warning(
                "langfuse_push",
                f"Failed to push trace: HTTP {response.status_code}",
                None,
            )
            return False

    except requests.exceptions.Timeout:
        log_warning("langfuse_push", f"Push timed out after {timeout}s", None)
        return False
    except requests.exceptions.ConnectionError:
        log_warning("langfuse_push", "Unable to reach Langfuse API", None)
        return False
    except Exception as e:
        log_warning("langfuse_push", f"Push failed: {str(e)}", e)
        return False


def push_batch_events(
    base_url: str, public_key: str, secret_key: str, batch: list, timeout: int = 2
) -> bool:
    """
    Push batch events to Langfuse ingestion API.

    Langfuse ingestion API expects batch array with event objects:
    POST /api/public/ingestion
    Body: {"batch": [event1, event2, ...]}

    Args:
        base_url: Langfuse API base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key
        batch: List of batch event objects (trace-create/update, generation-create/update)
        timeout: Request timeout in seconds (default: 2)

    Returns:
        True if successful, False if failed (graceful failure)
    """
    try:
        # Use ingestion API endpoint for batch events
        ingestion_url = f"{base_url.rstrip('/')}/api/public/ingestion"

        # Wrap batch in required structure
        payload = {"batch": batch}

        # Submit batch
        response = requests.post(
            ingestion_url,
            json=payload,
            auth=(public_key, secret_key),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code in (200, 201, 202, 207):
            log_info(
                "langfuse_push", f"Successfully pushed batch with {len(batch)} events"
            )
            return True
        else:
            log_warning(
                "langfuse_push",
                f"Failed to push batch: HTTP {response.status_code}",
                None,
            )
            return False

    except requests.exceptions.Timeout:
        log_warning("langfuse_push", f"Batch push timed out after {timeout}s", None)
        return False
    except requests.exceptions.ConnectionError:
        log_warning("langfuse_push", "Unable to reach Langfuse API", None)
        return False
    except Exception as e:
        log_warning("langfuse_push", f"Batch push failed: {str(e)}", e)
        return False
