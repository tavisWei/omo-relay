from __future__ import annotations

from typing import Any, Callable, Optional


class RuntimeClient:
    def __init__(self, client: Any) -> None:
        self._client = client

    def send_prompt(self, text: str, title: Optional[str] = None) -> Optional[str]:
        session_id = self._start_session_prompt(text, title=title)
        if session_id is not None:
            return session_id

        if hasattr(self._client, "prompt"):
            self._client.prompt(text)
            return None

        session = getattr(self._client, "session", None)
        if session is not None and hasattr(session, "prompt"):
            session.prompt(text)
            return None

        raise RuntimeError("Client does not support prompt API")

    def send_command(
        self, command: str, args: str = "", title: Optional[str] = None
    ) -> Optional[str]:
        text = f"/{command} {args}".strip()
        return self.send_prompt(text, title=title)

    def _start_session_prompt(
        self, text: str, title: Optional[str] = None
    ) -> Optional[str]:
        session_api = getattr(self._client, "session", None)
        if session_api is None or not hasattr(session_api, "create"):
            return None

        session_id = self._create_session_id(session_api, title)
        if session_id is None or not hasattr(session_api, "prompt"):
            return session_id

        self._call_prompt_with_session(session_api.prompt, session_id, text)
        return session_id

    def _create_session_id(
        self, session_api: Any, title: Optional[str]
    ) -> Optional[str]:
        create = session_api.create
        attempts: list[Callable[[], Any]] = []
        if title is not None:
            attempts.extend(
                [
                    lambda: create(title=title),
                    lambda: create({"title": title}),
                    lambda: create({"body": {"title": title}}),
                ]
            )
        attempts.append(lambda: create())

        for attempt in attempts:
            try:
                created = attempt()
            except TypeError:
                continue
            session_id = self._extract_session_id(created)
            if session_id is not None:
                return session_id
        return None

    def _call_prompt_with_session(
        self, prompt: Callable[..., Any], session_id: str, text: str
    ) -> None:
        attempts: list[Callable[[], Any]] = [
            lambda: prompt(session_id, text),
            lambda: prompt(text, session_id=session_id),
            lambda: prompt({"session_id": session_id, "text": text}),
            lambda: prompt(
                {
                    "path": {"id": session_id},
                    "body": {"parts": [{"type": "text", "text": text}]},
                }
            ),
        ]
        for attempt in attempts:
            try:
                attempt()
                return
            except TypeError:
                continue
        raise RuntimeError("Session prompt API shape is unsupported")

    def _extract_session_id(self, created: Any) -> Optional[str]:
        if created is None:
            return None
        if isinstance(created, str):
            return created
        if isinstance(created, dict):
            if "id" in created:
                return str(created["id"])
            if "session_id" in created:
                return str(created["session_id"])
            path = created.get("path")
            if isinstance(path, dict) and "id" in path:
                return str(path["id"])
            body = created.get("body")
            if isinstance(body, dict) and "id" in body:
                return str(body["id"])
        for attr in ("id", "session_id"):
            value = getattr(created, attr, None)
            if value is not None:
                return str(value)
        path = getattr(created, "path", None)
        if path is not None and getattr(path, "id", None) is not None:
            return str(path.id)
        body = getattr(created, "body", None)
        if body is not None and getattr(body, "id", None) is not None:
            return str(body.id)
        return None
