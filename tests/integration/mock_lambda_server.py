#!/usr/bin/env python3
"""Mock Lambda provisioning server for integration tests."""
from flask import Flask, request, jsonify
import threading
import time

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "mode": "mock"})


@app.route("/provision", methods=["POST"])
def provision():
    """Mock provisioning endpoint."""
    data = request.json

    # Validate required fields
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    required_fields = ["oauthToken", "adminApiKey", "userEmail"]
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    # Return mock provisioned keys
    return jsonify(
        {
            "publicKey": "pk-mock-test",
            "secretKey": "sk-mock-test",
            "host": "http://mock.langfuse.local",
        }
    )


def start_mock_server(port=3000, wait_for_ready=True):
    """
    Start mock server in background thread.

    Args:
        port: Port to listen on
        wait_for_ready: If True, wait until server is ready before returning

    Returns:
        Thread object running the server
    """
    thread = threading.Thread(
        target=lambda: app.run(port=port, debug=False, use_reloader=False)
    )
    thread.daemon = True
    thread.start()

    if wait_for_ready:
        # Wait for server to be ready
        import requests

        max_attempts = 50
        for _ in range(max_attempts):
            try:
                requests.get(f"http://localhost:{port}/health", timeout=0.1)
                break
            except (requests.exceptions.RequestException, Exception):
                time.sleep(0.1)

    return thread


if __name__ == "__main__":
    print("Starting mock Lambda server on http://localhost:3000")
    start_mock_server(port=3000, wait_for_ready=False)
    print("Mock server running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down mock server.")
