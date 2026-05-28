from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import logging
import re
import time

from openai import OpenAI

from backend.app.core.cancellation import CancellationToken
from backend.app.core.exceptions import LLMServiceError
from backend.app.services.sql_dialect import SqlDialect

try:
    import sqlglot
except Exception:  # pragma: no cover - optional dependency
    sqlglot = None


logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-14B",
        api_key: str | None = None,
        api_base: str | None = None,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        repair_max_retries: int | None = None,
        cache_ttl_seconds: int = 300,
        cache_max_entries: int = 256,
    ) -> None:
        if sqlglot is None:
            raise RuntimeError("sqlglot is required for LLM SQL validation helpers")
        self.model_name = model_name
        self.api_key = api_key
        self.api_base = api_base
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.repair_max_retries = max(1, repair_max_retries if repair_max_retries is not None else max_retries)
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self.cache_max_entries = max(0, cache_max_entries)
        self._response_cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._metrics: dict[str, dict[str, int]] = {}
        self.sql_dialect = SqlDialect.from_name("oracle")
        self.client = None
        if api_key:
            self.client = OpenAI(api_key=api_key, base_url=api_base)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def generate_classification_hint(
        self,
        prompt_payload: dict,
        cancellation_token: CancellationToken | None = None,
    ) -> dict:
        self._require_enabled("classification generation")

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
        self._record_metric("classification", "requests")
        cache_key = self._cache_key("classification", messages)
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            self._record_metric("classification", "cache_hits")
            cached_response = deepcopy(cached)
            cached_response["cache_hit"] = True
            return cached_response
        for attempt in range(1, self.max_retries + 1):
            self._raise_if_cancelled(cancellation_token, stage="classification generation")
            try:
                content = self._complete(messages, task_name="classification")
                self._raise_if_cancelled(cancellation_token, stage="classification generation")
                parsed = self._extract_json(content)
                if parsed:
                    parsed["mode"] = "live"
                    parsed["model"] = self.model_name
                    parsed["attempt"] = attempt
                    self._cache_put(cache_key, parsed)
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
                    raise LLMServiceError(
                        f"llm call failed during classification generation: {exc}"
                    ) from exc
                time.sleep(min(0.4 * attempt, 1.0))

        raise LLMServiceError("llm returned invalid JSON during classification generation")

    def generate_intent(
        self,
        prompt_payload: dict,
        cancellation_token: CancellationToken | None = None,
    ) -> dict:
        self._require_enabled("intent generation")

        system_prompt = (
            "你是一个 Text2SQL 意图理解器。"
            "基于 question、shallow_signals、session_focus 和 domain_hints，补全 prompt 要求的结构化 intent 字段。"
            "不要输出分类阶段负责的 question_type 或 inherit_context。"
            "只返回紧凑 JSON，不要输出 markdown 或额外解释。"
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        self._record_metric("intent", "requests")
        cache_key = self._cache_key("intent", messages)
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            self._record_metric("intent", "cache_hits")
            cached_response = deepcopy(cached)
            cached_response["cache_hit"] = True
            return cached_response
        for attempt in range(1, self.max_retries + 1):
            self._raise_if_cancelled(cancellation_token, stage="intent generation")
            try:
                content = self._complete(messages, task_name="intent")
                self._raise_if_cancelled(cancellation_token, stage="intent generation")
                parsed = self._extract_json(content)
                if parsed:
                    parsed["mode"] = "live"
                    parsed["model"] = self.model_name
                    parsed["attempt"] = attempt
                    self._cache_put(cache_key, parsed)
                    return parsed
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": "只返回合法 JSON，并且只保留 prompt 要求的字段。",
                        }
                    )
            except Exception as exc:
                if attempt >= self.max_retries:
                    raise LLMServiceError(
                        f"llm call failed during intent generation: {exc}"
                    ) from exc
                time.sleep(min(0.4 * attempt, 1.0))

        raise LLMServiceError("llm returned invalid JSON during intent generation")

    def check_question_relevance(
        self,
        prompt_payload: dict,
        cancellation_token: CancellationToken | None = None,
    ) -> dict:
        self._require_enabled("relevance guard")

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
        self._record_metric("relevance", "requests")
        cache_key = self._cache_key("relevance", messages)
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            self._record_metric("relevance", "cache_hits")
            cached_response = deepcopy(cached)
            cached_response["cache_hit"] = True
            return cached_response
        for attempt in range(1, self.max_retries + 1):
            self._raise_if_cancelled(cancellation_token, stage="relevance guard")
            try:
                content = self._complete(messages, task_name="relevance")
                self._raise_if_cancelled(cancellation_token, stage="relevance guard")
                parsed = self._extract_json(content)
                if parsed:
                    parsed["mode"] = "live"
                    parsed["model"] = self.model_name
                    parsed["attempt"] = attempt
                    self._cache_put(cache_key, parsed)
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
                    raise LLMServiceError(
                        f"llm call failed during relevance guard: {exc}"
                    ) from exc
                time.sleep(min(0.4 * attempt, 1.0))

        raise LLMServiceError("llm returned invalid JSON during relevance guard")

    def generate_sql_hint(
        self,
        prompt_payload: dict,
        cancellation_token: CancellationToken | None = None,
    ) -> str:
        self._require_enabled("sql generation")

        system_prompt = (
            f"你是 {self.sql_dialect.label} 场景下的主 Text2SQL 生成器。"
            "只能使用用户 prompt 中提供的真实数据库表和字段，生成一条可执行的只读 SQL。"
            "只返回 SQL，不要输出 markdown、注释或解释。"
        )
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        self._record_metric("sql", "requests")
        cache_key = self._cache_key("sql", messages)
        cached = self._cache_get(cache_key)
        if isinstance(cached, str):
            self._record_metric("sql", "cache_hits")
            return cached
        for attempt in range(1, self.max_retries + 1):
            self._raise_if_cancelled(cancellation_token, stage="sql generation")
            try:
                content = self._complete(messages, task_name="sql").strip()
                self._raise_if_cancelled(cancellation_token, stage="sql generation")
                sql = self._extract_sql(content)
                if sql and self._is_readonly_select(sql):
                    self._cache_put(cache_key, sql)
                    return sql
                if attempt < self.max_retries:
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "精确返回一条只读 SELECT 或 WITH ... SELECT 语句，"
                                f"并且必须带 {self.sql_dialect.result_limit_clause_name}。不要解释。"
                            ),
                        }
                    )
            except Exception as exc:
                if attempt >= self.max_retries:
                    raise LLMServiceError(
                        f"llm call failed during sql generation: {exc}"
                    ) from exc
                time.sleep(min(0.4 * attempt, 1.0))
        raise LLMServiceError("llm did not return a valid readonly SQL statement during sql generation")

    def repair_sql(
        self,
        prompt_payload: dict,
        sql: str,
        errors: list[str],
        warnings: list[str],
        *,
        repair_focus: str | None = None,
        extra_constraints: list[str] | None = None,
        extra_context: dict | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> str | None:
        if not self.enabled:
            return None

        constraints = [
            "只能基于原始 prompt 上下文修复 SQL。",
            "精确返回一条只读 SELECT 或 WITH ... SELECT 语句。",
            "必须继续满足 query_plan.tables、filters、dimensions、sort 和 limit 这些硬约束。",
            "如果 errors 指出缺失 required dimensions，先修复最终外层 SELECT 和最终外层 GROUP BY 的 shape。",
            "不要输出 markdown 或解释。",
            f"必须包含 {self.sql_dialect.result_limit_clause_name}。",
        ]
        constraints.append("不要使用 MySQL 专属语法，例如 LIMIT、DATE_FORMAT、STR_TO_DATE、DATE_ADD、CURDATE、反引号。")
        if extra_constraints:
            constraints = [*extra_constraints, *constraints]
        repair_payload = {
            "task": "sql_repair",
            "original_prompt": prompt_payload,
            "sql": sql,
            "errors": errors,
            "warnings": warnings,
            "instructions": {
                "return_format": "sql_only",
                "constraints": constraints,
            },
        }
        if repair_focus:
            repair_payload["repair_focus"] = repair_focus
        if extra_context:
            repair_payload["repair_context"] = extra_context
        system_prompt = (
            f"你负责修复 {self.sql_dialect.label} Text2SQL 的输出。"
            "只返回一条修正后的只读 SQL 语句。"
            "优先保证最终外层 SELECT / GROUP BY 的输出 shape 与 query_plan.dimensions 一致。"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)},
        ]
        self._record_metric("repair", "requests")
        for attempt in range(1, self.repair_max_retries + 1):
            self._raise_if_cancelled(cancellation_token, stage="sql repair")
            try:
                content = self._complete(messages, task_name="repair").strip()
                self._raise_if_cancelled(cancellation_token, stage="sql repair")
                repaired = self._extract_sql(content)
                if repaired and self._is_readonly_select(repaired):
                    return repaired
                if attempt < self.repair_max_retries:
                    messages.append({"role": "assistant", "content": content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "精确返回一条合法的只读 SQL 语句。若缺少 required dimensions，"
                                f"先补齐最终外层 SELECT 和 GROUP BY，并包含 {self.sql_dialect.result_limit_clause_name}。不要额外文字。"
                            ),
                        }
                    )
            except Exception:
                if attempt >= self.repair_max_retries:
                    return None
                time.sleep(min(0.4 * attempt, 1.0))
        return None

    def _raise_if_cancelled(
        self,
        cancellation_token: CancellationToken | None,
        *,
        stage: str,
    ) -> None:
        if cancellation_token is None:
            return
        cancellation_token.raise_if_cancelled(stage=stage)

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model_name,
            "api_base": self.api_base,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "repair_max_retries": self.repair_max_retries,
            "sql_dialect": self.sql_dialect.name,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "cache_max_entries": self.cache_max_entries,
            "cache_entries": len(self._response_cache),
            "metrics": deepcopy(self._metrics),
        }

    def clear_metrics(self) -> None:
        self._metrics.clear()

    def clear_cache(self) -> None:
        self._response_cache.clear()

    def _record_metric(self, task_name: str, metric_name: str, value: int = 1) -> None:
        metrics = self._metrics.setdefault(
            task_name,
            {
                "requests": 0,
                "cache_hits": 0,
                "provider_calls": 0,
                "failures": 0,
                "prompt_chars": 0,
                "response_chars": 0,
                "elapsed_ms": 0,
            },
        )
        metrics[metric_name] = metrics.get(metric_name, 0) + value

    def _cache_enabled(self) -> bool:
        return self.cache_ttl_seconds > 0 and self.cache_max_entries > 0

    def _cache_key(self, task_name: str, messages: list[dict]) -> str:
        payload = {
            "task": task_name,
            "model": self.model_name,
            "sql_dialect": self.sql_dialect.name,
            "messages": messages,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _cache_get(self, cache_key: str) -> object | None:
        if not self._cache_enabled():
            return None
        entry = self._response_cache.get(cache_key)
        if entry is None:
            return None
        cached_at, value = entry
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._response_cache.pop(cache_key, None)
            return None
        self._response_cache.move_to_end(cache_key)
        logger.info("llm cache hit model=%s key=%s", self.model_name, cache_key[:12])
        return deepcopy(value)

    def _cache_put(self, cache_key: str, value: object) -> None:
        if not self._cache_enabled():
            return
        self._response_cache[cache_key] = (time.time(), deepcopy(value))
        self._response_cache.move_to_end(cache_key)
        while len(self._response_cache) > self.cache_max_entries:
            self._response_cache.popitem(last=False)

    def _require_enabled(self, task_name: str) -> None:
        if self.enabled:
            return
        raise LLMServiceError(f"llm is required but not configured for {task_name}")

    def _complete(self, messages: list[dict], *, task_name: str = "unknown") -> str:
        started_at = time.perf_counter()
        prompt_chars = sum(len(str(message.get("content") or "")) for message in messages)
        self._record_metric(task_name, "provider_calls")
        self._record_metric(task_name, "prompt_chars", prompt_chars)
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.1,
                timeout=self.timeout_seconds,
            )
            content = response.choices[0].message.content or ""
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            self._record_metric(task_name, "response_chars", len(content))
            self._record_metric(task_name, "elapsed_ms", elapsed_ms)
            logger.info(
                "timing stage=llm.complete model=%s messages=%s prompt_chars=%s response_chars=%s elapsed_ms=%s",
                self.model_name,
                len(messages),
                prompt_chars,
                len(content),
                elapsed_ms,
            )
            return content
        except Exception:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            self._record_metric(task_name, "failures")
            self._record_metric(task_name, "elapsed_ms", elapsed_ms)
            logger.warning(
                "timing stage=llm.complete model=%s messages=%s prompt_chars=%s status=failed elapsed_ms=%s",
                self.model_name,
                len(messages),
                prompt_chars,
                elapsed_ms,
            )
            raise

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
        for candidate in self._sql_candidates(content):
            normalized = candidate.rstrip(";").strip()
            if not normalized:
                continue
            sql = normalized + ";"
            if not self._is_readonly_select(sql):
                continue
            if not self._is_single_sql_statement(sql):
                continue
            return sql
        return None

    def _is_readonly_select(self, sql: str) -> bool:
        compact = re.sub(r"\s+", " ", self._strip_sql_comments(sql).lower()).strip()
        normalized = f" {compact} "
        stripped = normalized.strip()
        if not (stripped.startswith("select") or stripped.startswith("with")):
            return False
        forbidden = (" insert ", " update ", " delete ", " drop ", " alter ", " truncate ", " create ")
        if any(keyword in normalized for keyword in forbidden):
            return False
        return self.sql_dialect.has_result_limit(compact)

    def _sql_candidates(self, content: str) -> list[str]:
        cleaned = self._strip_reasoning_markup(content).strip()
        if not cleaned:
            return []

        candidates: list[str] = []
        fence_matches = re.findall(r"```(?:sql)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
        candidates.extend(match.strip() for match in fence_matches if match.strip())

        unfenced = re.sub(
            r"```(?:sql)?\s*(.*?)```",
            lambda match: match.group(1).strip(),
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        for match in re.finditer(r"(?is)\b(?:with|select)\b", unfenced):
            remainder = unfenced[match.start():].strip()
            if not remainder:
                continue
            first_statement = self._truncate_first_statement(remainder)
            if first_statement:
                candidates.append(first_statement)
            candidates.extend(self._trimmed_line_candidates(remainder))

        if unfenced:
            candidates.append(unfenced)

        seen: set[str] = set()
        ordered: list[str] = []
        for candidate in candidates:
            normalized = candidate.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    def _trimmed_line_candidates(self, content: str) -> list[str]:
        lines = [line.rstrip() for line in content.splitlines() if line.strip()]
        if len(lines) <= 1:
            return []
        candidates: list[str] = []
        for end in range(len(lines) - 1, 0, -1):
            candidate = "\n".join(lines[:end]).strip()
            if candidate:
                candidates.append(candidate)
        full_content = "\n".join(lines).strip()
        if full_content:
            candidates.append(full_content)
        return candidates

    def _truncate_first_statement(self, content: str) -> str | None:
        if not content:
            return None
        in_single_quote = False
        in_double_quote = False
        in_backtick = False
        escaped = False
        for index, char in enumerate(content):
            if escaped:
                escaped = False
                continue
            if char == "\\" and (in_single_quote or in_double_quote):
                escaped = True
                continue
            if in_single_quote:
                if char == "'":
                    in_single_quote = False
                continue
            if in_double_quote:
                if char == '"':
                    in_double_quote = False
                continue
            if in_backtick:
                if char == "`":
                    in_backtick = False
                continue
            if char == "'":
                in_single_quote = True
                continue
            if char == '"':
                in_double_quote = True
                continue
            if char == "`":
                in_backtick = True
                continue
            if char == ";":
                return content[: index + 1].strip()
        return None

    def _strip_reasoning_markup(self, content: str) -> str:
        cleaned = re.sub(r"<think>.*?</think>", " ", content, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<analysis>.*?</analysis>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
        return cleaned.strip()

    def _strip_sql_comments(self, sql: str) -> str:
        without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        without_line_comments = re.sub(r"(--|#)[^\n]*", " ", without_block_comments)
        return without_line_comments

    def _is_single_sql_statement(self, sql: str) -> bool:
        try:
            statements = sqlglot.parse(sql, read=self.sql_dialect.sqlglot_dialect)
        except Exception:
            return False
        return len(statements) == 1
