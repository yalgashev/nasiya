from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address

from fastapi import Request

from app.settings import ClientIpMode, ClientIpNetwork, Settings
from app.telegram.client_ip import ResolvedClientIp


class ClientIpResolutionError(ValueError):
    def __init__(self) -> None:
        super().__init__("Client IP resolution failed")

    def __repr__(self) -> str:
        return "ClientIpResolutionError()"


def resolve_client_ip(request: Request, settings: Settings) -> ResolvedClientIp:
    peer = _parse_peer(request)
    if settings.client_ip_mode is ClientIpMode.DIRECT:
        return ResolvedClientIp(str(peer))

    if not _is_trusted_peer(peer, settings.trusted_proxy_cidrs):
        raise ClientIpResolutionError()

    forwarded_values = request.headers.getlist("x-real-ip")
    if len(forwarded_values) != 1:
        raise ClientIpResolutionError()

    try:
        return ResolvedClientIp(forwarded_values[0])
    except ValueError:
        raise ClientIpResolutionError() from None


def _parse_peer(request: Request) -> IPv4Address | IPv6Address:
    client = request.client
    if client is None:
        raise ClientIpResolutionError()

    try:
        peer = ip_address(client.host)
    except ValueError:
        raise ClientIpResolutionError() from None

    if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
        return peer.ipv4_mapped
    return peer


def _is_trusted_peer(
    peer: IPv4Address | IPv6Address,
    trusted_networks: tuple[ClientIpNetwork, ...],
) -> bool:
    return any(
        peer.version == network.version and peer in network
        for network in trusted_networks
    )
