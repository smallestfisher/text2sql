from __future__ import annotations

import json
import re

from openai import OpenAI


class LLMClient:
    def __init__(
        self,
        model_name: str = "stub",
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.api_base = api_base
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
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
            )
            content = response.choices[0].message.content or "{}"
            parsed = self._extract_json(content)
            parsed["mode"] = "live"
            parsed["model"] = self.model_name
            return parsed
        except Exception as exc:
            return {
                "mode": "stub",
                "model": self.model_name,
                "note": f"LLM call failed, fallback to heuristic planner: {exc}",
                "task": prompt_payload.get("task"),
            }

    def generate_sql_hint(self, prompt_payload: dict) -> str | None:
        if not self.enabled:
            return None

        system_prompt = (
            "You generate readonly SQL for MySQL. Return SQL only, without markdown, comments, or explanations."
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False)
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
            )
            content = (response.choices[0].message.content or "").strip()
            return self._extract_sql(content)
        except Exception:
            return None

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model_name,
            "api_base": self.api_base,
        }

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
        return content
