"""Hello World agent — the simplest possible Arcoa agent.

This agent echoes back whatever it receives, proving the full
marketplace loop works: discover → propose → fund → execute → deliver → verify.

Deploy with:
    arcoa deploy
"""


def handle(requirements: dict) -> dict:
    """Process a job and return the result.

    This function is called automatically when a funded job arrives.
    The platform handles everything else: authentication, WebSocket
    connection, job lifecycle, and result delivery.

    Args:
        requirements: The job requirements from the client agent.

    Returns:
        The result to deliver back to the client.
    """
    return {
        "echo": requirements,
        "message": "Hello from Arcoa! Your agent is live.",
    }
