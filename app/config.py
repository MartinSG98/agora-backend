"""Central configuration: hard limits and the model registry.

Design principle: hard limits are enforced in code by the orchestrator and
the agent tool loop — never as prompt instructions. An LLM will eventually
ignore a prompt; it cannot ignore code.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class HardLimits:
    max_rebuttal_rounds: int = 2          # ceiling; a format may request fewer
    max_response_tokens: int = 600        # per statement, via Bedrock inferenceConfig
    max_evidence_requests_per_phase: int = 3   # evidence tool calls per debater per phase
    max_tool_loop_iterations: int = 6     # hard stop for the agent tool-use loop
    judge_retries: int = 1                # re-ask once on schema-invalid judge output


# Friendly name -> Bedrock model id for the Converse API.
# Cross-region inference profiles ("us." prefix); cheap models by default.
# Adjust to what your account has enabled: `aws bedrock list-inference-profiles`.
MODEL_REGISTRY: dict[str, str] = {
    # instantly accessible (no access-request form)
    "nova-micro": "us.amazon.nova-micro-v1:0",
    "nova-lite": "us.amazon.nova-lite-v1:0",
    "nova-pro": "us.amazon.nova-pro-v1:0",
    # one-click EULA acceptance in the Bedrock console
    "llama-scout": "us.meta.llama4-scout-17b-instruct-v1:0",
    "llama-maverick": "us.meta.llama4-maverick-17b-instruct-v1:0",
    "llama-70b": "us.meta.llama3-3-70b-instruct-v1:0",
    # requires the Bedrock use-case access request to be approved
    "claude-haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "mock": "mock",  # deterministic provider, no AWS required
}

# role -> friendly model name; overridable per debate via the API.
# Defaults use only no-form models, put two different vendors head to head
# (ADR 0004), and spend by role: cheapest where the job is mechanical
# (fact-checker), mid-tier for the debaters, the strongest cheap model for
# the judge — evaluation quality is the product. ~ $0.02 per live debate.
DEFAULT_MODELS: dict[str, str] = {
    "debater_pro": "nova-lite",
    "debater_con": "llama-scout",
    "judge": "nova-pro",
    "fact_checker": "nova-micro",
}

DEFAULT_FORMAT = "oxford"


@dataclass
class Settings:
    mock_mode: bool
    aws_region: str
    db_path: Path
    mcp_servers_dir: Path
    limits: HardLimits = field(default_factory=HardLimits)


def get_settings() -> Settings:
    """Read settings from the environment.

    Mock mode is the default so a fresh clone runs with zero AWS cost;
    set AGORA_MOCK_MODE=0 to use real Bedrock models.
    """
    return Settings(
        mock_mode=os.environ.get("AGORA_MOCK_MODE", "1") != "0",
        aws_region=os.environ.get("AGORA_AWS_REGION", "us-east-1"),
        db_path=Path(os.environ.get("AGORA_DB_PATH", str(REPO_ROOT / "agora.db"))),
        mcp_servers_dir=Path(
            os.environ.get("AGORA_MCP_SERVERS_DIR", str(REPO_ROOT / "mcp-servers"))
        ),
    )


def resolve_model(friendly_name: str) -> str:
    if friendly_name not in MODEL_REGISTRY:
        raise KeyError(
            f"unknown model '{friendly_name}'; available: {sorted(MODEL_REGISTRY)}"
        )
    return MODEL_REGISTRY[friendly_name]
