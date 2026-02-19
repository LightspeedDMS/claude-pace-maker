#!/usr/bin/env python3
"""
Langfuse trace push functionality.

Handles submission of traces to Langfuse API with timeout and error handling.
"""

import json
import copy
import requests
from typing import Dict, Any, List, Tuple

from ..logger import log_warning, log_info


# Maximum payload size in bytes for Langfuse Cloud ingestion API.
# Langfuse Cloud enforces a 1MB body limit on POST /api/public/ingestion.
# We use 900KB to stay safely under the limit after HTTP overhead.
MAX_BATCH_PAYLOAD_BYTES = 900_000

# Minimum number of characters to preserve when truncating a field.
# Even after aggressive truncation, keep at least this many chars of content.
MIN_TRUNCATED_FIELD_CHARS = 100

# Maximum chars per field during aggressive (second-pass) truncation.
# Used when first-pass proportional truncation is insufficient.
AGGRESSIVE_TRUNCATION_CHARS = 1000


def _truncate_batch_to_fit(payload: dict, max_bytes: int) -> dict:
    """
    Progressively truncate string fields in batch event bodies to fit under max_bytes.

    Finds all string fields named 'input', 'output', or 'text' in batch event bodies
    and truncates them largest-first until the serialized payload fits under the limit.

    Args:
        payload: Dict with 'batch' key containing list of event dicts.
        max_bytes: Maximum serialized size in bytes.

    Returns:
        Payload dict with truncated string fields (may be a deep copy if modified).
    """
    serialized = json.dumps(payload)
    if len(serialized.encode("utf-8")) <= max_bytes:
        return payload

    # Deep copy to avoid mutating the original
    payload = copy.deepcopy(payload)

    # Collect all truncatable string fields: (event_index, field_name, current_length)
    truncatable_fields: List[Tuple[int, str, int]] = []
    target_field_names = {"input", "output", "text"}

    for idx, event in enumerate(payload.get("batch", [])):
        body = event.get("body", {})
        if not isinstance(body, dict):
            continue
        for field_name in target_field_names:
            if field_name in body and isinstance(body[field_name], str):
                truncatable_fields.append((idx, field_name, len(body[field_name])))

    if not truncatable_fields:
        # No string fields to truncate - return as-is
        return payload

    # Sort by length descending (truncate largest first)
    truncatable_fields.sort(key=lambda x: x[2], reverse=True)

    # Iteratively truncate the largest field until we fit
    for event_idx, field_name, original_length in truncatable_fields:
        serialized = json.dumps(payload)
        current_size = len(serialized.encode("utf-8"))
        if current_size <= max_bytes:
            break

        # Calculate how much we need to cut
        excess = current_size - max_bytes
        body = payload["batch"][event_idx]["body"]
        current_value = body[field_name]
        current_len = len(current_value)

        # Target length: cut the excess plus some margin for the truncation marker
        marker = (
            f"\n\n... [TRUNCATED - original size: {current_len} chars, "
            f"limit: {current_len - excess} chars]"
        )
        target_len = current_len - excess - len(marker) - MIN_TRUNCATED_FIELD_CHARS

        if target_len < MIN_TRUNCATED_FIELD_CHARS:
            target_len = MIN_TRUNCATED_FIELD_CHARS

        truncated = current_value[:target_len] + marker
        body[field_name] = truncated

        log_warning(
            "langfuse_push",
            f"Truncated batch field '{field_name}' from {current_len} to {len(truncated)} chars "
            f"(payload was {current_size} bytes, limit {max_bytes} bytes)",
            None,
        )

    # Final check - if still over, do more aggressive truncation
    serialized = json.dumps(payload)
    current_size = len(serialized.encode("utf-8"))
    if current_size > max_bytes:
        for event_idx, field_name, original_length in truncatable_fields:
            body = payload["batch"][event_idx]["body"]
            if not isinstance(body.get(field_name), str):
                continue
            current_value = body[field_name]
            if len(current_value) > AGGRESSIVE_TRUNCATION_CHARS:
                marker = (
                    f"\n\n... [TRUNCATED - original size: {original_length} chars, "
                    f"limit: {AGGRESSIVE_TRUNCATION_CHARS} chars]"
                )
                body[field_name] = current_value[:AGGRESSIVE_TRUNCATION_CHARS] + marker

    return payload


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
) -> tuple[bool, int]:
    """
    Push batch events to Langfuse ingestion API.

    Langfuse ingestion API expects batch array with event objects:
    POST /api/public/ingestion
    Body: {"batch": [event1, event2, ...]}

    IMPORTANT: Langfuse returns HTTP 200 even when items fail. The actual
    success/failure status is in the response body:
    {"successes": [...], "errors": [...]}

    Args:
        base_url: Langfuse API base URL
        public_key: Langfuse public key
        secret_key: Langfuse secret key
        batch: List of batch event objects (trace-create/update, generation-create/update)
        timeout: Request timeout in seconds (default: 2)

    Returns:
        Tuple of (success: bool, count: int) where:
        - success: True if at least one item succeeded, False if all failed
        - count: Number of items that actually succeeded (0 if all failed)
    """
    try:
        # Use ingestion API endpoint for batch events
        ingestion_url = f"{base_url.rstrip('/')}/api/public/ingestion"

        # Wrap batch in required structure
        payload = {"batch": batch}

        # Validate payload size and truncate if necessary
        serialized_size = len(json.dumps(payload).encode("utf-8"))
        if serialized_size > MAX_BATCH_PAYLOAD_BYTES:
            log_warning(
                "langfuse_push",
                f"Batch payload size {serialized_size} bytes exceeds limit "
                f"{MAX_BATCH_PAYLOAD_BYTES} bytes, truncating",
                None,
            )
            payload = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        # Submit batch
        response = requests.post(
            ingestion_url,
            json=payload,
            auth=(public_key, secret_key),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code in (200, 201, 202, 207):
            # Parse response body to check actual success/failure
            try:
                result = response.json()
                successes = result.get("successes", [])
                errors = result.get("errors", [])
                success_count = len(successes)

                # Log errors if any
                if errors:
                    log_warning(
                        "langfuse_push",
                        f"Batch had {len(errors)} errors: {errors[:2]}",
                        None,
                    )

                # Check if at least one item succeeded OR batch was empty
                if success_count > 0:
                    log_info(
                        "langfuse_push",
                        f"Successfully pushed {success_count}/{len(batch)} events",
                    )
                    return True, success_count
                elif len(batch) == 0:
                    # Empty batch is not an error
                    return True, 0
                else:
                    log_warning(
                        "langfuse_push",
                        f"All {len(batch)} events failed",
                        None,
                    )
                    return False, 0
            except (ValueError, KeyError) as e:
                log_warning(
                    "langfuse_push",
                    f"Failed to parse response: {e}",
                    e,
                )
                return False, 0
        else:
            log_warning(
                "langfuse_push",
                f"Failed to push batch: HTTP {response.status_code}",
                None,
            )
            return False, 0

    except requests.exceptions.Timeout:
        log_warning("langfuse_push", f"Batch push timed out after {timeout}s", None)
        return False, 0
    except requests.exceptions.ConnectionError:
        log_warning("langfuse_push", "Unable to reach Langfuse API", None)
        return False, 0
    except Exception as e:
        log_warning("langfuse_push", f"Batch push failed: {str(e)}", e)
        return False, 0
