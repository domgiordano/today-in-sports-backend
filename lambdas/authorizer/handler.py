"""
API Gateway custom authorizer.

Validates the caller's Cognito JWT against this app's own user pool. The pool is
owned by today-in-sports-infrastructure, not shared with the other apps -- this
is a standalone product.

Signature verification uses the pool's JWKS. Admin authorisation is a separate
concern handled in-handler by lambdas/common/admin.py, so this only answers
"is this a valid, unexpired token from our pool".
"""

import json
import urllib.request
from typing import Any, Optional

import jwt
from jwt import PyJWKClient

from lambdas.common import constants
from lambdas.common.errors import LambdaAuthorizerError
from lambdas.common.logger import get_logger

log = get_logger(__file__)

HANDLER = 'authorizer'

_jwk_client = None


def _jwks():
    global _jwk_client
    if _jwk_client is None:
        if not constants.COGNITO_JWKS_URL:
            raise LambdaAuthorizerError('COGNITO_JWKS_URL is not configured')
        _jwk_client = PyJWKClient(constants.COGNITO_JWKS_URL)
    return _jwk_client


def generate_policy(effect: str, resource: str,
                    context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """API Gateway only accepts string/number/bool values in `context`."""
    response: dict[str, Any] = {
        'principalId': constants.PRODUCT,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': effect,
                'Resource': resource,
            }],
        },
    }
    if context:
        response['context'] = context
    return response


def handler(event, context):
    token = (event.get('authorizationToken') or '').replace('Bearer ', '').strip()
    resource = event.get('methodArn', '*')

    if not token:
        log.warning('no token presented')
        raise Exception('Unauthorized')

    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=['RS256'],
            options={'verify_aud': False},
        )
    except Exception as exc:
        log.warning(f'token rejected: {type(exc).__name__}')
        raise Exception('Unauthorized')

    email = claims.get('email') or ''
    if not email:
        log.warning('token carried no email claim')
        raise Exception('Unauthorized')

    return generate_policy('Allow', resource, {
        'email': email,
        'sub': claims.get('sub', ''),
    })
