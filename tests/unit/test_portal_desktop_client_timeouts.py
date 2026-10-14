from portal.desktop_app import DesktopViewModel


def test_desktop_view_model_uses_short_telemetry_client_but_long_message_client():
    calls = []

    class Client:
        def __init__(self, name): self.name = name
        def request(self, command, **payload):
            calls.append((self.name, command))
            if command == "desktop_status":
                return {"state": "ACTIVE", "runtime_id": "r1"}
            if command == "desktop_discover_routes":
                return {"routes": []}
            if command == "desktop_recent_activity":
                return {"items": []}
            if command == "desktop_cognize":
                return {"request_id": payload.get("request_id"),
                        "state": "COMPLETED", "response_text": "ok"}
            raise AssertionError(command)

    vm = DesktopViewModel(Client("telemetry"), messaging_client=Client("cognition"))
    vm.refresh()
    vm.send_message("hello", request_id="bound")
    assert calls == [
        ("telemetry", "desktop_status"),
        ("telemetry", "desktop_discover_routes"),
        ("telemetry", "desktop_recent_activity"),
        ("cognition", "desktop_cognize"),
    ]
