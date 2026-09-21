"""
Role-based permissions for privileged governance actions (Phase 5A).

Roles are Django auth Groups - no new user model, no parallel identity store.
A user is a SUPERVISOR if they are in the SUPERVISOR group (or is_superuser);
everyone else authenticating is treated as an OFFICER.

Deliberately narrow: only actions that alter governance or financial state opt
in to these. Read endpoints stay open so the existing dashboard keeps working,
and AR ingestion stays open so the frozen iOS app keeps working.
"""

from rest_framework.permissions import BasePermission

SUPERVISOR_GROUP = "SUPERVISOR"
OFFICER_GROUP = "OFFICER"


def is_supervisor(user):
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.groups.filter(name=SUPERVISOR_GROUP).exists()


def is_officer(user):
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=OFFICER_GROUP).exists()


class IsSupervisor(BasePermission):
    """Approve / reject / override. An officer or an anonymous caller is refused.

    This closes the hole where any unauthenticated caller could approve a block
    and name themselves as the approver via a free-text 'actor' field.
    """

    message = (
        "Supervisor authentication required. This action changes governance state "
        "and is recorded against your authenticated identity."
    )

    def has_permission(self, request, view):
        return is_supervisor(request.user)
