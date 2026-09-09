"""Tests pour notifications.py: envoi de webhook (Slack/Discord/...) sur
changement d'état — aucun vrai réseau, urllib.request.urlopen monkeypatché."""
import notifications


def test_send_does_nothing_without_configured_webhook(monkeypatch):
    monkeypatch.setattr(notifications.config, "NOTIFY_WEBHOOK_URL", "")
    calls = []
    monkeypatch.setattr(notifications.urllib.request, "urlopen", lambda *a, **k: calls.append(a))

    notifications.send("test")

    assert calls == []


def test_send_posts_json_with_text_and_content_fields(monkeypatch):
    monkeypatch.setattr(notifications.config, "NOTIFY_WEBHOOK_URL", "https://example.invalid/webhook")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = request.data
        captured["timeout"] = timeout

    monkeypatch.setattr(notifications.urllib.request, "urlopen", fake_urlopen)

    notifications.send("Machine X ne répond plus")

    assert captured["url"] == "https://example.invalid/webhook"
    assert captured["method"] == "POST"
    import json
    payload = json.loads(captured["body"])
    assert payload["text"] == "Machine X ne répond plus"
    assert payload["content"] == "Machine X ne répond plus"
    assert captured["timeout"] == notifications.TIMEOUT_SECONDS


def test_send_does_not_raise_when_webhook_unreachable(monkeypatch):
    monkeypatch.setattr(notifications.config, "NOTIFY_WEBHOOK_URL", "https://example.invalid/webhook")

    def boom(request, timeout=None):
        raise OSError("connexion refusée")

    monkeypatch.setattr(notifications.urllib.request, "urlopen", boom)

    notifications.send("test")  # ne doit pas lever
