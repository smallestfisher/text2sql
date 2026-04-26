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
            "你是一个 Text2SQL 规划器。只返回紧凑 JSON。"
            "不要输出 markdown。只保留你有把握的字段。"
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
                            "content": "只返回合法 JSON，去掉所有解释性文字和 markdown 代码块。",
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
            "你是一个用于 Text2SQL 会话分类的裁决模型。"
            "不要脱离现有结构化候选从零随意重分类，而是根据 prompt 中给出的本地候选和证据做裁决。"
            "你的任务是选出最连贯、最符合约束的分类；如果选择 follow_up，还要生成最小可执行的 context_delta。"
            "只返回紧凑 JSON，不要输出 markdown 或额外解释。只能选择 prompt 明确允许的取值。"
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
                            "content": "只返回合法 JSON，并且只保留要求的字段。",
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

    def check_question_relevance(self, prompt_payload: dict) -> dict:
        if not self.enabled:
            return {
                "mode": "stub",
                "model": self.model_name,
                "note": "LLM is not connected; relevance guard is disabled.",
                "task": prompt_payload.get("task"),
            }

        system_prompt = (
            "你是一个 Text2SQL 系统的相关性守卫模型。"
            "判断用户输入是否属于应该继续留在 SQL 工作流中的业务数据查询或业务追问。"
            "如果它是业务数据请求，只是信息不完整，也应继续留在范围内。"
            "只返回紧凑 JSON，不要输出 markdown 或额外解释。"
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
                            "content": "只返回合法 JSON，并且只保留要求的字段。",
                        }
                    )
            except Exception as exc:
                if attempt >= self.max_retries:
                    return {
                        "mode": "stub",
                        "model": self.model_name,
                        "note": f"LLM call failed, relevance guard skipped: {exc}",
                        "task": prompt_payload.get("task"),
                    }
                time.sleep(min(0.4 * attempt, 1.0))

        return {
            "mode": "stub",
            "model": self.model_name,
            "note": "LLM returned non-JSON content, relevance guard skipped.",
            "task": prompt_payload.get("task"),
        }

    def generate_sql_hint(self, prompt_payload: dict) -> str | None:
        if not self.enabled:
            return None

        system_prompt = (
            "你是 MySQL 场景下的主 Text2SQL 生成器。"
            "只能使用用户 prompt 中提供的真实数据库表和字段，生成一条可执行的只读 SQL。"
            "只返回 SQL，不要输出 markdown、注释或解释。"
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
                            "content": "精确返回一条只读 SELECT 或 WITH ... SELECT 语句，并且必须带 LIMIT。不要解释。",
                        }
                    )
            except Exception:
                if attempt >= self.max_retries:
                    return None
                time.sleep(min(0.4 * attempt, 1.0))
        return None

    def repair_sql(self, prompt_payload: dict, sql: str, errors: list[str], warnings: list[str]) -> str | None:
        if not self.enabled:
            return None

        repair_payload = {
            "task": "sql_repair",
            "original_prompt": prompt_payload,
            "sql": sql,
            "errors": errors,
            "warnings": warnings,
            "instructions": {
                "return_format": "sql_only",
                "constraints": [
                    "只能基于原始 prompt 上下文修复 SQL。",
                    "精确返回一条只读 SELECT 或 WITH ... SELECT 语句。",
                    "不要输出 markdown 或解释。",
                    "必须包含 LIMIT。",
                ],
            },
        }
        system_prompt = (
            "你负责修复 MySQL Text2SQL 的输出。只返回一条修正后的只读 SQL 语句。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
        ]
        for attempt in range(1, self.max_retries + 1):
            try:
                content = self._complete(messages).strip()
                repaired = self._extract_sql(content)
                if repaired and self._is_readonly_select(repaired):
                    return repaired
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": "精确返回一条合法的只读 SQL 语句。不要额外文字。",
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
        stripped = normalized.strip()
        if not (stripped.startswith("select") or stripped.startswith("with")):
            return False
        forbidden = (" insert ", " update ", " delete ", " drop ", " alter ", " truncate ", " create ")
        if any(keyword in normalized for keyword in forbidden):
            return False
        return " limit " in normalized
