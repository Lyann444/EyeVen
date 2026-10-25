import json
import logging
import re
import time

import requests
from flask import current_app
from jsonschema import ValidationError, validate as jsonschema_validate

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 30000
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 300


class GlmServiceError(Exception):
    pass


class GlmMalformedOutputError(GlmServiceError):
    pass


class GlmSchemaValidationError(GlmServiceError):
    pass


class GlmApiError(GlmServiceError):
    pass


class GlmService:
    @staticmethod
    def is_retryable_error(error: Exception) -> bool:
        if not isinstance(error, GlmApiError):
            return False
        message = str(error).lower()
        return "timeout" in message or bool(
            re.search(r"\b(?:429|5\d\d)\b", message)
        )

    @staticmethod
    def call_glm(
        prompt: str,
        system_message: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_schema: dict | None = None,
        retry_on_failure: bool = True,
    ) -> dict:
        if len(prompt) > MAX_CONTEXT_CHARS:
            logger.warning(
                "Prompt exceeds %d chars (%d), truncating",
                MAX_CONTEXT_CHARS, len(prompt),
            )
            prompt = prompt[:MAX_CONTEXT_CHARS]

        default_system = (
            "You are a structured data extraction and reasoning engine for an enterprise "
            "vendor onboarding platform. You must always return valid JSON matching the "
            "schema provided. Never invent vendor facts. Mark unknown values explicitly. "
            "Distinguish between missing, conflicting, unverifiable, and low_confidence."
        )
        system_msg = system_message or default_system

        payload = {
            "model": current_app.config["GLM_MODEL"],
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_schema:
            payload["response_format"] = {"type": "json_object"}

        result = GlmService._make_request(payload, retry_on_failure)

        parsed = GlmService._parse_json_response(result)

        if response_schema:
            # Normalize canonical form output before schema validation.
            if response_schema.get("properties", {}).get("fields"):
                parsed = GlmService._normalize_canonical_form(parsed)
            if response_schema.get("properties", {}).get("critical_mismatches"):
                parsed = GlmService._normalize_verification_mismatches(parsed)

            GlmService._validate_schema(parsed, response_schema)

        return parsed

    @staticmethod
    def _make_request(payload: dict, retry: bool) -> str:
        url = current_app.config["GLM_API_URL"]
        api_key = current_app.config["GLM_API_KEY"]

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        attempts = 2 if retry else 1
        last_error = None

        for attempt in range(attempts):
            try:
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("Raw GLM response: %s", json.dumps(data)[:1000])

                choice = data.get("choices", [{}])[0]
                logger.info(
    "LLM finish_reason=%s, usage=%s",
    choice.get("finish_reason"),
    data.get("usage"),
)
                message = choice.get("message", {})
                content = message.get("content")

                if content is None:
                    # Some OpenAI-compatible providers may return tool_calls, reasoning_content,
                    # or other fields instead of message.content.
                    content = message.get("reasoning_content") or choice.get("text")

                if not content:
                    raise GlmMalformedOutputError(
                        f"GLM response content is empty or null. Raw response: {json.dumps(data)[:1000]}"
                    )

                logger.info("GLM response received (attempt %d)", attempt + 1)
                return content
            except requests.exceptions.HTTPError as e:
                last_error = GlmApiError(f"GLM API HTTP error: {e}")
                logger.error("GLM API HTTP error (attempt %d): %s", attempt + 1, e)
            except requests.exceptions.Timeout as e:
                last_error = GlmApiError(f"GLM API timeout: {e}")
                logger.error("GLM API timeout (attempt %d): %s", attempt + 1, e)
            except (KeyError, IndexError) as e:
                last_error = GlmMalformedOutputError(f"GLM response missing expected fields: {e}")
                logger.error("GLM response structure error (attempt %d): %s", attempt + 1, e)
            except json.JSONDecodeError as e:
                last_error = GlmMalformedOutputError(f"GLM response not valid JSON: {e}")
                logger.error("GLM JSON decode error (attempt %d): %s", attempt + 1, e)

            if attempt < attempts - 1:
                logger.info("Retrying GLM call in %ds", RETRY_DELAY_SECONDS)
                time.sleep(RETRY_DELAY_SECONDS)

        raise last_error or GlmApiError("GLM API call failed")

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        if not raw:
            raise GlmMalformedOutputError("GLM returned empty response content")

        text = raw.strip()
        if text.startswith("```json"):
            text = text[len("```json"):]
        if text.startswith("```"):
            text = text[len("```"):]
        if text.endswith("```"):
            text = text[:-len("```")]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise GlmMalformedOutputError(f"Could not parse GLM output as JSON: {e}") from e
    @staticmethod
    def _normalize_canonical_form(data: dict) -> dict:
        for field in data.get("fields", []):
            evidence = field.get("evidence_reference")
            if isinstance(evidence, dict):
                source_type = evidence.get("source_type")
                if not isinstance(source_type, str) or not source_type.strip():
                    evidence["source_type"] = "unknown"

        return data

    @staticmethod
    def _normalize_verification_mismatches(data: dict) -> dict:
        for key in ("minor_mismatches", "moderate_mismatches", "critical_mismatches"):
            mismatches = data.get(key)
            if not isinstance(mismatches, list):
                continue

            data[key] = [
                {
                    "field_name": item,
                    "description": "Mismatch reported without additional details.",
                }
                if isinstance(item, str)
                else item
                for item in mismatches
            ]

        return data

    @staticmethod
    def _validate_schema(data: dict, schema: dict) -> None:
        try:
            jsonschema_validate(instance=data, schema=schema)
        except ValidationError as e:
            field_path = ".".join(str(part) for part in e.absolute_path)
            logger.error(
                "LLM schema validation failed at '%s': %s",
                field_path or "<root>",
                e.message,
            )
            raise GlmSchemaValidationError(
                f"GLM output failed schema validation at "
                f"'{field_path or '<root>'}': {e.message}"
            ) from e
