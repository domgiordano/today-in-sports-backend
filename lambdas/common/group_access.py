"""
Membership as the permission.

Every comment route needs the same two answers — who is calling, and are they
in this group — and getting either wrong is the whole security model of a
private group. So it is written once here rather than in each handler, where
the second one to be written is the one that forgets.
"""

from lambdas.common import groups_dynamo
from lambdas.common.errors import ForbiddenError, NotFoundError, UnauthorizedError


def caller(event, handler):
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    claims = claims.get('claims') or claims
    user_id = claims.get('sub')
    if not user_id:
        raise UnauthorizedError(
            message='sign in to use groups',
            handler=handler, function='caller')
    return user_id


def group_for_member(group_id, user_id, handler):
    """
    The group, if this caller is in it.

    "Not a member" and "no such group" are both answered with 404 rather than
    403, so the endpoint cannot be used to discover which group ids exist. A
    private group nobody can enumerate is the point.
    """
    if not group_id:
        raise NotFoundError(
            message='no such group', handler=handler,
            function='group_for_member')

    group = groups_dynamo.get_group(group_id)
    if not group or user_id not in (group.get('memberIds') or set()):
        raise NotFoundError(
            message='no such group', handler=handler,
            function='group_for_member')
    return group


__all__ = ['caller', 'group_for_member', 'ForbiddenError']
