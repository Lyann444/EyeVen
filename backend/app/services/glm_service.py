import json
import logging
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
            GlmService._coerce_null_strings(parsed, response_schema)
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
        except json.JSONDecodeError:
            repaired = GlmService._repair_truncated_json(text)
            if repaired is not None:
                return repaired
            raise GlmMalformedOutputError(
                f"Could not parse GLM output as JSON (also failed truncated repair). "
                f"First 200 chars: {text[:200]}"
            )

    @staticmethod
    def _repair_truncated_json(text: str) -> dict | None:
        """Try to salvage a valid JSON object from a truncated response."""
        # Find the outermost object
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False
        end = -1

        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        # No closing brace found — truncate to last complete value and close up
        # Close any open strings, arrays, objects
        suffix = ""
        if in_string:
            suffix += '"'
        # Count unclosed brackets
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                escape_next = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c in ("{", "["):
                depth += 1
            elif c in ("}", "]"):
                depth -= 1

        # Remove trailing incomplete key-value (e.g. "notes": "some unfinis)
        truncated = text[start:]
        # Find the last complete key-value pair before the truncation point
        last_comma = truncated.rfind(",")
        last_colon = truncated.rfind(":")
        # If colon appears after last comma, we're mid-value — cut back to last comma
        if last_colon > last_comma and last_comma > 0:
            truncated = truncated[:last_comma]

        suffix += "}" * max(depth, 0)

        try:
            return json.loads(truncated + suffix)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _coerce_null_strings(data, schema) -> None:
        """Replace null values with empty string where schema requires a string type."""
        if not isinstance(data, dict) or not isinstance(schema, dict):
            return

        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            if key not in data:
                continue

            value = data[key]
            if value is None:
                allowed = prop_schema.get("type", [])
                if isinstance(allowed, str):
                    allowed = [allowed]
                if "string" in allowed and "null" not in allowed:
                    data[key] = ""
                    continue

            # Recurse into nested objects
            if isinstance(value, dict) and isinstance(prop_schema, dict):
                GlmService._coerce_null_strings(value, prop_schema)

            # Recurse into array items
            if isinstance(value, list) and isinstance(prop_schema, dict):
                item_schema = prop_schema.get("items")
                if item_schema and isinstance(item_schema, dict):
                    for item in value:
                        if isinstance(item, dict):
                            GlmService._coerce_null_strings(item, item_schema)

    @staticmethod
    def _validate_schema(data: dict, schema: dict) -> None:
        try:
            jsonschema_validate(instance=data, schema=schema)
        except ValidationError as e:
            raise GlmSchemaValidationError(
                f"GLM output failed schema validation: {e.message}"
            ) from e
