"""
Who is calling a public route.

Every `/play/*` route is `authorization = "NONE"`, deliberately — anonymous
players are first-class and a quiz that demands an account before the first
question is a quiz nobody tries. But API Gateway only populates
`requestContext.authorizer.claims` on routes it authorises, so on these routes
that field is *always* empty, token or no token.

Every play handler read it anyway:

    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    if claims.get('sub'):          # never true here
        identity = claims['sub']
        anonymous = False

which is dead code on a public route. The consequence was not subtle: every
signed-in round was recorded under a device id and flagged anonymous, so
accounts earned no streaks, boards showed a device's name rather than a
person's, and `/play/react` — which requires a subject — refused everybody.

So the token is verified here instead, when one is offered. Anonymous callers
are unaffected: no header, no verification, no identity.
"""

import jwt
from jwt import PyJWKClient

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

_jwk_client = None


def _jwks():
    global _jwk_client
    if _jwk_client is None:
        if not constants.COGNITO_JWKS_URL:
            return None
        _jwk_client = PyJWKClient(constants.COGNITO_JWKS_URL)
    return _jwk_client


def _bearer(event):
    headers = event.get('headers') or {}
    for key in ('Authorization', 'authorization'):
        raw = headers.get(key)
        if raw:
            return raw.replace('Bearer ', '').strip()
    return ''


def subject(event):
    """
    The verified Cognito subject of the caller, or None.

    Returns None rather than raising for a bad token. These routes are public:
    a caller with an expired token is a player whose session lapsed mid-quiz,
    and the right answer is to let them keep playing anonymously rather than to
    fail their round. A forged token fails signature verification and lands in
    the same place, which is the outcome that matters.
    """
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    if claims.get('sub'):
        # An authorised route did the work already.
        return claims['sub']

    token = _bearer(event)
    if not token:
        return None

    client = _jwks()
    if client is None:
        log.warning('a token was presented but COGNITO_JWKS_URL is not set')
        return None

    try:
        verified = jwt.decode(
            token,
            client.get_signing_key_from_jwt(token).key,
            algorithms=['RS256'],
            options={'verify_aud': False},
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "not signed in"
        log.info(f'token presented but not usable: {type(exc).__name__}')
        return None

    return verified.get('sub')


def resolve(event, device_id):
    """
    The identity to record a round under, and whether it is anonymous.

    A verified subject always beats a client-supplied device id — the device id
    is whatever the browser says it is, and the subject is signed.
    """
    sub = subject(event)
    if sub:
        return sub, False
    return (device_id or '').strip(), True
