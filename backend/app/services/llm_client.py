from __future__ import annotations

import json
import re
import time

from openai import OpenAI


class LLMClient:
    def __init__(
        self,
        model_name: str = "stub",
        api_key: str | None = None,
        api_base: str | None = None,
        timeout_seconds: int = 20,
        max_retries: int = 2,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.api_base = api_base
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.client = None
        if api_key and model_name != "stub":
            self.client = OpenAI(api_key=api_key, base_url=api_base)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def generate_query_plan_hint(self, prompt_payload: dict) -> dict:
        if not self.enabled:
            return {
                "mode": "stub",
                "model": self.model_name,
                "note": "LLM is not connected; heuristic planner is active.",
                "task": prompt_payload.get("task"),
            }

        system_prompt = (
            "You are a Text2SQL planner. Return only compact JSON. "
            "Do not add markdown. Keep only fields you are confident about."
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self._complete(messages)
                parsed = self._extract_json(content)
                if parsed:
                    parsed["mode"] = "live"
                    parsed["model"] = self.model_name
                    parsed["attempt"] = attempt
                    return parsed
                if attempt < self.max_retries:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                        }
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": "Return valid JSON only. Remove all prose and markdown fences.",
                        }
                    )
            except Exception as exc:
                if attempt >= self.max_retries:
                    return {
                        "mode": "stub",
                        "model": self.model_name,
                        "note": f"LLM call failed, fallback to heuristic planner: {exc}",
                        "task": prompt_payload.get("task"),
                    }
                time.sleep(min(0.4 * attempt, 1.0))

        return {
            "mode": "stub",
            "model": self.model_name,
            "note": "LLM returned non-JSON content, fallback to heuristic planner.",
            "task": prompt_payload.get("task"),
        }

    def generate_classification_hint(self, prompt_payload: dict) -> dict:
        if not self.enabled:
            return {
                "mode": "stub",
                "model": self.model_name,
                "note": "LLM is not connected; structured classifier is active.",
                "task": prompt_payload.get("task"),
            }

        system_prompt = (
            "You classify user questions in a Text2SQL conversation. "
            "Return only compact JSON. Do not add markdown or prose. "
            "Only choose values explicitly allowed by the prompt."
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self._complete(messages)
                parsed = self._extract_json(content)
                if parsed:
                    parsed["mode"] = "live"
                    parsed["model"] = self.model_name
                    parsed["attempt"] = attempt
                    return parsed
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Return valid JSON only and keep only the requested fields.",
                        }
                    )
            except Exception as exc:
                if attempt >= self.max_retries:
                    return {
                        "mode": "stub",
                        "model": self.model_name,
                        "note": f"LLM call failed, fallback to structured classifier: {exc}",
                        "task": prompt_payload.get("task"),
                    }
                time.sleep(min(0.4 * attempt, 1.0))

        return {
            "mode": "stub",
            "model": self.model_name,
            "note": "LLM returned non-JSON content, fallback to structured classifier.",
            "task": prompt_payload.get("task"),
        }

    def generate_sql_hint(self, prompt_payload: dict) -> str | None:
        if not self.enabled:
            return None

        system_prompt = (
            "You generate readonly SQL for MySQL. Return SQL only, without markdown, comments, or explanations."
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self._complete(messages).strip()
                sql = self._extract_sql(content)
                if sql and self._is_readonly_select(sql):
                    return sql
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Return exactly one readonly SELECT statement with LIMIT. No explanation.",
                        }
                    )
            except Exception:
                if attempt >= self.max_retries:
                    return None
                time.sleep(min(0.4 * attempt, 1.0))
        return None

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model_name,
            "api_base": self.api_base,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
        }

    def _complete(self, messages: list[dict]) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.1,
            timeout=self.timeout_seconds,
        )
        return response.choices[0].message.content or ""

    def _extract_json(self, content: str) -> dict:
        content = content.strip()
        if not content:
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                return {}
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}

    def _extract_sql(self, content: str) -> str | None:
        if not content:
            return None
        fence_match = re.search(r"```(?:sql)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if fence_match:
            content = fence_match.group(1).strip()
        statements = [item.strip() for item in re.split(r";\s*", content) if item.strip()]
        if len(statements) != 1:
            return None
        return statements[0] + ";"

    def _is_readonly_select(self, sql: str) -> bool:
        normalized = f" {sql.lower()} "
        if not normalized.strip().startswith("select"):
            return False
        forbidden = (" insert ", " update ", " delete ", " drop ", " alter ", " truncate ", " create ")
        if any(keyword in normalized for keyword in forbidden):
            return False
        return " limit " in normalized
