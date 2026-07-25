from __future__ import annotations

import ipaddress


class ResolvedClientIp:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("Resolved client IP must be a string")
        if not value:
            raise ValueError("Resolved client IP cannot be empty")
        if value != value.strip():
            raise ValueError("Resolved client IP must not contain whitespace")
        if "," in value:
            raise ValueError("Resolved client IP must be a single IP literal")
        if value.startswith("[") or value.endswith("]"):
            raise ValueError("Resolved client IP must not include brackets")

        try:
            parsed_ip = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("Resolved client IP must be a valid IP literal") from exc

        if isinstance(parsed_ip, ipaddress.IPv6Address) and parsed_ip.ipv4_mapped:
            canonical_value = str(parsed_ip.ipv4_mapped)
        else:
            canonical_value = str(parsed_ip)

        self._value = canonical_value

    def as_hmac_input(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "ResolvedClientIp(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-client-ip>"
