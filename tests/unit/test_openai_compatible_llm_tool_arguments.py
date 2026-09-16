import json

import httpx2
from pydantic import SecretStr

from jobs_status_manager.agent.contracts import ConversationPrompt, ToolCallRequest
from jobs_status_manager.infrastructure.adapters.openai_compatible_llm import OpenAICompatibleLLM


def test_conversation_preserves_model_specific_invalid_scalar_tool_arguments() -> None:
    arguments: dict[str, str | int | bool | None] = {
        "company": "小米",
        "position": "软件研发工程师",
        "status": "INTERVIEW",
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "production-shaped-call",
                                    "type": "function",
                                    "function": {
                                        "name": "UpdateApplicationStatus",
                                        "arguments": json.dumps(
                                            arguments, ensure_ascii=False, separators=(",", ":")
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        adapter = OpenAICompatibleLLM(
            "https://llm.example.test",
            SecretStr("secret"),
            client=client,
        )
        result = adapter.converse(ConversationPrompt(user_message="新增求职状态"))

    assert result.tool_calls == (
        ToolCallRequest(
            provider_call_id="production-shaped-call",
            provider_type="function",
            name="UpdateApplicationStatus",
            arguments=arguments,
            arguments_json=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        ),
    )
